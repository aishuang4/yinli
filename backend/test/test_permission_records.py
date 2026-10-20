"""权限事实的租户隔离、原地升级和一次许可事务回归。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from src.runtime.authorization import ActionSnapshot, AuthorizationDecision, RunAuthorization
from src.storage import database
from src.storage.permission_records import PermissionRecordStore
from src.tenancy.context import TenantContext, use_tenant


def action_for(tenant="local", **updates):
    if database.get_chat_run("r") is None:
        database.upsert_chat_run({"id": "r", "workspaceId": "w", "projectId": "p", "sessionId": "s"})
    owner = RunAuthorization(tenantId=tenant, workspaceId="w", projectId="p", sessionId="s",
                             runId="r", actorId="a", capabilityVersion="1", **updates)
    return ActionSnapshot(authorization=owner, callId="c", toolName="Fake", toolVersion="1",
                          normalizedInputJson='{"value":1}')


def save_pending(store, action, **kwargs):
    decision = AuthorizationDecision(id=f"pd_{uuid4().hex}", actionFingerprint=action.fingerprint,
        verdict="needs_human", source="manual", reasonCode="manual", reason="请批准这一次")
    return store.save(action, decision, **kwargs)


def test_summarize_run_reports_partial_execution_facts():
    store = PermissionRecordStore()
    successful = action_for(approvalMode="full_access")
    allowed = AuthorizationDecision(id=f"pd_{uuid4().hex}", actionFingerprint=successful.fingerprint,
        verdict="allow", source="full_access", reasonCode="full_access", reason="开放")
    store.save(successful, allowed)
    store.reserve(successful, allowed.id)
    store.start(successful, allowed.id)
    assert store.finish(allowed.id, successful.authorization, status="succeeded")

    denied = successful.model_copy(update={"call_id": "denied"})
    blocked = AuthorizationDecision(id=f"pd_{uuid4().hex}", actionFingerprint=denied.fingerprint,
        verdict="deny", source="boundary", reasonCode="hard_boundary", reason="超出范围")
    store.save(denied, blocked)
    summary = store.summarize_run("r")
    assert summary["outcome"] == "partial"
    assert summary["counts"]["succeeded"] == 1
    assert summary["counts"]["denied"] == 1


def test_old_db_upgrades_and_snapshot_roundtrip():
    path = database.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    from src.storage.permission_schema import PERMISSION_SCHEMA_SQL
    legacy = PERMISSION_SCHEMA_SQL.replace("    invalidated_at TEXT NOT NULL DEFAULT '',\n", "").replace(
        "    expires_at TEXT NOT NULL DEFAULT '',\n", "",
    )
    with sqlite3.connect(path) as conn:
        conn.executescript(legacy)
        conn.execute("CREATE TABLE legacy_marker (value TEXT)")
        conn.execute("INSERT INTO legacy_marker VALUES ('preserve')")
    store, action = PermissionRecordStore(), action_for()
    decision = save_pending(store, action)
    assert store.find(action) == decision
    assert store.get_approval(decision.id, action.authorization).status == "pending"
    duplicate = save_pending(store, action)
    assert duplicate.id == decision.id
    with database._connect() as conn:
        assert conn.execute("SELECT value FROM legacy_marker").fetchone()[0] == "preserve"
        assert conn.execute("SELECT COUNT(*) FROM permission_decisions").fetchone()[0] == 1
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(permission_decisions)")}
        assert {"invalidated_at", "expires_at"} <= columns


def test_repeated_approval_and_concurrent_resume_get_one_permit(local_tenant):
    store, action = PermissionRecordStore(), action_for()
    decision = save_pending(store, action)
    with pytest.raises(ValueError):
        store.reserve(action, decision.id)
    assert store.answer(decision.id, action.authorization, approved=True, approved_by="human")
    assert not store.answer(decision.id, action.authorization, approved=False, approved_by="human")

    def reserve(_):
        with use_tenant(local_tenant):
            try:
                return store.reserve(action, decision.id).id
            except ValueError:
                return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reserve, range(8)))
    assert len([r for r in results if r is not None]) == 1
    assert store.finish(decision.id, action.authorization, status="unknown")
    assert not store.finish(decision.id, action.authorization, status="succeeded")
    with pytest.raises(ValueError):
        store.reserve(action, decision.id)


@pytest.mark.parametrize("field,value", [("workspaceId", "other"), ("projectId", "other"),
    ("sessionId", "other"), ("runId", "other"), ("actorId", "other"), ("capabilityVersion", "2")])
def test_foreign_owner_commands_rejected(field, value):
    store, action = PermissionRecordStore(), action_for()
    decision = save_pending(store, action)
    data = action.authorization.model_dump(by_alias=True)
    data[field] = value
    foreign = RunAuthorization.model_validate(data)
    with pytest.raises(ValueError):
        store.answer(decision.id, foreign, approved=True, approved_by="human")
    assert store.get_approval(decision.id, action.authorization).status == "pending"


def test_tenant_db_isolation_and_no_cross_tenant_snapshot():
    store, local = PermissionRecordStore(), action_for()
    local_decision = save_pending(store, local)
    tenant = TenantContext.hosted("usr_" + "a" * 32)
    with use_tenant(tenant):
        foreign = action_for(tenant.tenant_id)
        assert store.find(foreign) is None
        with pytest.raises(ValueError):
            store.find(local)
        other = save_pending(store, foreign)
        assert other.id != local_decision.id
        with pytest.raises(ValueError):
            store.get_approval(local_decision.id, foreign.authorization)
    assert store.find(local) == local_decision


def test_expired_cancelled_and_changed_action_cannot_execute():
    store, action = PermissionRecordStore(), action_for()
    decision = save_pending(store, action, ttl_seconds=-1)
    assert not store.answer(decision.id, action.authorization, approved=True, approved_by="human")
    with pytest.raises(ValueError):
        store.reserve(action, decision.id)
    other = action.model_copy(update={"call_id": "other"})
    pending = save_pending(store, other)
    assert store.answer(pending.id, other.authorization, approved=True, approved_by="human")
    changed = other.model_copy(update={"normalized_input_json": '{"value":2}'})
    with pytest.raises(ValueError):
        store.reserve(changed, pending.id)
    store.invalidate_run(action.authorization)
    assert not store.answer(pending.id, other.authorization, approved=True, approved_by="human")
    with pytest.raises(ValueError):
        store.reserve(other, pending.id)


def test_foreign_key_and_unique_execution_constraints():
    database.ensure_db()
    with database._connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO permission_executions VALUES ('x','absent','reserved','','now','now')")


def test_reserved_cannot_become_success_before_start_and_start_is_once():
    store, action = PermissionRecordStore(), action_for()
    decision = save_pending(store, action)
    store.answer(decision.id, action.authorization, approved=True, approved_by="human")
    store.reserve(action, decision.id)
    assert not store.finish(decision.id, action.authorization, status="succeeded")
    assert store.start(action, decision.id).status == "running"
    with pytest.raises(ValueError):
        store.start(action, decision.id)
    assert store.finish(decision.id, action.authorization, status="succeeded")
    assert store.get_execution(decision.id, action.authorization).status == "succeeded"
    assert not store.finish(decision.id, action.authorization, status="unknown")


def test_cancel_between_reserve_and_start_loses_live_permit():
    store, action = PermissionRecordStore(), action_for()
    decision = save_pending(store, action)
    store.answer(decision.id, action.authorization, approved=True, approved_by="human")
    store.reserve(action, decision.id)
    store.invalidate_run(action.authorization)
    with pytest.raises(ValueError):
        store.start(action, decision.id)
    assert store.get_execution(decision.id, action.authorization).status == "unknown"
    assert store.get_approval(decision.id, action.authorization).status == "cancelled"


def test_expired_pending_is_persisted_and_approved_can_expire_before_start():
    store, action = PermissionRecordStore(), action_for()
    expired = save_pending(store, action, ttl_seconds=-1)
    assert store.get_approval(expired.id, action.authorization).status == "expired"
    next_action = action.model_copy(update={"call_id": "next"})
    approved = save_pending(store, next_action)
    store.answer(approved.id, action.authorization, approved=True, approved_by="human")
    store.reserve(next_action, approved.id)
    with database._connect() as conn:
        conn.execute("UPDATE permission_approvals SET expires_at='2000-01-01' WHERE decision_id=?", (approved.id,))
        conn.commit()
    with pytest.raises(ValueError):
        store.start(next_action, approved.id)
    assert store.get_approval(approved.id, action.authorization).status == "expired"


def test_missing_run_cannot_create_permission_fact():
    store, action = PermissionRecordStore(), action_for()
    owner = action.authorization.model_copy(update={"run_id": "absent"})
    with pytest.raises(ValueError, match="Run 不存在"):
        save_pending(store, action.model_copy(update={"authorization": owner}))


def test_permission_connections_close_on_success_and_error(monkeypatch):
    store, action = PermissionRecordStore(), action_for()
    original, opened = database._connect, []
    def connect():
        conn = original()
        opened.append(conn)
        return conn
    monkeypatch.setattr(database, "_connect", connect)
    decision = save_pending(store, action)
    store.get_approval(decision.id, action.authorization)
    with pytest.raises(ValueError):
        store.reserve(action, decision.id)
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")


def test_run_metadata_is_strict_and_frozen_while_session_selection_can_change():
    session = database.insert_chat_session_if_absent({"id": "selected"})
    assert session["approvalMode"] == "manual"
    database.update_chat_session("selected", approval_mode="full_access")
    assert database.get_chat_session("selected")["approvalMode"] == "full_access"
    metadata = {"schemaVersion": 1, "tenantId": "local", "approvalMode": "manual", "reviewerConfigVersion": ""}
    run = database.upsert_chat_run({"id": "frozen", "sessionId": "selected", "agentId": "actor",
                                    "authorization": metadata})
    database.update_chat_session("selected", approval_mode="model_review")
    assert database.get_chat_run("frozen")["authorization"] == metadata
    assert database.upsert_chat_run({"id": "frozen", "status": "waiting_permission"})["authorization"] == metadata
    for changes in ({"approvalMode": "full_access"}, {"tenantId": "other"}, {"unexpected": True}):
        with pytest.raises(ValueError):
            database.upsert_chat_run({"id": "frozen", "authorization": {**metadata, **changes}})
    for key in ("sessionId", "workspaceId", "projectId", "agentId"):
        with pytest.raises(ValueError):
            database.upsert_chat_run({"id": "frozen", key: "different"})
    assert database.get_chat_run("frozen")["authorization"] == run["authorization"]
    with pytest.raises(ValueError):
        database.update_chat_session("selected", approval_mode="bypass")
    with pytest.raises(ValueError):
        database.insert_chat_session_if_absent({"id": "bad", "approvalMode": "trusted_tools"})
    assert database.get_chat_session("bad") is None


def _deny(store, action, *, source="model", reason_code="policy", reason="与用户限制冲突"):
    decision = AuthorizationDecision(id=f"pd_{uuid4().hex}", actionFingerprint=action.fingerprint,
        verdict="deny", source=source, reasonCode=reason_code, reason=reason)
    return store.save(action, decision)


def test_model_denial_review_is_one_new_attempt_and_cannot_override_boundary():
    store, action = PermissionRecordStore(), action_for()
    denied = _deny(store, action)
    attempt, review = store.request_human_review(denied.id, action.authorization.session_id)
    assert review.verdict == "needs_human"
    assert review.reason_code == "human_review"
    assert attempt.call_id != action.call_id
    assert attempt.normalized_input_json == action.normalized_input_json
    with pytest.raises(ValueError):
        store.reserve(action, denied.id)
    with pytest.raises(ValueError):
        store.reserve(attempt, review.id)
    assert store.answer(review.id, attempt.authorization, approved=True, approved_by="human")
    assert store.reserve(attempt, review.id).status == "reserved"
    with pytest.raises(ValueError):
        store.reserve(attempt, review.id)

    boundary = action.model_copy(update={"call_id": "boundary"})
    blocked = _deny(store, boundary, source="boundary", reason_code="hard_boundary", reason="超出文件范围")
    with pytest.raises(ValueError, match="硬边界"):
        store.request_human_review(blocked.id, action.authorization.session_id)

    database.update_chat_run_status("r", "completed")
    with pytest.raises(ValueError, match="终止"):
        store.request_human_review(denied.id, action.authorization.session_id)


def test_startup_reconcile_invalidates_pending_approval_and_execution():
    """后端重启对账：孤儿 Run 的 pending/approved 与未消费许可全部失效，历史保留但不可执行。"""

    from src.server.routes.chat.background_runs import reconcile_interrupted_chat_runs

    store, action = PermissionRecordStore(), action_for()
    database.upsert_chat_run({"id": "r", "workspaceId": "w", "projectId": "p", "sessionId": "s",
                              "status": "waiting_permission",
                              "authorization": {"schemaVersion": 1, "tenantId": "local",
                                                "approvalMode": "manual", "reviewerConfigVersion": ""}})
    pending = save_pending(store, action)
    approved_action = action.model_copy(update={"call_id": "c2"})
    approved = save_pending(store, approved_action)
    assert store.answer(approved.id, approved_action.authorization, approved=True, approved_by="human")
    store.reserve(approved_action, approved.id)

    count = reconcile_interrupted_chat_runs()
    assert count == 1
    assert database.get_chat_run("r")["status"] == "failed"
    assert store.get_approval(pending.id, action.authorization).status == "interrupted"
    assert store.get_approval(approved.id, approved_action.authorization).status == "interrupted"
    assert store.get_execution(approved.id, approved_action.authorization).status == "unknown"
    with pytest.raises(ValueError):
        store.find(action)
    assert not store.answer(pending.id, action.authorization, approved=True, approved_by="human")
    with pytest.raises(ValueError):
        store.start(approved_action, approved.id)
    # 历史仍可读取，但状态不再是 pending/approved。
    decision_row = None
    with database._connect() as conn:
        decision_row = conn.execute("SELECT * FROM permission_decisions WHERE id=?", (pending.id,)).fetchone()
    assert decision_row is not None and decision_row["invalidated_at"]
