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
        database.upsert_chat_run({"id": "r", "projectSlug": "p", "sessionId": "s"})
    owner = RunAuthorization(tenantId=tenant, projectSlug="p", sessionId="s",
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
    assert all(item["riskClass"] for item in summary["items"])


def test_projection_separates_approval_from_execution_for_trace_and_snapshot():
    from src.server.routes.chat.completion import _permission_summary
    from src.server.routes.chat.observation_runtime import _review_trace_facts
    from src.server.routes.chat.projection.source_normalizer import _permission_facts
    from src.storage.permission_records import project_permission_fact

    store, action = PermissionRecordStore(), action_for()
    approved = save_pending(store, action)
    store.answer(approved.id, action.authorization, approved=True, approved_by="human")
    summary = store.summarize_run("r")
    assert summary["counts"]["approved"] == 1
    assert summary["counts"]["succeeded"] == 0
    assert summary["items"][0]["status"] == "approved"

    failed = action.model_copy(update={"call_id": "failed"})
    failed_decision = save_pending(store, failed)
    store.answer(failed_decision.id, failed.authorization, approved=True, approved_by="human")
    store.reserve(failed, failed_decision.id)
    store.start(failed, failed_decision.id)
    store.finish(failed_decision.id, failed.authorization, status="failed")
    unknown = action.model_copy(update={"call_id": "unknown"})
    unknown_decision = save_pending(store, unknown)
    store.answer(unknown_decision.id, unknown.authorization, approved=True, approved_by="human")
    store.reserve(unknown, unknown_decision.id)
    store.finish(unknown_decision.id, unknown.authorization, status="unknown")
    rejected = action.model_copy(update={"call_id": "rejected"})
    rejected_decision = save_pending(store, rejected)
    store.answer(rejected_decision.id, rejected.authorization, approved=False, approved_by="human")

    summary = _permission_summary("r")
    by_call = {item["callId"]: item for item in summary["items"]}
    assert by_call["c"]["status"] == "approved"
    assert by_call["failed"]["status"] == "failed"
    assert by_call["unknown"]["status"] == "unknown"
    assert by_call["rejected"]["status"] == "denied"
    assert by_call["failed"]["riskClass"]

    shared = project_permission_fact(verdict="needs_human", approval_status="approved",
                                     execution_status="failed", risk_class="dangerous")
    snapshot = _permission_facts([{"verdict": "needs_human", "status": "approved",
                                   "executionStatus": "failed", "riskClass": "dangerous"}])
    traced = _review_trace_facts([{"reviewable": True, "verdict": "needs_human", "status": "approved",
                                   "executionStatus": "failed", "riskClass": "dangerous",
                                   "decisionId": "pd", "reasonCode": "human_review_of_model_deny"}])
    assert snapshot[0] == shared
    assert traced[0]["status"] == shared["status"]
    assert traced[0]["riskClass"] == shared["riskClass"]


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


def test_normal_review_and_concurrent_entries_reserve_once(local_tenant):
    """普通批准、复核批准和并发恢复都只能取得一次占位。"""

    store, action = PermissionRecordStore(), action_for(approvalMode="full_access")
    allowed = AuthorizationDecision(id=f"pd_{uuid4().hex}", actionFingerprint=action.fingerprint,
        verdict="allow", source="full_access", reasonCode="full_access", reason="开放")
    store.save(action, allowed)
    assert store.reserve(action, allowed.id).status == "reserved"
    with pytest.raises(ValueError):
        store.reserve(action, allowed.id)

    review_action = action.model_copy(update={"call_id": "review-once"})
    denied = AuthorizationDecision(id=f"pd_{uuid4().hex}", actionFingerprint=review_action.fingerprint,
        verdict="deny", source="model", reasonCode="smartguard_deny", reason="审核模型拒绝这一次")
    review = store.save_model_deny_with_review(review_action, denied)
    store.answer(review.id, review_action.authorization, approved=True, approved_by="human")
    assert store.reserve(review_action, review.id).status == "reserved"
    with pytest.raises(ValueError):
        store.reserve(review_action, review.id)

    concurrent = action.model_copy(update={"call_id": "concurrent"})
    pending = save_pending(store, concurrent)
    store.answer(pending.id, concurrent.authorization, approved=True, approved_by="human")

    def reserve(_):
        with use_tenant(local_tenant):
            try:
                return store.reserve(concurrent, pending.id).id
            except ValueError:
                return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len([item for item in pool.map(reserve, range(6)) if item]) == 1


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


@pytest.mark.parametrize("field,value", [("projectSlug", "other"),
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
    for key in ("sessionId", "projectSlug", "agentId"):
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


def test_model_denial_review_keeps_original_snapshot_and_cannot_override_boundary():
    store, action = PermissionRecordStore(), action_for()
    denied = AuthorizationDecision(id=f"pd_{uuid4().hex}", actionFingerprint=action.fingerprint,
        verdict="deny", source="model", reasonCode="smartguard_deny", reason="审核模型拒绝这一次")
    review = store.save_model_deny_with_review(action, denied)
    assert review.verdict == "needs_human"
    assert review.reason_code == "human_review_of_model_deny"
    assert review.related_decision_id == denied.id
    assert review.action_fingerprint == action.fingerprint
    with database._connect() as conn:
        rows = conn.execute("SELECT call_id, decision_json FROM permission_decisions ORDER BY created_at").fetchall()
    assert [row["call_id"] for row in rows] == [action.call_id, f"{action.call_id}#review"]
    original = AuthorizationDecision.model_validate_json(rows[0]["decision_json"])
    assert original.verdict == "deny" and original.id == denied.id
    with pytest.raises(ValueError):
        store.reserve(action, denied.id)
    assert store.answer(review.id, action.authorization, approved=True, approved_by="human")
    assert store.reserve(action, review.id).status == "reserved"
    with pytest.raises(ValueError):
        store.reserve(action, review.id)

    boundary = action.model_copy(update={"call_id": "boundary"})
    blocked = AuthorizationDecision(id=f"pd_{uuid4().hex}", actionFingerprint=boundary.fingerprint,
        verdict="deny", source="boundary", reasonCode="hard_boundary", reason="超出文件范围")
    with pytest.raises(ValueError, match="硬边界"):
        store.save_model_deny_with_review(boundary, blocked)

    database.update_chat_run_status("r", "completed")
    fresh = action.model_copy(update={"call_id": "after-stop"})
    stopped = AuthorizationDecision(id=f"pd_{uuid4().hex}", actionFingerprint=fresh.fingerprint,
        verdict="deny", source="model", reasonCode="smartguard_deny", reason="审核模型拒绝这一次")
    with pytest.raises(ValueError, match="终止"):
        store.save_model_deny_with_review(fresh, stopped)


def test_startup_reconcile_invalidates_pending_approval_and_execution():
    """后端重启对账：孤儿 Run 的 pending/approved 与未消费许可全部失效，历史保留但不可执行。"""

    from src.server.routes.chat.background_runs import reconcile_interrupted_chat_runs

    store, action = PermissionRecordStore(), action_for()
    database.upsert_chat_run({"id": "r", "projectSlug": "p", "sessionId": "s",
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


def test_answer_batch_rejects_all_when_one_item_is_expired():
    store = PermissionRecordStore()
    first_action = action_for()
    second_action = first_action.model_copy(update={"call_id": "batch-2"})
    first = save_pending(store, first_action)
    second = save_pending(store, second_action)
    with database._connect() as conn:
        conn.execute("UPDATE permission_decisions SET expires_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", first.id))
        conn.execute("UPDATE permission_approvals SET expires_at=? WHERE decision_id=?", ("2000-01-01T00:00:00+00:00", first.id))
        conn.commit()
    reasons = store.answer_batch(
        [(first.id, first_action.authorization), (second.id, second_action.authorization)],
        approved=True,
        approved_by="human",
    )
    assert reasons[0]["reason"] == "已过期"
    assert store.get_approval(second.id, second_action.authorization).status == "pending"
    again = store.answer_batch([(second.id, second_action.authorization)], approved=True, approved_by="human")
    assert again[0]["reason"] == ""
    repeat = store.answer_batch([(second.id, second_action.authorization)], approved=True, approved_by="human")
    assert repeat[0]["reason"] == "already_answered"
    assert store.get_approval(second.id, second_action.authorization).status == "approved"


def test_cancel_invalidates_run_before_clearing_pending_interaction():
    from src.server.routes.chat.background_runs import ChatBackgroundRun, _release_cancelled_run
    from src.server.routes.chat.models import PENDING_AGENTS, PENDING_INTERACTIONS, PendingInteraction

    store = PermissionRecordStore()
    action = action_for()
    pending = save_pending(store, action)
    run = ChatBackgroundRun(
        id="r",
        session_id="s",
        
        project_slug="p",
        persisted=True,
        status="cancelled",
    )
    interaction = PendingInteraction(
        id="hitl-1",
        type="tool",
        session_id="s",
        agent_id="a",
        agent_name="助手",
        model_name="m",
        reply_id="reply",
        run_id="r",
        tool_calls=[],
        message="请批准",
    )
    PENDING_INTERACTIONS[interaction.id] = interaction
    PENDING_AGENTS[interaction.id] = object()
    _release_cancelled_run(run)
    assert interaction.id not in PENDING_INTERACTIONS
    assert interaction.id not in PENDING_AGENTS
    assert store.answer(pending.id, action.authorization, approved=True, approved_by="human") is False


def _decision_json(decision_id: str) -> str:
    with database._connect() as conn:
        row = conn.execute("SELECT decision_json FROM permission_decisions WHERE id=?", (decision_id,)).fetchone()
    return row["decision_json"]


def test_review_keeps_original_deny_and_expiry_or_reject_returns_it():
    store, action = PermissionRecordStore(), action_for(approvalMode="model_review")
    denied = AuthorizationDecision(id=f"pd_{uuid4().hex}", actionFingerprint=action.fingerprint,
        verdict="deny", source="model", reasonCode="smartguard_deny", reason="审核模型拒绝这一次")
    review = store.save_model_deny_with_review(action, denied)
    original = _decision_json(denied.id)
    assert store.answer(review.id, action.authorization, approved=False, approved_by="human")
    assert _decision_json(denied.id) == original
    found = store.find(action)
    assert found is not None and found.id == denied.id and found.verdict == "deny" and found.source == "model"
    with pytest.raises(ValueError):
        store.reserve(action, denied.id)

    fresh = action.model_copy(update={"call_id": "expire"})
    denied_again = AuthorizationDecision(id=f"pd_{uuid4().hex}", actionFingerprint=fresh.fingerprint,
        verdict="deny", source="model", reasonCode="smartguard_deny", reason="审核模型拒绝这一次")
    expired = store.save_model_deny_with_review(fresh, denied_again)
    before = _decision_json(denied_again.id)
    with database._connect() as conn:
        conn.execute("UPDATE permission_decisions SET expires_at=? WHERE action_fingerprint=?",
                     ("2000-01-01T00:00:00+00:00", fresh.fingerprint))
        conn.execute("UPDATE permission_approvals SET expires_at=? WHERE decision_id=?",
                     ("2000-01-01T00:00:00+00:00", expired.id))
        conn.commit()
    assert store.find(fresh).id == denied_again.id
    assert _decision_json(denied_again.id) == before
    with pytest.raises(ValueError):
        store.reserve(fresh, expired.id)


def test_approved_review_consumes_one_grant_and_trace_keeps_three_facts():
    from src.server.routes.chat.observation_runtime import observe_source_event, open_run_root
    from src.storage.database import list_trace_observations

    store, action = PermissionRecordStore(), action_for(approvalMode="model_review")
    denied = AuthorizationDecision(id=f"pd_{uuid4().hex}", actionFingerprint=action.fingerprint,
        verdict="deny", source="model", reasonCode="smartguard_deny", reason="审核模型拒绝这一次")
    review = store.save_model_deny_with_review(action, denied)
    original = _decision_json(denied.id)
    assert store.answer(review.id, action.authorization, approved=True, approved_by="human")
    assert _decision_json(denied.id) == original
    execution = store.reserve(action, review.id)
    with pytest.raises(ValueError):
        store.reserve(action, review.id)
    with pytest.raises(ValueError):
        store.reserve(action, denied.id)
    facts = store.trace_facts(action)
    assert facts["modelDeny"]["id"] == denied.id
    assert facts["review"]["id"] == review.id
    assert facts["execution"]["id"] == execution.id
    assert len({facts["modelDeny"]["id"], facts["review"]["id"], facts["execution"]["id"]}) == 3

    open_run_root(persist=True, run_id=action.authorization.run_id, session_id="s", task="复核")
    observe_source_event(
        persist=True, run_id=action.authorization.run_id, session_id="s", event_type="HITL_REQUIRED",
        data={"payload": {"interactionId": "hitl-review", "toolCalls": [{"name": "HttpRequest"}], "decisions": [{
            "reviewable": True,
            "decisionId": review.id,
            "verdict": review.verdict,
            "source": review.source,
            "reasonCode": review.reason_code,
            "originalDecision": facts["modelDeny"],
            "executionId": execution.id,
            "executionStatus": execution.status,
        }]}},
    )
    approval = next(item for item in list_trace_observations(action.authorization.run_id) if item["kind"] == "approval")
    traced = approval["input"]["authorizationFacts"][0]
    assert traced["modelDeny"]["id"] == denied.id
    assert traced["review"]["id"] == review.id
    assert traced["execution"]["id"] == execution.id
