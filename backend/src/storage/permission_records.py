"""租户授权记录与条件事务；模型/人工等待不得进入存储事务。"""

from __future__ import annotations

from contextlib import closing, contextmanager
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.security.authorization.models import (
    ActionSnapshot, ApprovalRecord, AuthorizationDecision, ExecutionRecord, RunAuthorization,
)
from src.storage import database
from src.tenancy.context import current_tenant


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _transaction():
    """短事务必须关闭连接；异常和提前返回也完成回滚/提交。"""

    database.ensure_db()
    with database._lock, closing(database._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        yield conn


class PermissionRecordStore:
    """每次调用解析当前租户，写入前核对服务端 Run/actor 归属。"""

    def _check_owner(self, owner: RunAuthorization) -> None:
        if current_tenant().tenant_id != owner.tenant_id:
            raise ValueError("授权租户不匹配")

    def summarize_run(self, run_id: str) -> dict:
        """按当前租户汇总一次 Run 的真实执行事实，供 completion/Trace 共用。"""

        self._check_owner(RunAuthorization(
            tenantId=current_tenant().tenant_id, workspaceId="summary", projectId="summary",
            sessionId="summary", runId=run_id, actorId="summary", capabilityVersion="summary",
        ))
        with _transaction() as conn:
            rows = conn.execute(
                """SELECT d.snapshot_json, e.status AS execution_status
                   FROM permission_decisions d
                   LEFT JOIN permission_executions e ON e.decision_id=d.id
                   WHERE d.tenant_id=? AND d.run_id=? ORDER BY d.created_at ASC""",
                (current_tenant().tenant_id, run_id),
            ).fetchall()
        items = []
        for row in rows:
            snapshot = json.loads(row["snapshot_json"] or "{}")
            items.append({
                "callId": snapshot.get("callId", ""),
                "toolName": snapshot.get("toolName", ""),
                "targets": [item.get("target", "") for item in snapshot.get("targets", [])],
                "status": row["execution_status"] or "denied",
            })
        counts = {status: sum(1 for item in items if item["status"] == status)
                  for status in ("succeeded", "failed", "denied", "unknown")}
        return {"outcome": "partial" if counts["succeeded"] and any(counts[key] for key in ("failed", "denied", "unknown")) else "complete",
                "counts": counts, "items": items}

    def _run_error(self, conn, owner: RunAuthorization) -> str | None:
        row = conn.execute("SELECT * FROM chat_runs WHERE id=?", (owner.run_id,)).fetchone()
        if row is None:
            return "授权 Run 不存在"
        if row["session_id"] != owner.session_id:
            return "授权 Run 归属不匹配"
        frozen = json.loads(row["authorization_json"] or "{}")
        if frozen and (frozen.get("tenantId") != owner.tenant_id or
                       frozen.get("approvalMode") != owner.approval_mode or
                       frozen.get("reviewerConfigVersion", "") != owner.reviewer_config_version):
            return "授权模式或审核版本不属于该 Run"
        if row["authorization_invalidated_at"] or row["status"] not in {
            "running", "waiting_user", "waiting_permission", "waiting_external",
        }:
            return "授权 Run 已终止或失效"
        return None

    def run_error(self, owner: RunAuthorization) -> str | None:
        """Run 事实先于 decision 存在，挡住取消后的迟到审核。"""

        self._check_owner(owner)
        with _transaction() as conn:
            return self._run_error(conn, owner)

    def find(self, action: ActionSnapshot) -> AuthorizationDecision | None:
        self._check_owner(action.authorization)
        with _transaction() as conn:
            error = self._run_error(conn, action.authorization)
            if error:
                raise ValueError(error)
            row = conn.execute(
                "SELECT decision_json,invalidated_at,expires_at FROM permission_decisions "
                "WHERE action_fingerprint=? AND tenant_id=?",
                (action.fingerprint, action.authorization.tenant_id),
            ).fetchone()
            if row and (row["invalidated_at"] or not row["expires_at"] or row["expires_at"] <= _now()):
                raise ValueError("授权决定已失效或过期")
        return AuthorizationDecision.model_validate_json(row[0]) if row else None

    @staticmethod
    def _expire(conn, decision_id: str, now: str) -> None:
        conn.execute("""UPDATE permission_approvals SET status='expired',updated_at=?
                        WHERE decision_id=? AND status IN ('pending','approved') AND expires_at<=?""",
                     (now, decision_id, now))

    def save(self, action: ActionSnapshot, decision: AuthorizationDecision, *, ttl_seconds: int = 900) -> AuthorizationDecision:
        """相同行动唯一保存；并发初次判断只有首个结果成为权威。"""

        owner = action.authorization
        self._check_owner(owner)
        if action.fingerprint != decision.action_fingerprint:
            raise ValueError("决定不属于此动作")
        database.ensure_db()
        now = _now()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
        with _transaction() as conn:
            error = self._run_error(conn, owner)
            if error:
                raise ValueError(error)
            conn.execute(
                """INSERT INTO permission_decisions
                   (id,tenant_id,workspace_id,project_id,session_id,run_id,actor_id,call_id,
                    action_fingerprint,snapshot_json,decision_json,approval_mode,policy_version,created_at,updated_at,expires_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(tenant_id,run_id,actor_id,call_id,action_fingerprint) DO NOTHING""",
                (decision.id, owner.tenant_id, owner.workspace_id, owner.project_id, owner.session_id,
                 owner.run_id, owner.actor_id, action.call_id, action.fingerprint,
                 action.model_dump_json(by_alias=True), decision.model_dump_json(by_alias=True),
                 owner.approval_mode, owner.policy_version, now, now, expires),
            )
            row = conn.execute("SELECT id,decision_json,invalidated_at,expires_at FROM permission_decisions WHERE action_fingerprint=?",
                               (action.fingerprint,)).fetchone()
            if row["id"] != decision.id and (row["invalidated_at"] or not row["expires_at"] or row["expires_at"] <= now):
                raise ValueError("已保存授权失效或过期")
            saved = AuthorizationDecision.model_validate_json(row["decision_json"])
            if saved.verdict == "needs_human":
                conn.execute("""INSERT INTO permission_approvals
                                (id,decision_id,status,approved_by,expires_at,created_at,updated_at)
                                VALUES (?,?,?,?,?,?,?)
                                ON CONFLICT(decision_id) DO NOTHING""",
                             (f"pa_{uuid4().hex}", row["id"], "pending", "", expires, now, now))
        return saved

    def _owned_decision(self, conn, decision_id: str, owner: RunAuthorization):
        row = conn.execute("SELECT * FROM permission_decisions WHERE id=?", (decision_id,)).fetchone()
        if row is None:
            raise ValueError("授权决定不存在")
        stored = ActionSnapshot.model_validate_json(row["snapshot_json"])
        if stored.authorization != owner:
            raise ValueError("授权归属或策略版本不匹配")
        return row, stored

    def get_approval(self, decision_id: str, owner: RunAuthorization) -> ApprovalRecord | None:
        self._check_owner(owner)
        database.ensure_db()
        with _transaction() as conn:
            self._owned_decision(conn, decision_id, owner)
            self._expire(conn, decision_id, _now())
            row = conn.execute("SELECT * FROM permission_approvals WHERE decision_id=?", (decision_id,)).fetchone()
        return ApprovalRecord(id=row["id"], decisionId=row["decision_id"], status=row["status"],
                              approvedBy=row["approved_by"], expiresAt=row["expires_at"]) if row else None

    def request_human_review(self, decision_id: str, session_id: str) -> tuple[ActionSnapshot, AuthorizationDecision]:
        """模型拒绝另开一次人工尝试。硬边界和已终止 Run 不产生许可。"""

        database.ensure_db()
        with _transaction() as conn:
            row = conn.execute("SELECT * FROM permission_decisions WHERE id=?", (decision_id,)).fetchone()
            if row is None:
                raise ValueError("授权决定不存在")
            stored = ActionSnapshot.model_validate_json(row["snapshot_json"])
            owner = stored.authorization
            self._check_owner(owner)
            if owner.session_id != session_id:
                raise ValueError("授权归属或策略版本不匹配")
            now = _now()
            if row["invalidated_at"] or not row["expires_at"] or row["expires_at"] <= now:
                raise ValueError("授权决定已失效或过期")
            error = self._run_error(conn, owner)
            if error:
                raise ValueError("运行已终止，不能复活已结束的调用")
            decision = AuthorizationDecision.model_validate_json(row["decision_json"])
            if decision.source == "boundary" or decision.reason_code == "hard_boundary":
                raise ValueError("硬边界拒绝不能人工覆盖")
            if decision.verdict != "deny" or decision.source != "model":
                raise ValueError("只有模型拒绝可以申请人工复核")
        attempt = stored.model_copy(update={"call_id": f"review_{uuid4().hex}"})
        review = AuthorizationDecision(
            id=f"pd_{uuid4().hex}",
            actionFingerprint=attempt.fingerprint,
            verdict="needs_human",
            source="manual",
            reasonCode="human_review",
            reason=f"人工复核模型拒绝：{decision.reason}"[:500],
        )
        return attempt, self.save(attempt, review)

    def answer(self, decision_id: str, owner: RunAuthorization, *, approved: bool, approved_by: str) -> bool:
        """批准者来自认证上下文；重复/迟到/跨归属命令不改变记录。"""

        self._check_owner(owner)
        if not approved_by:
            raise ValueError("缺少审批身份")
        database.ensure_db()
        now = _now()
        with _transaction() as conn:
            row, _ = self._owned_decision(conn, decision_id, owner)
            self._expire(conn, decision_id, now)
            if (row["invalidated_at"] or not row["expires_at"] or row["expires_at"] <= now
                    or self._run_error(conn, owner)):
                return False
            count = conn.execute("""UPDATE permission_approvals SET status=?,approved_by=?,updated_at=?
                                    WHERE decision_id=? AND status='pending' AND expires_at>?""",
                                 ("approved" if approved else "rejected", approved_by, now, decision_id, now)).rowcount
        return count == 1

    def reserve(self, action: ActionSnapshot, decision_id: str) -> ExecutionRecord:
        """条件批准与一次占位在同一写事务核对，唯一约束防止重复执行。"""

        self._check_owner(action.authorization)
        database.ensure_db()
        now, execution_id = _now(), f"pe_{uuid4().hex}"
        with _transaction() as conn:
            row, stored = self._owned_decision(conn, decision_id, action.authorization)
            if (row["invalidated_at"] or not row["expires_at"] or row["expires_at"] <= now
                    or self._run_error(conn, action.authorization)
                    or stored.fingerprint != action.fingerprint):
                raise ValueError("执行行动已变化")
            decision = AuthorizationDecision.model_validate_json(row["decision_json"])
            approval = conn.execute("SELECT * FROM permission_approvals WHERE decision_id=?", (decision_id,)).fetchone()
            if decision.verdict == "deny" or (decision.verdict == "needs_human" and (
                approval is None or approval["status"] != "approved" or approval["expires_at"] <= now
            )):
                raise ValueError("行动未批准或已过期")
            count = conn.execute("""INSERT INTO permission_executions
                                    (id,decision_id,status,result_ref,created_at,updated_at)
                                    VALUES (?,?,?,?,?,?)
                                    ON CONFLICT(decision_id) DO NOTHING""",
                                 (execution_id, decision_id, "reserved", "", now, now)).rowcount
            if count != 1:
                raise ValueError("此行动许可已被消费")
        return ExecutionRecord(id=execution_id, decisionId=decision_id, status="reserved")

    def get_execution(self, decision_id: str, owner: RunAuthorization) -> ExecutionRecord | None:
        """只按完整归属读取执行事实。"""

        self._check_owner(owner)
        with _transaction() as conn:
            self._owned_decision(conn, decision_id, owner)
            row = conn.execute("SELECT * FROM permission_executions WHERE decision_id=?", (decision_id,)).fetchone()
        return ExecutionRecord(id=row["id"], decisionId=row["decision_id"], status=row["status"]) if row else None

    def start(self, action: ActionSnapshot, decision_id: str) -> ExecutionRecord:
        """实际分发前再核对归属、批准有效期和取消，只允许 reserved→running。"""

        self._check_owner(action.authorization)
        now = _now()
        with _transaction() as conn:
            row, stored = self._owned_decision(conn, decision_id, action.authorization)
            self._expire(conn, decision_id, now)
            approval = conn.execute("SELECT * FROM permission_approvals WHERE decision_id=?", (decision_id,)).fetchone()
            if (row["invalidated_at"] or not row["expires_at"] or row["expires_at"] <= now
                    or self._run_error(conn, action.authorization)
                    or stored.fingerprint != action.fingerprint
                    or (approval is not None and approval["status"] != "approved")):
                raise ValueError("待执行许可已失效")
            changed = conn.execute("""UPDATE permission_executions SET status='running',updated_at=?
                                      WHERE decision_id=? AND status='reserved'""", (now, decision_id)).rowcount
            if changed != 1:
                raise ValueError("许可尚未占位或已开始执行")
            execution = conn.execute("SELECT id FROM permission_executions WHERE decision_id=?", (decision_id,)).fetchone()
        return ExecutionRecord(id=execution["id"], decisionId=decision_id, status="running")

    def finish(self, decision_id: str, owner: RunAuthorization, *, status: str, result_ref: str = "") -> bool:
        """成功/失败只收口 running；失去现场的 reserved 可保守标记 unknown。"""

        if status not in {"succeeded", "failed", "unknown"}:
            raise ValueError("无效执行终态")
        self._check_owner(owner)
        with _transaction() as conn:
            self._owned_decision(conn, decision_id, owner)
            prior = ("reserved", "running") if status == "unknown" else ("running",)
            placeholders = ",".join("?" for _ in prior)
            count = conn.execute(f"""UPDATE permission_executions SET status=?,result_ref=?,updated_at=?
                                     WHERE decision_id=? AND status IN ({placeholders})""",
                                 (status, result_ref, _now(), decision_id, *prior)).rowcount
        return count == 1

    def invalidate_run(self, owner: RunAuthorization, *, interrupted: bool = False) -> None:
        """取消失去现场的批准；可能开始的执行保留 unknown，绝不补执行。"""

        self._check_owner(owner)
        database.ensure_db()
        now = _now()
        with _transaction() as conn:
            run = conn.execute("SELECT * FROM chat_runs WHERE id=?", (owner.run_id,)).fetchone()
            if run is None or run["session_id"] != owner.session_id:
                raise ValueError("取消 Run 归属不匹配")
            conn.execute("""UPDATE chat_runs SET authorization_invalidated_at=?,updated_at=?
                            WHERE id=? AND authorization_invalidated_at=''""", (now, now, owner.run_id))
            query = "SELECT id FROM permission_decisions WHERE tenant_id=? AND run_id=?"
            params = (owner.tenant_id, owner.run_id)
            conn.execute("""UPDATE permission_decisions SET invalidated_at=?,updated_at=?
                            WHERE tenant_id=? AND run_id=?""", (now, now, *params))
            conn.execute(f"""UPDATE permission_approvals SET status=?,updated_at=?
                             WHERE decision_id IN ({query}) AND status IN ('pending','approved')""",
                         ("interrupted" if interrupted else "cancelled", now, *params))
            conn.execute(f"""UPDATE permission_executions SET status='unknown',updated_at=?
                             WHERE decision_id IN ({query}) AND status IN ('reserved','running')""",
                         (now, *params))

    def invalidate_run_record(self, run_id: str, *, workspace_id: str, project_id: str,
                              session_id: str, interrupted: bool = False) -> None:
        """收口属于当前租户的整个 Run，无需恢复已丢失的 actor 现场。"""
        tenant = current_tenant()
        owner = RunAuthorization(tenantId=tenant.tenant_id, workspaceId=workspace_id, projectId=project_id,
                                 sessionId=session_id, runId=run_id, actorId="run-lifecycle", capabilityVersion="lifecycle")
        self.invalidate_run(owner, interrupted=interrupted)
