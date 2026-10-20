"""Team 审批并行恢复、领域事实和活动预算回归。"""

import asyncio
from types import SimpleNamespace

import pytest

from src.server.routes.chat.projection.source_events import SourceEvent
from src.server.routes.chat.team_runtime.execution_budget import ActiveExecutionBudget
from src.server.routes.chat.team_runtime.service import TeamRunService
from src.server.routes.chat.team_runtime.store import TeamStore


def test_team_permission_resolves_only_selected_waiter(tmp_path, monkeypatch):
    """批准一个成员不会替换主任务或唤醒另一个成员；每次审批只写一条领域事实。"""

    cleared = []
    monkeypatch.setattr("src.server.routes.chat.team_runtime.service._clear_pending_interaction", cleared.append)
    monkeypatch.setattr("src.server.routes.chat.team_runtime.service._hitl_payload", lambda item: {"interactionId": item.id})
    monkeypatch.setattr("src.server.routes.chat.models.get_pending_interaction", lambda key: interactions[key])
    monkeypatch.setattr("src.server.routes.chat.team_runtime.service.runtime_trace_source_event",
                        lambda kind, **data: SourceEvent(kind, data))
    interactions = {key: SimpleNamespace(id=key, agent_id=f"member-{key}", agent_name=key,
                    session_id="session", run_id="run", type="REQUIRE_USER_CONFIRM", chat_data={})
                    for key in ("first", "second")}

    async def scenario():
        queue = asyncio.Queue()
        service = TeamRunService(ctx=SimpleNamespace(), run_data=SimpleNamespace(), queue=queue)
        service.store = TeamStore.create(session_id="session", chat_run_id="run", goal="work",
                                        leader_agent_id="leader", leader_name="Leader", root=tmp_path)
        waiters = [asyncio.create_task(service.wait_for_member_permission(item)) for item in interactions.values()]
        await asyncio.sleep(0)
        assert all(not task.done() for task in waiters)
        await service.resolve_member_permission("first", "approved", action="approve")
        assert await waiters[0] == "approved"
        assert not waiters[1].done()
        with pytest.raises(RuntimeError, match="已失效"):
            await service.resolve_member_permission("first", "duplicate", action="approve")
        signals = []
        while not queue.empty():
            signals.append(queue.get_nowait())
        assert [item.event_type for item in signals] == [
            "RUN_WAITING", "RUN_WAITING", "HITL_RESOLVED", "RUN_RESUMED", "RUN_WAITING",
        ]
        assert signals[-1].data["status"] == "waiting_permission"
        await service.resolve_member_permission("second", "denied", action="reject")
        assert await waiters[1] == "denied"
        assert not service._member_waiters
        events = service.store.events_after(0)
        assert len([event for event in events if event["type"] == "member.permission.required"]) == 2
        assert [event["payload"]["action"] for event in events
                if event["type"] == "member.permission.resolved"] == ["approve", "reject"]
        assert cleared == ["first", "second"]

    asyncio.run(scenario())


def test_team_permission_cancel_removes_pending_waiter(tmp_path, monkeypatch):
    """取消挂起的成员必须清理 live waiter 和 Chat pending。"""

    cleared = []
    monkeypatch.setattr("src.server.routes.chat.team_runtime.service._clear_pending_interaction", cleared.append)
    monkeypatch.setattr("src.server.routes.chat.team_runtime.service._hitl_payload", lambda item: {})
    monkeypatch.setattr("src.server.routes.chat.team_runtime.service.runtime_trace_source_event",
                        lambda kind, **data: SourceEvent(kind, data))

    async def scenario():
        service = TeamRunService(ctx=SimpleNamespace(), run_data=SimpleNamespace(), queue=asyncio.Queue())
        service.store = TeamStore.create(session_id="session", chat_run_id="run", goal="work",
                                        leader_agent_id="leader", leader_name="Leader", root=tmp_path)
        interaction = SimpleNamespace(id="pending", agent_id="leader", agent_name="Leader",
                                      session_id="session", run_id="run", type="REQUIRE_USER_CONFIRM", chat_data={})
        task = asyncio.create_task(service.wait_for_member_permission(interaction))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not service.has_member_waiter("pending")
        assert cleared == ["pending"]

    asyncio.run(scenario())


def test_human_wait_suspends_deadline_but_retains_remaining_budget():
    """人工等待没有 deadline，恢复后继续原剩余预算而非重新给十分钟。"""

    async def scenario():
        async with ActiveExecutionBudget(10) as budget:
            original_deadline = budget.timeout.when()

            async def human(_interaction):
                assert budget.timeout.when() is None
                await asyncio.sleep(0)
                return "approved"

            assert await budget.wait_for_human(human, None) == "approved"
            assert 0 < budget.remaining <= 10
            assert budget.timeout.when() >= original_deadline
            assert budget.timeout.when() - asyncio.get_running_loop().time() <= budget.remaining

    asyncio.run(scenario())
