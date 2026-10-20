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


def project_permission_fact(
    *,
    verdict: str,
    approval_status: str | None,
    execution_status: str | None,
    risk_class: str = "",
) -> dict[str, str]:
    """批准、拒绝和执行终态共用这一条投影。批准本身不是执行成功。"""

    if execution_status == "succeeded":
        status = "succeeded"
    elif execution_status == "failed":
        status = "failed"
    elif execution_status in {"unknown", "running", "reserved"}:
        status = "unknown"
    elif verdict == "deny" or approval_status in {"rejected", "cancelled", "expired", "interrupted"}:
        status = "denied"
    elif approval_status == "approved" or verdict == "allow":
        status = "approved"
    else:
        status = "pending"
    return {"status": status, "riskClass": risk_class or "unknown"}


def _prefer_permission_fact(new: dict, old: dict) -> bool:
    """同一调用保留执行结果；等待中的复核优先于原来的 deny。"""

    status_rank = {"succeeded": 5, "failed": 4, "unknown": 3, "approved": 2, "pending": 2, "denied": 1}

    def rank(item: dict) -> tuple[int, int]:
        review = 1 if item.get("reasonCode") == "human_review_of_model_deny" else 0
        return (status_rank.get(str(item.get("status")), 0), review)

    return rank(new) > rank(old)


class _BatchRejected(Exception):
    """批量审批失败时带着逐条原因回滚事务。"""

    def __init__(self, reasons: list[dict[str, str]]) -> None:
        self.reasons = reasons
        super().__init__("batch rejected")


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
                """SELECT d.snapshot_json, d.decision_json, a.status AS approval_status, e.status AS execution_status
                   FROM permission_decisions d
                   LEFT JOIN permission_approvals a ON a.decision_id=d.id
                   LEFT JOIN permission_executions e ON e.decision_id=d.id
                   WHERE d.tenant_id=? AND d.run_id=? ORDER BY d.created_at ASC""",
                (current_tenant().tenant_id, run_id),
            ).fetchall()
        grouped: dict[str, dict] = {}
        for row in rows:
            snapshot = json.loads(row["snapshot_json"] or "{}")
            decision = json.loads(row["decision_json"] or "{}")
            call_id = str(snapshot.get("callId") or decision.get("id") or "")
            try:
                from src.security.authorization.models import ActionSnapshot
                from src.security.authorization.risk import classify_risk
                risk_class = classify_risk(ActionSnapshot.model_validate(snapshot)).risk_class
            except Exception:
                risk_class = "unknown"
            projected = project_permission_fact(
                verdict=str(decision.get("verdict") or ""),
                approval_status=row["approval_status"],
                execution_status=row["execution_status"],
                risk_class=risk_class,
            )
            item = {
                "callId": call_id,
                "toolName": snapshot.get("toolName", ""),
                "targets": [target.get("target", "") for target in snapshot.get("targets", [])],
                "status": projected["status"],
                "riskClass": projected["riskClass"],
                "reasonCode": str(decision.get("reasonCode") or ""),
            }
            current = grouped.get(call_id)
            if current is None or _prefer_permission_fact(item, current):
                grouped[call_id] = item
        items = list(grouped.values())
        counts = {status: sum(1 for item in items if item["status"] == status)
                  for status in ("succeeded", "failed", "denied", "unknown", "approved", "pending")}
        mixed = counts["succeeded"] and any(counts[key] for key in ("failed", "denied", "unknown"))
        return {"outcome": "partial" if mixed else "complete", "counts": counts, "items": items}

    def _run_error(self, conn, owner: RunAuthorization) -> str | None:
        row = conn.execute("SELECT * FROM chat_runs WHERE id=?", (owner.run_id,)).fetchone()
        if row is None:
            return "授权 Run 不存在"
        if (row["workspace_id"], row["project_id"], row["session_id"]) != (
            owner.workspace_id, owner.project_id, owner.session_id,
        ):
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
            rows = conn.execute(
                "SELECT id,decision_json,invalidated_at,expires_at FROM permission_decisions "
                "WHERE action_fingerprint=? AND tenant_id=? ORDER BY created_at ASC",
                (action.fingerprint, action.authorization.tenant_id),
            ).fetchall()
            if any(row["invalidated_at"] for row in rows):
                raise ValueError("授权决定已失效或过期")
            now = _now()
            for row in rows:
                self._expire(conn, row["id"], now)
            chosen = self._active_decision(conn, rows)
            if chosen is not None and chosen.verdict != "deny":
                matched = next(
                    row for row in rows
                    if AuthorizationDecision.model_validate_json(row["decision_json"]).id == chosen.id
                )
                if not matched["expires_at"] or matched["expires_at"] <= now:
                    raise ValueError("授权决定已失效或过期")
        return chosen

    @staticmethod
    def _active_decision(conn, rows) -> AuthorizationDecision | None:
        """复核还在等待或已批准时，调用方看到复核；维持拒绝后才回到原 deny。"""

        if not rows:
            return None
        decisions = [AuthorizationDecision.model_validate_json(row["decision_json"]) for row in rows]
        for decision in decisions:
            if decision.reason_code != "human_review_of_model_deny":
                continue
            approval = conn.execute(
                "SELECT status FROM permission_approvals WHERE decision_id=?", (decision.id,),
            ).fetchone()
            if approval is not None and approval["status"] in {"pending", "approved"}:
                return decision
        for decision in decisions:
            if decision.reason_code != "human_review_of_model_deny":
                return decision
        return decisions[0]

    def get_decision(self, decision_id: str, owner: RunAuthorization) -> AuthorizationDecision | None:
        """按归属读取一条决定，不改变它。"""

        self._check_owner(owner)
        with _transaction() as conn:
            try:
                row, _stored = self._owned_decision(conn, decision_id, owner)
            except ValueError:
                return None
        return AuthorizationDecision.model_validate_json(row["decision_json"])

    def trace_facts(self, action: ActionSnapshot) -> dict:
        """原模型 deny、复核 decision 和 execution 分开返回。"""

        self._check_owner(action.authorization)
        with _transaction() as conn:
            rows = conn.execute(
                "SELECT decision_json FROM permission_decisions WHERE action_fingerprint=? AND tenant_id=? ORDER BY created_at ASC",
                (action.fingerprint, action.authorization.tenant_id),
            ).fetchall()
        deny = review = None
        for row in rows:
            decision = AuthorizationDecision.model_validate_json(row["decision_json"])
            if decision.reason_code == "human_review_of_model_deny":
                review = decision
            elif decision.source == "model" and decision.verdict == "deny":
                deny = decision
        execution = self.get_execution(review.id, action.authorization) if review is not None else None
        approval = self.get_approval(review.id, action.authorization) if review is not None else None

        def brief(decision: AuthorizationDecision | None) -> dict | None:
            if decision is None:
                return None
            return {"id": decision.id, "verdict": decision.verdict, "source": decision.source,
                    "reasonCode": decision.reason_code}

        return {
            "modelDeny": brief(deny),
            "review": None if review is None else {**brief(review), "approvalStatus": approval.status if approval else ""},
            "execution": None if execution is None else {"id": execution.id, "status": execution.status},
        }

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
            row = conn.execute(
                "SELECT id,decision_json,invalidated_at,expires_at FROM permission_decisions "
                "WHERE action_fingerprint=? AND call_id=?",
                (action.fingerprint, action.call_id),
            ).fetchone()
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

    def save_model_deny_with_review(self, action: ActionSnapshot, decision: AuthorizationDecision) -> AuthorizationDecision:
        """同一事务保存原模型 deny，并挂上同一快照的人工复核。"""

        owner = action.authorization
        self._check_owner(owner)
        if action.fingerprint != decision.action_fingerprint:
            raise ValueError("决定不属于此动作")
        if decision.source == "boundary" or decision.reason_code in {"hard_boundary", "human_only"}:
            raise ValueError("硬边界拒绝不能人工覆盖")
        if decision.verdict != "deny" or decision.source != "model":
            raise ValueError("只有模型拒绝可以申请人工复核")
        if owner.surface == "job":
            raise ValueError("无人值守运行不创建复核")
        database.ensure_db()
        now = _now()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=900)).isoformat()
        review = AuthorizationDecision(
            id=f"pd_{uuid4().hex}",
            actionFingerprint=action.fingerprint,
            verdict="needs_human",
            source="manual",
            reasonCode="human_review_of_model_deny",
            reason=f"模型拒绝了这一次：{decision.reason}"[:500],
            relatedDecisionId=decision.id,
        )
        with _transaction() as conn:
            error = self._run_error(conn, owner)
            if error:
                raise ValueError("运行已终止，不能复活已结束的调用")
            self._insert_decision(conn, action, decision, now, expires, related="")
            self._insert_decision(conn, action, review, now, expires, related=decision.id, call_id=f"{action.call_id}#review")
            conn.execute("""INSERT INTO permission_approvals
                            (id,decision_id,status,approved_by,expires_at,created_at,updated_at)
                            VALUES (?,?,?,?,?,?,?)""",
                         (f"pa_{uuid4().hex}", review.id, "pending", "", expires, now, now))
        return review

    @staticmethod
    def _insert_decision(conn, action: ActionSnapshot, decision: AuthorizationDecision, now: str, expires: str,
                         *, related: str, call_id: str | None = None) -> None:
        owner = action.authorization
        conn.execute(
            """INSERT INTO permission_decisions
               (id,tenant_id,workspace_id,project_id,session_id,run_id,actor_id,call_id,
                action_fingerprint,snapshot_json,decision_json,approval_mode,policy_version,
                created_at,updated_at,expires_at,related_decision_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (decision.id, owner.tenant_id, owner.workspace_id, owner.project_id, owner.session_id,
             owner.run_id, owner.actor_id, call_id or action.call_id, action.fingerprint,
             action.model_dump_json(by_alias=True), decision.model_dump_json(by_alias=True),
             owner.approval_mode, owner.policy_version, now, now, expires, related),
        )

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

    def answer_batch(
        self,
        items: list[tuple[str, RunAuthorization]],
        *,
        approved: bool,
        approved_by: str,
    ) -> list[dict[str, str]]:
        """整批在一个写事务里校验。任一项失败则全部回滚，并返回逐条原因。"""

        if not approved_by:
            raise ValueError("缺少审批身份")
        for _decision_id, owner in items:
            self._check_owner(owner)
        now = _now()
        status = "approved" if approved else "rejected"
        reasons: list[dict[str, str]] = []
        try:
            with _transaction() as conn:
                staged: list[str] = []
                for decision_id, owner in items:
                    reason = self._batch_item_reason(conn, decision_id, owner, now, status)
                    reasons.append({"decisionId": decision_id, "reason": reason})
                    if not reason:
                        staged.append(decision_id)
                if any(item["reason"] not in {"", "already_answered"} for item in reasons):
                    raise _BatchRejected(reasons)
                for decision_id in staged:
                    conn.execute("""UPDATE permission_approvals SET status=?,approved_by=?,updated_at=?
                                    WHERE decision_id=? AND status='pending' AND expires_at>?""",
                                 (status, approved_by, now, decision_id, now))
        except _BatchRejected as exc:
            return exc.reasons
        return reasons

    def _batch_item_reason(self, conn, decision_id: str, owner: RunAuthorization, now: str, status: str) -> str:
        try:
            row, _stored = self._owned_decision(conn, decision_id, owner)
        except ValueError as exc:
            return str(exc)
        self._expire(conn, decision_id, now)
        row = conn.execute("SELECT * FROM permission_decisions WHERE id=?", (decision_id,)).fetchone()
        if row["invalidated_at"] or not row["expires_at"] or row["expires_at"] <= now:
            return "已过期"
        if self._run_error(conn, owner):
            return "运行已失效"
        approval = conn.execute("SELECT * FROM permission_approvals WHERE decision_id=?", (decision_id,)).fetchone()
        if approval is None:
            return "没有待批记录"
        if approval["status"] == status:
            return "already_answered"
        if approval["status"] != "pending":
            return f"状态已是 {approval['status']}"
        return ""

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
            if run is None or (run["workspace_id"], run["project_id"], run["session_id"]) != (
                owner.workspace_id, owner.project_id, owner.session_id,
            ):
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
