"""全新 Team Runtime 的状态与事件边界测试。"""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from agentscope.event import (
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
)
from agentscope.message import ToolResultState

from src.agents.factory import AgentGuardrails, AgentModelOptions
from src.server.routes.chat.lifecycle import can_transition_run_status, waiting_status_for_interaction
from src.server.routes.chat.team_runtime.coordinator import TeamCoordinator
from src.server.routes.chat.team_runtime.member_runtime import (
    MEMBER_RECOVERY_MAX_REACT_ITERS,
    MEMBER_RECOVERY_TOOLS,
    TeamSessionError,
    _create_team_agent,
    _run_agent_stream,
)
from src.server.routes.chat.runtime_builder import RuntimeModelConfig
from src.server.routes.chat.team_runtime.maintenance import (
    INTERRUPTED_REASON,
    delete_team_runs_for_session,
    maintain_team_runtime_runs,
)
from src.server.routes.chat.team_runtime.service import TeamRunService
from src.server.routes.chat.team_runtime.store import TeamEventPublisher, TeamStore, TeamStoreError
from src.server.routes.chat.team_runtime.tools import leader_tools, member_tools


def test_chat_run_waiting_status_distinguishes_interaction_types() -> None:
    """顶层 Run 必须区分用户输入、权限审批与外部执行等待。"""

    assert waiting_status_for_interaction(
        "REQUIRE_EXTERNAL_EXECUTION",
        [{"name": "AskUser"}],
    ) == "waiting_user"
    assert waiting_status_for_interaction(
        "REQUIRE_USER_CONFIRM",
        [{"name": "Bash"}],
    ) == "waiting_permission"
    assert waiting_status_for_interaction(
        "REQUIRE_EXTERNAL_EXECUTION",
        [{"name": "ExternalTool"}],
    ) == "waiting_external"


def test_chat_run_terminal_status_cannot_transition_back_to_active() -> None:
    """顶层 Run 进入终态后不得被晚到事件改回运行态或其他终态。"""

    assert can_transition_run_status("waiting_permission", "running") is True
    assert can_transition_run_status("waiting", "waiting_user") is True
    assert can_transition_run_status("running", "failed") is True
    assert can_transition_run_status("failed", "running") is False
    assert can_transition_run_status("completed", "failed") is False
    assert can_transition_run_status("cancelled", "running") is False


def create_store(tmp_path) -> TeamStore:
    return TeamStore.create(
        session_id="session-new",
        chat_run_id="run-new",
        goal="完成一轮协作",
        leader_agent_id="agent-leader",
        leader_name="Leader",
        root=tmp_path,
    )


def test_team_store_uses_fresh_state_and_hides_runtime_prompt(tmp_path) -> None:
    store = create_store(tmp_path)
    member = store.spawn_member(name="Researcher", role="research", runtime_prompt="private prompt")

    snapshot = store.snapshot()

    assert snapshot["teamRunId"].startswith("trun_")
    assert snapshot["run"]["phase"] == "created"
    assert snapshot["run"]["status"] == "running"
    projected_member = next(item for item in snapshot["members"] if item["id"] == member["id"])
    assert "runtimePrompt" not in projected_member
    assert (store.ref.path / "events.jsonl").exists()


def test_task_dependencies_require_current_run_ids(tmp_path) -> None:
    store = create_store(tmp_path)
    member = store.spawn_member(name="Reviewer", role="review", runtime_prompt="review")

    with pytest.raises(TeamStoreError, match="任务依赖不存在"):
        store.create_task(
            title="Invalid",
            brief="invalid dependency",
            created_by="leader",
            assigned_member_id=member["id"],
            depends_on=["task_from_another_run"],
        )


def test_claim_result_and_dependency_unlock_invariants(tmp_path) -> None:
    store = create_store(tmp_path)
    first_member = store.spawn_member(name="Researcher", role="research", runtime_prompt="research")
    second_member = store.spawn_member(name="Reviewer", role="review", runtime_prompt="review")
    first = store.create_task(
        title="Research",
        brief="research",
        created_by="leader",
        assigned_member_id=first_member["id"],
    )
    second = store.create_task(
        title="Review",
        brief="review",
        created_by="leader",
        assigned_member_id=second_member["id"],
        depends_on=[first["id"]],
    )

    claimed = store.claim_task(first["id"], first_member["id"])
    assert claimed["status"] == "running"
    assert store.is_blocked(store.task(second["id"])) is True
    with pytest.raises(TeamStoreError, match="任务结果不能为空"):
        store.complete_task(first["id"], first_member["id"], "  ")

    store.read_inbox(first_member["id"])
    store.complete_task(first["id"], first_member["id"], "research result")

    assert store.task(first["id"])["status"] == "succeeded"
    completed_snapshot = next(task for task in store.snapshot()["tasks"] if task["id"] == first["id"])
    assert completed_snapshot["briefPreview"] == "research"
    assert completed_snapshot["resultPreview"] == "research result"
    assert store.is_blocked(store.task(second["id"])) is False
    assert any(event["type"] == "task.unlocked" for event in store.events_after(0))
    with pytest.raises(TeamStoreError, match="仍有任务未成功"):
        store.complete_run()


def test_fail_run_finalizes_open_tasks_and_members(tmp_path) -> None:
    store = create_store(tmp_path)
    running_member = store.spawn_member(name="Running", role="member", runtime_prompt="work")
    idle_member = store.spawn_member(name="Idle", role="member", runtime_prompt="wait")
    running_task = store.create_task(title="Running", brief="work", created_by="leader")
    queued_task = store.create_task(title="Queued", brief="wait", created_by="leader")
    store.claim_task(running_task["id"], running_member["id"])

    store.fail_run("coordination failed")

    assert store.run()["status"] == "failed"
    assert store.task(running_task["id"])["status"] == "cancelled"
    assert store.task(queued_task["id"])["status"] == "cancelled"
    assert store.member(running_member["id"])["status"] == "failed"
    assert store.member(idle_member["id"])["status"] == "stopped"
    assert store.member("leader")["status"] == "stopped"


def test_team_tools_are_role_scoped(tmp_path) -> None:
    store = create_store(tmp_path)
    member = store.spawn_member(name="Member", role="member", runtime_prompt="work")

    assert {tool.name for tool in leader_tools(store)} == {
        "team_spawn_member",
        "team_list_members",
        "team_create_task",
        "team_read_tasks",
        "team_send_mail",
        "team_read_mailbox",
        "team_finish_coordination",
    }
    assert {tool.name for tool in member_tools(store, member["id"])} == {
        "team_read_tasks",
        "team_read_mailbox",
        "team_send_mail",
        "team_submit_result",
    }


def test_team_agent_overrides_parent_iteration_budget(monkeypatch) -> None:
    captured = {}

    def fake_create(spec, **_kwargs):
        captured["spec"] = spec
        return spec

    monkeypatch.setattr(
        "src.server.routes.chat.team_runtime.member_runtime.create_main_agent_from_spec",
        fake_create,
    )
    ctx = SimpleNamespace(
        agent_id="leader-agent",
        session_id="session-new",
        persist_run=False,
        runtime_model_config=RuntimeModelConfig(
            model_name="test-model",
            fallback_names=[],
            model_options=AgentModelOptions(max_react_iters=99),
        ),
        guardrails=AgentGuardrails(),
    )

    _create_team_agent(
        ctx=ctx,
        actor_id="member-test",
        actor_name="Member",
        session_id="member-session",
        system_prompt="work",
        runtime_key="team-member-test",
        extra_tools=[],
        tool_allowlist=set(),
        used_skills=[],
        max_react_iters=32,
    )

    assert captured["spec"].model_options.max_react_iters == 32
    assert ctx.runtime_model_config.model_options.max_react_iters == 99


def test_coordinator_runs_independent_tasks_concurrently(tmp_path, monkeypatch) -> None:
    store = create_store(tmp_path)
    members = [
        store.spawn_member(name=f"Member {index}", role="member", runtime_prompt="work")
        for index in range(2)
    ]
    for index, member in enumerate(members):
        store.create_task(
            title=f"Task {index}",
            brief="work",
            created_by="leader",
            assigned_member_id=member["id"],
        )
    running = 0
    peak = 0

    async def fake_member_task(*, store, member, task, **_kwargs):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        store.read_inbox(member["id"])
        store.complete_task(task["id"], member["id"], f"result {task['id']}")
        running -= 1

    async def flush_events() -> None:
        return None

    monkeypatch.setattr(
        "src.server.routes.chat.team_runtime.coordinator.run_member_task",
        fake_member_task,
    )
    coordinator = TeamCoordinator(
        store=store,
        ctx=SimpleNamespace(),
        run_data=SimpleNamespace(),
        flush_events=flush_events,
    )

    asyncio.run(coordinator.run())

    assert peak == 2
    assert all(task["status"] == "succeeded" for task in store.tasks())


def test_coordinator_cancellation_stops_active_member_tasks(tmp_path, monkeypatch) -> None:
    store = create_store(tmp_path)
    member = store.spawn_member(name="Member", role="member", runtime_prompt="work")
    task = store.create_task(
        title="Long task",
        brief="wait",
        created_by="leader",
        assigned_member_id=member["id"],
    )

    async def scenario() -> None:
        started = asyncio.Event()

        async def fake_member_task(**_kwargs):
            started.set()
            await asyncio.Event().wait()

        async def flush_events() -> None:
            return None

        monkeypatch.setattr(
            "src.server.routes.chat.team_runtime.coordinator.run_member_task",
            fake_member_task,
        )
        coordinator = TeamCoordinator(
            store=store,
            ctx=SimpleNamespace(),
            run_data=SimpleNamespace(),
            flush_events=flush_events,
        )
        coordinator_task = asyncio.create_task(coordinator.run())
        await started.wait()
        coordinator_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await coordinator_task

    asyncio.run(scenario())

    assert store.task(task["id"])["status"] == "cancelled"
    assert store.member(member["id"])["status"] == "stopped"
    store.cancel_run()
    assert store.run()["status"] == "cancelled"
    assert any(event["type"] == "run.cancelled" for event in store.events_after(0))


def test_user_mail_is_persisted_and_published(tmp_path) -> None:
    store = create_store(tmp_path)
    member = store.spawn_member(name="Member", role="member", runtime_prompt="work")
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    service = TeamRunService(ctx=SimpleNamespace(), run_data=SimpleNamespace(), queue=queue)
    service.store = store
    service.publisher = TeamEventPublisher(store)
    service.publisher.mark_existing_seen()

    message = asyncio.run(service.send_user_mail(recipient=member["id"], content="请优先检查边界条件"))

    assert message["kind"] == "user_intervention"
    assert message["from"] == "user"
    assert store.inbox(member["id"])[-1]["content"] == "请优先检查边界条件"
    published = queue.get_nowait()
    assert published is not None
    assert published.event_type == "TEAM_DOMAIN_EVENT"
    assert published.data["type"] == "mail.sent"


def test_mailbox_broadcast_expands_to_individual_deliveries(tmp_path) -> None:
    store = create_store(tmp_path)
    first = store.spawn_member(name="First", role="member", runtime_prompt="work")
    second = store.spawn_member(name="Second", role="member", runtime_prompt="work")

    member_messages = store.broadcast_mail(sender=first["id"], content="同步证据")
    user_messages = store.broadcast_mail(sender="user", content="统一补充")

    assert {message["to"] for message in member_messages} == {"leader", second["id"]}
    assert all(message["from"] == first["id"] for message in member_messages)
    assert {message["to"] for message in user_messages} == {"leader", first["id"], second["id"]}
    assert len(store.mailbox()) == 5


def test_startup_maintenance_interrupts_orphaned_team_run(tmp_path, monkeypatch) -> None:
    store = create_store(tmp_path)
    member = store.spawn_member(name="Member", role="member", runtime_prompt="work")
    task = store.create_task(title="Running", brief="work", created_by="leader")
    store.claim_task(task["id"], member["id"])
    persisted: dict[str, object] = {}

    monkeypatch.setattr(
        "src.server.routes.chat.team_runtime.maintenance.update_chat_run_status",
        lambda run_id, status, completed_at: persisted.update(status=(run_id, status, completed_at)),
    )
    monkeypatch.setattr(
        "src.server.routes.chat.team_runtime.maintenance.get_chat_run",
        lambda _run_id: {"agentId": "leader-agent", "agentName": "Leader"},
    )
    monkeypatch.setattr(
        "src.server.routes.chat.team_runtime.maintenance._append_assistant_message",
        lambda **kwargs: persisted.update(message=kwargs),
    )

    result = maintain_team_runtime_runs(root=tmp_path, retention_days=3650, max_records=20)

    assert result["interrupted"] == 1
    assert store.run()["status"] == "cancelled"
    assert store.task(task["id"])["status"] == "cancelled"
    assert persisted["message"]["error"] == INTERRUPTED_REASON
    assert "team_snapshot" not in persisted["message"]


def test_team_run_retention_and_session_deletion(tmp_path) -> None:
    store = create_store(tmp_path)
    member = store.spawn_member(name="Member", role="member", runtime_prompt="work")
    task = store.create_task(title="Done", brief="work", created_by="leader")
    store.claim_task(task["id"], member["id"])
    store.read_inbox(member["id"])
    store.complete_task(task["id"], member["id"], "done")
    store.complete_run()
    completed_at = datetime.fromisoformat(store.run()["completedAt"])

    result = maintain_team_runtime_runs(
        root=tmp_path,
        retention_days=30,
        max_records=20,
        now=completed_at + timedelta(days=31),
    )

    assert result["removed"] == 1
    assert not store.ref.path.exists()

    active = create_store(tmp_path)
    with pytest.raises(TeamStoreError, match="仍有运行中的 TeamRun"):
        delete_team_runs_for_session("session-new", root=tmp_path)
    active.cancel_run()
    assert delete_team_runs_for_session("session-new", root=tmp_path) == 1


def test_unread_mail_blocks_result_until_member_reads_it(tmp_path) -> None:
    store = create_store(tmp_path)
    member = store.spawn_member(name="Member", role="member", runtime_prompt="work")
    task = store.create_task(
        title="Check",
        brief="check",
        created_by="leader",
        assigned_member_id=member["id"],
    )
    store.claim_task(task["id"], member["id"])
    store.read_inbox(member["id"])
    store.send_mail(
        sender="user",
        recipient=member["id"],
        kind="user_intervention",
        content="补充检查失败分支",
    )

    with pytest.raises(TeamStoreError, match="Mailbox 存在未读消息"):
        store.complete_task(task["id"], member["id"], "first draft")

    messages = store.read_inbox(member["id"])
    assert messages[-1]["content"] == "补充检查失败分支"
    assert store.unread_inbox(member["id"]) == []
    store.complete_task(task["id"], member["id"], "updated result")
    assert store.task_result(task["id"]) == "updated result"


def test_team_store_rejects_path_like_ids_and_mail_after_completion(tmp_path) -> None:
    store = create_store(tmp_path)
    member = store.spawn_member(name="Member", role="member", runtime_prompt="work")
    task = store.create_task(
        title="Check",
        brief="check",
        created_by="leader",
        assigned_member_id=member["id"],
    )
    with pytest.raises(TeamStoreError, match="Member ID 非法"):
        store.member("../run")
    with pytest.raises(TeamStoreError, match="Task ID 非法"):
        store.task("../../run")

    store.claim_task(task["id"], member["id"])
    store.read_inbox(member["id"])
    store.complete_task(task["id"], member["id"], "done")
    mailbox_version = len(store.mailbox())
    store.send_mail(sender="user", recipient=member["id"], content="latest evidence")
    with pytest.raises(TeamStoreError, match="Mailbox 在综合期间发生变化"):
        store.complete_run(expected_mailbox_version=mailbox_version)
    store.complete_run(expected_mailbox_version=len(store.mailbox()))
    with pytest.raises(TeamStoreError, match="不可接收用户消息"):
        store.send_mail(sender="user", recipient=member["id"], content="too late")


def test_member_stream_coalesces_deltas_and_preserves_tool_name(tmp_path) -> None:
    store = create_store(tmp_path)
    member = store.spawn_member(name="Member", role="member", runtime_prompt="work")
    task = store.create_task(title="Stream", brief="stream", created_by="leader")
    store.claim_task(task["id"], member["id"])

    class FakeEvent:
        def __init__(self, data: dict) -> None:
            self.data = data

        def model_dump(self, **_kwargs) -> dict:
            return dict(self.data)

    class FakeAgent:
        async def reply_stream(self, _message):
            for _ in range(100):
                yield FakeEvent({"type": "TEXT_BLOCK_DELTA", "delta": "abc"})
            yield FakeEvent({
                "type": "TOOL_CALL_START",
                "tool_call_id": "call-1",
                "tool_call_name": "team_read_mailbox",
            })
            yield FakeEvent({
                "type": "TOOL_RESULT_END",
                "tool_call_id": "call-1",
                "tool_call_name": "",
                "state": "success",
            })
            yield FakeEvent({"type": "TEXT_BLOCK_DELTA", "delta": "done"})

    async def flush_events() -> None:
        return None

    output = asyncio.run(_run_agent_stream(
        agent=FakeAgent(),
        user_msg=SimpleNamespace(),
        store=store,
        actor_id=member["id"],
        task_id=task["id"],
        flush_events=flush_events,
    ))
    member_events = [
        event
        for event in store.events_after(0)
        if event.get("actorId") == member["id"]
    ]
    output_events = [event for event in member_events if event["type"] == "member.output.delta"]
    completed_tool = next(event for event in member_events if event["type"] == "member.tool.completed")

    assert output == "abc" * 100 + "done"
    assert len(output_events) == 3
    assert "".join(str(event["payload"]["delta"]) for event in output_events) == output
    assert completed_tool["payload"]["name"] == "team_read_mailbox"


def test_member_stream_forwards_synthesis_output_deltas_immediately(tmp_path) -> None:
    """综合文本必须逐段转发给 Chat，不能只在 session 结束后整体 replace。"""

    store = create_store(tmp_path)

    class FakeEvent:
        def __init__(self, delta: str) -> None:
            self.delta = delta

        def model_dump(self, **_kwargs) -> dict:
            return {"type": "TEXT_BLOCK_DELTA", "delta": self.delta}

    class FakeAgent:
        async def reply_stream(self, _message):
            yield FakeEvent("第一段")
            yield FakeEvent("第二段")

    forwarded: list[str] = []

    async def flush_events() -> None:
        return None

    async def on_output_delta(delta: str) -> None:
        forwarded.append(delta)

    output = asyncio.run(_run_agent_stream(
        agent=FakeAgent(),
        user_msg=SimpleNamespace(),
        store=store,
        actor_id="leader",
        task_id="",
        flush_events=flush_events,
        on_output_delta=on_output_delta,
    ))

    assert forwarded == ["第一段", "第二段"]
    assert output == "第一段第二段"


def test_leader_stream_stops_after_finish_coordination_tool(tmp_path) -> None:
    store = create_store(tmp_path)

    class FakeEvent:
        def __init__(self, data: dict) -> None:
            self.data = data

        def model_dump(self, **_kwargs) -> dict:
            return dict(self.data)

    class FakeAgent:
        async def reply_stream(self, _message):
            yield FakeEvent({
                "type": "TOOL_CALL_START",
                "tool_call_id": "call-finish",
                "tool_call_name": "team_finish_coordination",
            })
            yield FakeEvent({
                "type": "TOOL_RESULT_END",
                "tool_call_id": "call-finish",
                "state": "success",
            })
            raise AssertionError("协调完成后不应继续消费 Leader 事件")

    async def flush_events() -> None:
        return None

    output = asyncio.run(_run_agent_stream(
        agent=FakeAgent(),
        user_msg=SimpleNamespace(),
        store=store,
        actor_id="leader",
        task_id="",
        flush_events=flush_events,
        stop_after_tool_name="team_finish_coordination",
    ))

    assert output == ""
    assert any(
        event["type"] == "member.tool.completed"
        and event["payload"]["name"] == "team_finish_coordination"
        for event in store.events_after(0)
    )


@pytest.mark.parametrize(
    ("required_type", "expected_resume_type", "expected_reason"),
    [
        ("REQUIRE_USER_CONFIRM", UserConfirmResultEvent, "requires_user_confirmation"),
        ("REQUIRE_EXTERNAL_EXECUTION", ExternalExecutionResultEvent, "requires_external_execution"),
    ],
)
def test_member_stream_denies_interactive_tool_and_resumes_session(
    tmp_path,
    required_type,
    expected_resume_type,
    expected_reason,
) -> None:
    store = create_store(tmp_path)
    member = store.spawn_member(name="Member", role="member", runtime_prompt="work")
    task = store.create_task(title="Protected tool", brief="work", created_by="leader")
    store.claim_task(task["id"], member["id"])

    class FakeEvent:
        def __init__(self, data: dict) -> None:
            self.data = data

        def model_dump(self, **_kwargs) -> dict:
            return dict(self.data)

    class FakeAgent:
        calls = 0

        async def reply_stream(self, message):
            self.calls += 1
            if self.calls == 1:
                yield FakeEvent({
                    "type": required_type,
                    "reply_id": "reply-1",
                    "tool_calls": [{
                        "type": "tool_call",
                        "id": "call-write",
                        "name": "Write",
                        "input": '{"file_path":"report.md","content":"draft"}',
                    }],
                })
                return
            assert isinstance(message, expected_resume_type)
            if isinstance(message, UserConfirmResultEvent):
                assert all(not result.confirmed for result in message.confirm_results)
            else:
                assert all(result.state == ToolResultState.DENIED for result in message.execution_results)
            yield FakeEvent({"type": "TEXT_BLOCK_DELTA", "delta": "continued read-only"})

    async def flush_events() -> None:
        return None

    output = asyncio.run(_run_agent_stream(
        agent=FakeAgent(),
        user_msg=SimpleNamespace(),
        store=store,
        actor_id=member["id"],
        task_id=task["id"],
        flush_events=flush_events,
    ))
    denied = next(event for event in store.events_after(0) if event["type"] == "member.tool.denied")

    assert output == "continued read-only"
    assert denied["payload"] == {
        "toolCallId": "call-write",
        "name": "Write",
        "reason": expected_reason,
    }


def test_member_stream_recovers_after_iteration_limit_with_restricted_tools(tmp_path) -> None:
    store = create_store(tmp_path)
    member = store.spawn_member(name="Member", role="member", runtime_prompt="work")
    task = store.create_task(title="Converge", brief="work", created_by="leader")
    store.claim_task(task["id"], member["id"])

    class FakeEvent:
        def __init__(self, data: dict) -> None:
            self.data = data

        def model_dump(self, **_kwargs) -> dict:
            return dict(self.data)

    class FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeAgent:
        def __init__(self) -> None:
            self.calls = 0
            self.react_config = SimpleNamespace(max_iters=32)
            self.toolkit = SimpleNamespace(tool_groups=[SimpleNamespace(
                tools=[*(FakeTool(name) for name in MEMBER_RECOVERY_TOOLS), FakeTool("Grep")],
                mcps=[object()],
                skills_or_loaders=[object()],
            )])

        async def reply_stream(self, message):
            self.calls += 1
            if self.calls == 1:
                yield FakeEvent({
                    "type": "TOOL_CALL_START",
                    "tool_call_id": "call-grep",
                    "tool_call_name": "Grep",
                })
                yield FakeEvent({"type": "EXCEED_MAX_ITERS"})
                return
            assert message.name == "user"
            assert "team_submit_result" in str(message.content)
            assert self.react_config.max_iters == MEMBER_RECOVERY_MAX_REACT_ITERS
            group = self.toolkit.tool_groups[0]
            assert {tool.name for tool in group.tools} == MEMBER_RECOVERY_TOOLS
            assert group.mcps == []
            assert group.skills_or_loaders == []
            yield FakeEvent({"type": "TEXT_BLOCK_DELTA", "delta": "converged"})

    async def flush_events() -> None:
        return None

    output = asyncio.run(_run_agent_stream(
        agent=FakeAgent(),
        user_msg=SimpleNamespace(),
        store=store,
        actor_id=member["id"],
        task_id=task["id"],
        flush_events=flush_events,
        allow_iteration_recovery=True,
    ))
    recovery = next(
        event for event in store.events_after(0)
        if event["type"] == "member.iteration.recovery"
    )

    assert output == "converged"
    assert recovery["payload"]["lastTool"] == "Grep"
    assert recovery["payload"]["recoveryLimit"] == MEMBER_RECOVERY_MAX_REACT_ITERS


def test_member_stream_fails_after_recovery_budget_is_exhausted(tmp_path) -> None:
    store = create_store(tmp_path)
    member = store.spawn_member(name="Member", role="member", runtime_prompt="work")
    task = store.create_task(title="Exhaust", brief="work", created_by="leader")
    store.claim_task(task["id"], member["id"])

    class FakeEvent:
        def model_dump(self, **_kwargs) -> dict:
            return {"type": "EXCEED_MAX_ITERS"}

    class FakeAgent:
        react_config = SimpleNamespace(max_iters=32)
        toolkit = SimpleNamespace(tool_groups=[])

        async def reply_stream(self, _message):
            yield FakeEvent()

    async def flush_events() -> None:
        return None

    with pytest.raises(TeamSessionError, match=r"32\+8"):
        asyncio.run(_run_agent_stream(
            agent=FakeAgent(),
            user_msg=SimpleNamespace(),
            store=store,
            actor_id=member["id"],
            task_id=task["id"],
            flush_events=flush_events,
            allow_iteration_recovery=True,
        ))

    assert any(
        event["type"] == "member.iteration.exhausted"
        for event in store.events_after(0)
    )
