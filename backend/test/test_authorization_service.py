"""权威决定复用、边界优先及模型等待不占数据库事务。"""

import asyncio

import pytest

from src.security.authorization.policy import evaluate_policy
from src.security.authorization.service import PlatformAuthorizationService
from src.storage.engine import database
from test.authorization_fakes import run_async
from test.test_permission_records import action_for


async def valid(action):
    return None


@pytest.mark.parametrize("mode", ["manual", "model_review", "full_access"])
@pytest.mark.parametrize("constraint", ["explicit deny", "tenant", "Project binding", "network", "path",
                                       "sandbox", "Plan", "cancelled"])
@run_async
async def test_boundary_precedes_every_mode_and_readonly(mode, constraint):
    async def denied(action):
        return constraint
    action = action_for(approvalMode=mode).model_copy(update={"effect": "read_only"})
    result = await evaluate_policy(action, denied)
    assert result.verdict == "deny" and result.source == "boundary" and result.reason == constraint


@run_async
async def test_manual_unchanged_resume_and_new_call_require_separate_approval():
    service = PlatformAuthorizationService(boundary_check=valid, revalidate=valid)
    action = action_for()
    first = await service.authorize(action)
    assert first.verdict == "needs_human"
    assert service.store.answer(first.id, action.authorization, approved=True, approved_by="human")
    resumed = await service.authorize(action)
    assert resumed.id == first.id and resumed.verdict == "allow"
    await service.reserve_execution(action, first.id)
    with pytest.raises(ValueError):
        await service.reserve_execution(action, first.id)
    second = await service.authorize(action.model_copy(update={"call_id": "new"}))
    assert second.id != first.id and second.verdict == "needs_human"


@run_async
async def test_model_wait_does_not_hold_db_lock_and_concurrent_call_bills_once():
    count = 0
    async def reviewer(action):
        nonlocal count
        count += 1
        # 第二连接可获得 SQLite 写事务，证明审核期间无数据库锁。
        with database._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
        await asyncio.sleep(0)
        return PlatformAuthorizationService._decision(action, "allow", "model", "test", "审核通过")
    database.ensure_db()
    service = PlatformAuthorizationService(boundary_check=valid, revalidate=valid, reviewer=reviewer)
    action = action_for(approvalMode="model_review")
    results = await asyncio.gather(*(service.authorize(action) for _ in range(5)))
    assert count == 1 and len({r.id for r in results}) == 1


@run_async
async def test_reviewer_failure_falls_back_to_human_and_boundary_tightening_wins():
    revoked = False
    async def boundary(action):
        return "能力已收紧" if revoked else None
    async def reviewer(action):
        raise TimeoutError()
    service = PlatformAuthorizationService(boundary_check=boundary, revalidate=valid, reviewer=reviewer)
    action = action_for(approvalMode="model_review")
    decision = await service.authorize(action)
    assert decision.verdict == "needs_human" and decision.source == "model_error"
    assert service.store.answer(decision.id, action.authorization, approved=True, approved_by="human")
    revoked = True
    assert (await service.authorize(action)).verdict == "deny"
    with pytest.raises(ValueError):
        await service.reserve_execution(action, decision.id)


@run_async
async def test_cancelled_full_access_cannot_consume_saved_allow():
    service = PlatformAuthorizationService(boundary_check=valid, revalidate=valid)
    action = action_for(approvalMode="full_access")
    decision = await service.authorize(action)
    service.store.invalidate_run(action.authorization)
    with pytest.raises(ValueError):
        await service.reserve_execution(action, decision.id)


@run_async
async def test_cancel_before_first_decision_rejects_late_model_allow():
    entered, release = asyncio.Event(), asyncio.Event()
    async def reviewer(action):
        entered.set()
        await release.wait()
        return PlatformAuthorizationService._decision(action, "allow", "model", "test", "通过")
    service = PlatformAuthorizationService(boundary_check=valid, revalidate=valid, reviewer=reviewer)
    action = action_for(approvalMode="model_review")
    task = asyncio.create_task(service.authorize(action))
    await entered.wait()
    service.store.invalidate_run(action.authorization)
    release.set()
    decision = await task
    assert decision.verdict == "deny" and decision.source == "boundary"
    with pytest.raises(ValueError):
        service.store.find(action)
    with database._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM permission_decisions").fetchone()[0] == 0


@run_async
async def test_tightened_boundary_during_model_wait_persists_denial():
    entered, release = asyncio.Event(), asyncio.Event()
    revoked = False
    async def boundary(action):
        return "能力已撤销" if revoked else None
    async def reviewer(action):
        entered.set()
        await release.wait()
        return PlatformAuthorizationService._decision(action, "allow", "model", "test", "通过")
    service = PlatformAuthorizationService(boundary_check=boundary, revalidate=valid, reviewer=reviewer)
    action = action_for(approvalMode="model_review")
    task = asyncio.create_task(service.authorize(action))
    await entered.wait()
    revoked = True
    release.set()
    decision = await task
    assert decision.verdict == "deny" and decision.source == "boundary"
    assert service.store.find(action) == decision


@run_async
async def test_cancelled_allow_is_not_returned_as_allowed():
    service = PlatformAuthorizationService(boundary_check=valid, revalidate=valid)
    action = action_for(approvalMode="full_access")
    await service.authorize(action)
    service.store.invalidate_run(action.authorization)
    assert (await service.authorize(action)).verdict == "deny"


@run_async
async def test_independent_services_share_same_live_action_review_lock():
    count = 0
    async def reviewer(action):
        nonlocal count
        count += 1
        await asyncio.sleep(0)
        return PlatformAuthorizationService._decision(action, "allow", "model", "test", "通过")
    services = [PlatformAuthorizationService(boundary_check=valid, revalidate=valid, reviewer=reviewer)
                for _ in range(5)]
    action = action_for(approvalMode="model_review")
    decisions = await asyncio.gather(*(service.authorize(action) for service in services))
    assert count == 1 and len({decision.id for decision in decisions}) == 1


@run_async
async def test_denial_breaker_blocks_before_model_and_only_human_resets_actor():
    calls = 0

    async def reviewer(action):
        nonlocal calls
        calls += 1
        return PlatformAuthorizationService._decision(action, "deny", "model", "known_risk", "存在风险")

    service = PlatformAuthorizationService(boundary_check=valid, revalidate=valid, reviewer=reviewer)
    first = action_for(approvalMode="model_review")
    for index in range(2):
        await service.authorize(first.model_copy(update={"call_id": f"deny-{index}"}))
    await service.authorize(first.model_copy(update={"call_id": "read", "effect": "read_only"}))

    async def allow(action):
        nonlocal calls
        calls += 1
        return PlatformAuthorizationService._decision(action, "allow", "model", "user_authorized", "用户目标允许")

    service.reviewer = allow
    allowed = await service.authorize(first.model_copy(update={"call_id": "allow-does-not-reset"}))
    assert allowed.verdict == "allow"
    service.reviewer = reviewer
    await service.authorize(first.model_copy(update={"call_id": "deny-2"}))
    blocked = await service.authorize(first.model_copy(update={"call_id": "after-limit"}))
    assert blocked.reason_code == "review_denial_breaker" and blocked.do_not_retry is True
    assert calls == 4
    other = first.authorization.model_copy(update={"actor_id": "member-b"})
    for index in range(3):
        await service.authorize(first.model_copy(update={
            "call_id": f"member-{index}",
            "authorization": other,
        }))
    newcomer = first.authorization.model_copy(update={"actor_id": "member-c"})
    skipped = await service.authorize(first.model_copy(update={
        "call_id": "member-c",
        "authorization": newcomer,
    }))
    assert skipped.reason_code == "review_denial_breaker"
    assert calls == 7
    service.reset_denials(first.authorization)
    await service.authorize(first.model_copy(update={"call_id": "after-human"}))
    assert calls == 8


@run_async
async def test_interactive_model_deny_pauses_for_review_and_job_returns_deny():
    async def reviewer(action):
        return PlatformAuthorizationService._decision(action, "deny", "model", "smartguard_deny", "审核模型拒绝这一次")

    service = PlatformAuthorizationService(boundary_check=valid, revalidate=valid, reviewer=reviewer)
    interactive = action_for(approvalMode="model_review").model_copy(update={"tool_name": "HttpRequest"})
    paused = await service.authorize(interactive)
    assert paused.verdict == "needs_human" and paused.reason_code == "human_review_of_model_deny"
    assert service.store.get_approval(paused.id, interactive.authorization).status == "pending"
    again = await service.authorize(interactive)
    assert again.id == paused.id and again.verdict == "needs_human"

    review_calls = {"n": 0}

    async def counting_reviewer(action):
        review_calls["n"] += 1
        return PlatformAuthorizationService._decision(action, "deny", "model", "smartguard_deny", "不应调用")

    service.reviewer = counting_reviewer
    job = action_for(approvalMode="model_review", surface="job").model_copy(
        update={"call_id": "job-call", "tool_name": "MemoryDelete", "tool_class": "MemoryDelete", "effect": "mutation"})
    denied = await service.authorize(job)
    assert review_calls["n"] == 0
    assert denied.verdict == "deny" and denied.source == "policy"
    assert service.store.get_approval(denied.id, job.authorization) is None

    calls = 0

    async def unused(action):
        nonlocal calls
        calls += 1
        return PlatformAuthorizationService._decision(action, "deny", "model", "smartguard_deny", "不应调用")

    service.reviewer = unused
    human = await service.authorize(action_for(approvalMode="model_review").model_copy(update={
        "call_id": "confirm", "tool_name": "ConfirmJob",
    }))
    assert calls == 0 and human.verdict == "needs_human"
    assert human.reason_code != "human_review_of_model_deny"


@run_async
async def test_model_deny_review_asks_instead_of_denied():
    from agentscope.permission import PermissionBehavior

    from src.agents.permission_adapter import _CallContext, _PlatformEngine, _call_context

    async def reviewer(action):
        return PlatformAuthorizationService._decision(action, "deny", "model", "smartguard_deny", "审核模型拒绝这一次")

    service = PlatformAuthorizationService(boundary_check=valid, revalidate=valid, reviewer=reviewer)
    action = action_for(approvalMode="model_review").model_copy(update={"tool_name": "HttpRequest"})
    tool = object()
    engine = _PlatformEngine(service)
    token = _call_context.set(_CallContext(tool=tool, action=action, builder=lambda *_args: action))
    try:
        decision = await engine.check_permission(tool, {"value": 1})
    finally:
        _call_context.reset(token)
    assert decision.behavior == PermissionBehavior.ASK
    assert decision.behavior != PermissionBehavior.DENY


@run_async
async def test_review_grant_cannot_cross_boundary_and_reject_returns_original_deny():
    blocked = False

    async def boundary(action):
        return "能力已收紧" if blocked else None

    async def reviewer(action):
        return PlatformAuthorizationService._decision(action, "deny", "model", "smartguard_deny", "审核模型拒绝这一次")

    service = PlatformAuthorizationService(boundary_check=boundary, revalidate=valid, reviewer=reviewer)
    action = action_for(approvalMode="model_review").model_copy(update={"tool_name": "HttpRequest"})
    review = await service.authorize(action)
    original_id = review.related_decision_id
    before = service.store.get_decision(original_id, action.authorization)
    assert service.store.answer(review.id, action.authorization, approved=True, approved_by="human")
    blocked = True
    with pytest.raises(ValueError, match="能力已收紧"):
        await service.reserve_execution(action, review.id)
    assert service.store.get_execution(review.id, action.authorization) is None
    assert service.store.get_decision(original_id, action.authorization) == before

    other = action.model_copy(update={"call_id": "sustain"})
    blocked = False
    pending = await service.authorize(other)
    assert service.store.answer(pending.id, other.authorization, approved=False, approved_by="human")
    denied = await service.authorize(other)
    assert denied.id == pending.related_decision_id
    assert denied.verdict == "deny" and denied.source == "model"


@run_async
async def test_changed_target_does_not_reuse_allow():
    from src.security.authorization.models import TargetVersion

    service = PlatformAuthorizationService(boundary_check=valid, revalidate=valid)
    action = action_for(approvalMode="full_access")
    first = await service.authorize(action)
    revised = action.model_copy(update={"targets": (TargetVersion(target="/tmp/other.txt"),)})
    second = await service.authorize(revised)
    assert first.verdict == "allow" and second.id != first.id


@run_async
async def test_repeated_hard_boundary_denials_stop_cross_tool_variants():
    async def denied(action):
        return "Project 资源未绑定"

    service = PlatformAuthorizationService(boundary_check=denied, revalidate=valid)
    action = action_for(approvalMode="full_access")
    first = await service.authorize(action)
    second = await service.authorize(action.model_copy(update={"call_id": "write-variant"}))
    third = await service.authorize(action.model_copy(update={"call_id": "bash-variant", "tool_name": "Bash"}))
    assert first.reason_code == "hard_boundary"
    assert second.reason_code == "hard_boundary"
    assert third.reason_code == "boundary_denial_breaker"
    assert third.do_not_retry is True
