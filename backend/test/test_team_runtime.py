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
    LEADER_MAX_REACT_ITERS,
    MEMBER_MAX_REACT_ITERS,
    MEMBER_RECOVERY_MAX_REACT_ITERS,
    MEMBER_RECOVERY_TOOLS,
    TeamSessionError,
    _create_team_agent,
    _finish_coordination_if_ready,
    _run_agent_stream,
    run_leader_coordination,
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
    assert store.member(first_member["id"])["status"] == "running"
    assert store.member(first_member["id"])["activeTaskId"] == first["id"]
    running_member_event = next(
        event
        for event in store.events_after(0)
        if event["type"] == "member.status.changed"
        and event["actorId"] == first_member["id"]
        and event["status"] == "running"
    )
    assert running_member_event["payload"]["member"]["activeTaskId"] == first["id"]
    attempt_id = claimed["currentAttemptId"]
    assert attempt_id.startswith("attempt_")
    assert store.attempt(attempt_id)["status"] == "running"
    assert store.is_blocked(store.task(second["id"])) is True
    with pytest.raises(TeamStoreError, match="任务结果不能为空"):
        store.complete_task(first["id"], first_member["id"], "  ")

    store.read_inbox(first_member["id"])
    store.complete_task(first["id"], first_member["id"], "research result")

    assert store.task(first["id"])["status"] == "succeeded"
    assert store.task(first["id"])["acceptedAttemptId"] == attempt_id
    assert store.attempt(attempt_id)["status"] == "accepted"
    assert len(store.attempts(first["id"])) == 1
    completed_snapshot = next(task for task in store.snapshot()["tasks"] if task["id"] == first["id"])
    assert completed_snapshot["briefPreview"] == "research"
    assert completed_snapshot["resultPreview"] == "research result"
    assert store.is_blocked(store.task(second["id"])) is False
    assert any(event["type"] == "task.unlocked" for event in store.events_after(0))
    with pytest.raises(TeamStoreError, match="仍有任务未结束"):
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
    # Team 成员与单 Agent 共用上下文治理 middleware；未用 Skill 时不挂 SkillContextMiddleware
    names = [type(item).__name__ for item in captured["spec"].middlewares]
    assert names == ["ContextHygieneMiddleware", "ContextCompressionMiddleware"]


def test_team_agent_with_skills_keeps_skill_context_and_memory_prompt(monkeypatch) -> None:
    captured = {}

    class FakeAgent:
        def __init__(self):
            from agentscope.state import AgentState

            self.state = AgentState()

    def fake_create(spec, **_kwargs):
        captured["spec"] = spec
        return FakeAgent()

    monkeypatch.setattr(
        "src.server.routes.chat.team_runtime.member_runtime.create_main_agent_from_spec",
        fake_create,
    )
    ctx = SimpleNamespace(
        agent_id="leader-agent",
        session_id="session-new",
        persist_run=False,
        memory_prompt="# Memory\n用户偏好中文。",
        runtime_model_config=RuntimeModelConfig(
            model_name="test-model",
            fallback_names=[],
            model_options=AgentModelOptions(max_react_iters=99),
        ),
        guardrails=AgentGuardrails(),
    )

    agent = _create_team_agent(
        ctx=ctx,
        actor_id="member-test",
        actor_name="Member",
        session_id="member-session",
        system_prompt="work\n\n# Memory\n用户偏好中文。",
        runtime_key="team-member-test",
        extra_tools=[],
        tool_allowlist=set(),
        used_skills=[],
        max_react_iters=32,
        skill_context=True,
    )

    names = [type(item).__name__ for item in captured["spec"].middlewares]
    assert names == ["SkillContextMiddleware", "ContextHygieneMiddleware", "ContextCompressionMiddleware"]
    # offloader 按 Team session 分目录；prompt 分段字数供 contextStats
    assert agent.state.session_id == "member-session"
    from src.server.routes.chat.trace_recorder import PROMPT_SECTIONS_ATTR

    sections = getattr(agent, PROMPT_SECTIONS_ATTR)
    assert sections["memory"] == len(ctx.memory_prompt)
    assert sections["team"] == len("work\n\n")


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


def test_user_mail_new_task_wakes_idle_member(tmp_path) -> None:
    store = create_store(tmp_path)
    member = store.spawn_member(name="Member", role="member", runtime_prompt="work")
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    service = TeamRunService(ctx=SimpleNamespace(), run_data=SimpleNamespace(), queue=queue)
    service.store = store
    service.publisher = TeamEventPublisher(store)
    service.publisher.mark_existing_seen()

    asyncio.run(service.send_user_mail(
        recipient=member["id"],
        content="补充核对版本差异",
        dispatch_mode="new_task",
    ))

    task = store.tasks()[-1]
    assert task["status"] == "queued"
    assert task["assignedMemberId"] == member["id"]
    assert task["origin"] == "user_mail"
    assert task["originMailId"]
    user_mail = next(
        mail for mail in reversed(store.mailbox())
        if mail["from"] == "user" and mail["kind"] == "user_intervention"
    )
    assert user_mail["dispatchMode"] == "new_task"


def test_user_mail_continue_task_creates_followup_for_idle_member(tmp_path) -> None:
    store = create_store(tmp_path)
    member = store.spawn_member(name="Member", role="member", runtime_prompt="work")
    task = store.create_task(title="Research", brief="research", created_by="leader", assigned_member_id=member["id"])
    store.claim_task(task["id"], member["id"])
    store.read_inbox(member["id"])
    store.complete_task(task["id"], member["id"], "initial result")
    store.set_member_status(member["id"], "idle")

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    service = TeamRunService(ctx=SimpleNamespace(), run_data=SimpleNamespace(), queue=queue)
    service.store = store
    service.publisher = TeamEventPublisher(store)
    service.publisher.mark_existing_seen()
    asyncio.run(service.send_user_mail(
        recipient=member["id"],
        content="继续补充证据",
        dispatch_mode="continue_task",
        task_id=task["id"],
    ))

    assert store.task(task["id"])["status"] == "succeeded"
    followup = next(task for task in store.tasks() if task["origin"] == "user_mail")
    assert followup["status"] == "queued"
    assert followup["assignedMemberId"] == member["id"]
    assert followup["dependsOn"] == [task["id"]]
    user_mail = next(
        mail for mail in reversed(store.mailbox())
        if mail["from"] == "user" and mail["kind"] == "user_intervention"
    )
    assert followup["originMailId"] == user_mail["id"]


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


def test_member_stream_reports_model_calls_to_observer(tmp_path) -> None:
    """MODEL_CALL_START / END 交给观测回调（写 CONTEXT_USAGE trace），不进 Team 领域事件。"""

    store = create_store(tmp_path)

    class FakeEvent:
        def __init__(self, data: dict) -> None:
            self.data = data

        def model_dump(self, **_kwargs) -> dict:
            return dict(self.data)

    class FakeAgent:
        async def reply_stream(self, _message):
            yield FakeEvent({"type": "MODEL_CALL_START", "model_name": "m"})
            yield FakeEvent({"type": "TEXT_BLOCK_DELTA", "delta": "ok"})
            yield FakeEvent({"type": "MODEL_CALL_END", "model_name": "m", "input_tokens": 12, "output_tokens": 3})

    seen: list[tuple[str, int]] = []

    async def flush_events() -> None:
        return None

    async def on_model_call(agent, event_type, data) -> None:
        assert isinstance(agent, FakeAgent)
        seen.append((event_type, int(data.get("input_tokens") or 0)))

    output = asyncio.run(_run_agent_stream(
        agent=FakeAgent(),
        user_msg=SimpleNamespace(),
        store=store,
        actor_id="leader",
        task_id="",
        flush_events=flush_events,
        on_model_call=on_model_call,
    ))

    assert output == "ok"
    assert seen == [("MODEL_CALL_START", 0), ("MODEL_CALL_END", 12)]
    assert not any("model" in str(event.get("type") or "").lower() for event in store.events_after(0))


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
            self.react_config = SimpleNamespace(max_iters=MEMBER_MAX_REACT_ITERS)
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
                yield FakeEvent({
                    "type": "REPLY_END",
                    "finished_reason": "exceed_max_iters",
                })
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
        react_config = SimpleNamespace(max_iters=MEMBER_MAX_REACT_ITERS)
        toolkit = SimpleNamespace(tool_groups=[])

        async def reply_stream(self, _message):
            yield FakeEvent()

    async def flush_events() -> None:
        return None

    with pytest.raises(
        TeamSessionError,
        match=rf"{MEMBER_MAX_REACT_ITERS}\+{MEMBER_RECOVERY_MAX_REACT_ITERS}",
    ):
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


def test_store_caps_members_tasks_and_result_length(tmp_path) -> None:
    from src.server.routes.chat.team_runtime.store import MAX_TASK_RESULT_CHARS, MAX_TEAM_MEMBERS, MAX_TEAM_TASKS

    store = create_store(tmp_path)
    members = [store.spawn_member(name=f"M{i}", role="member", runtime_prompt="work") for i in range(MAX_TEAM_MEMBERS)]
    with pytest.raises(TeamStoreError, match="最多创建"):
        store.spawn_member(name="extra", role="member", runtime_prompt="work")

    for i in range(MAX_TEAM_TASKS):
        store.create_task(title=f"T{i}", brief="b", created_by="leader")
    with pytest.raises(TeamStoreError, match="最多创建"):
        store.create_task(title="extra", brief="b", created_by="leader")

    # 结果超长必须写文件，而不是塞进 result
    task = store.tasks()[0]
    member = members[0]
    store.claim_task(task["id"], member["id"])
    store.read_inbox(member["id"])
    with pytest.raises(TeamStoreError, match="超过"):
        store.complete_task(task["id"], member["id"], "x" * (MAX_TASK_RESULT_CHARS + 1))
    store.complete_task(task["id"], member["id"], "x" * MAX_TASK_RESULT_CHARS)
    assert store.task(task["id"])["status"] == "succeeded"


def test_store_rejects_dependency_cycle_from_tampered_task_graph(tmp_path) -> None:
    from src.server.routes.chat.team_runtime.store import _dependency_cycle

    tasks = [
        {"id": "a", "dependsOn": ["b"]},
        {"id": "b", "dependsOn": ["a"]},
    ]
    assert _dependency_cycle(tasks, ["a"]) is True
    assert _dependency_cycle([{"id": "a", "dependsOn": []}, {"id": "b", "dependsOn": ["a"]}], ["b"]) is False


def test_coordinator_retries_failed_task_once_then_partial_completes(tmp_path, monkeypatch) -> None:
    """任务失败先重派一次；仍失败则保留 failed、级联下游，只要有成功任务就返回让综合阶段做部分综合。"""

    store = create_store(tmp_path)
    good = store.spawn_member(name="Good", role="member", runtime_prompt="work")
    bad = store.spawn_member(name="Bad", role="member", runtime_prompt="work")
    ok_task = store.create_task(title="OK", brief="b", created_by="leader", assigned_member_id=good["id"])
    flaky = store.create_task(title="Flaky", brief="b", created_by="leader", assigned_member_id=bad["id"])
    downstream = store.create_task(title="Downstream", brief="b", created_by="leader", depends_on=[flaky["id"]])
    attempts: dict[str, int] = {}

    async def fake_member_task(*, store, member, task, **_kwargs):
        attempts[task["id"]] = attempts.get(task["id"], 0) + 1
        if task["id"] == flaky["id"]:
            raise RuntimeError("tool exploded")
        store.read_inbox(member["id"])
        store.complete_task(task["id"], member["id"], f"result {task['id']}")

    async def flush_events() -> None:
        return None

    monkeypatch.setattr("src.server.routes.chat.team_runtime.coordinator.run_member_task", fake_member_task)
    coordinator = TeamCoordinator(store=store, ctx=SimpleNamespace(), run_data=SimpleNamespace(), flush_events=flush_events)

    asyncio.run(coordinator.run())

    assert attempts[flaky["id"]] == 2  # 首次 + 重派一次
    assert attempts[ok_task["id"]] == 1
    by_id = {task["id"]: task for task in store.tasks()}
    assert by_id[ok_task["id"]]["status"] == "succeeded"
    assert by_id[flaky["id"]]["status"] == "failed"
    assert by_id[flaky["id"]]["retryCount"] == 1
    assert by_id[downstream["id"]]["status"] == "failed"
    assert by_id[downstream["id"]]["retryable"] is False
    assert "依赖任务已失败" in by_id[downstream["id"]]["failureReason"]
    assert any(event["type"] == "task.retry.scheduled" for event in store.events_after(0))

    run = store.complete_run()
    assert run["status"] == "completed"
    assert run["partial"] is True and run["failedTaskCount"] == 2
    assert store.member(bad["id"])["status"] == "idle"


def test_coordinator_does_not_abort_when_blocked_tasks_exist_and_assignee_was_failed(tmp_path, monkeypatch) -> None:
    """成员被标成 failed 时，只要还有可执行任务就不能因为下游 blocked 而整队中止。"""

    store = create_store(tmp_path)
    member = store.spawn_member(name="Worker", role="member", runtime_prompt="work")
    ready = store.create_task(title="Ready", brief="b", created_by="leader", assigned_member_id=member["id"])
    blocked = store.create_task(title="Blocked", brief="b", created_by="leader", depends_on=[ready["id"]])
    store.set_member_status(member["id"], "failed")

    async def fake_member_task(*, store, member, task, **_kwargs):
        store.read_inbox(member["id"])
        store.complete_task(task["id"], member["id"], f"result {task['id']}")

    async def flush_events() -> None:
        return None

    monkeypatch.setattr("src.server.routes.chat.team_runtime.coordinator.run_member_task", fake_member_task)
    coordinator = TeamCoordinator(store=store, ctx=SimpleNamespace(), run_data=SimpleNamespace(), flush_events=flush_events)
    asyncio.run(coordinator.run())

    assert store.task(ready["id"])["status"] == "succeeded"
    assert store.task(blocked["id"])["status"] == "succeeded"
    assert store.member(member["id"])["status"] == "idle"


def test_coordinator_fails_when_every_task_fails(tmp_path, monkeypatch) -> None:
    from src.server.routes.chat.team_runtime.coordinator import TeamCoordinatorError

    store = create_store(tmp_path)
    member = store.spawn_member(name="Bad", role="member", runtime_prompt="work")
    store.create_task(title="Only", brief="b", created_by="leader", assigned_member_id=member["id"])

    async def fake_member_task(**_kwargs):
        raise RuntimeError("boom")

    async def flush_events() -> None:
        return None

    monkeypatch.setattr("src.server.routes.chat.team_runtime.coordinator.run_member_task", fake_member_task)
    coordinator = TeamCoordinator(store=store, ctx=SimpleNamespace(), run_data=SimpleNamespace(), flush_events=flush_events)
    with pytest.raises(TeamCoordinatorError, match="任务全部失败"):
        asyncio.run(coordinator.run())
    with pytest.raises(TeamStoreError, match="没有任何成功任务"):
        store.complete_run()


def test_coordinator_times_out_stuck_member_task(tmp_path, monkeypatch) -> None:
    from src.server.routes.chat.team_runtime import coordinator as coordinator_module

    store = create_store(tmp_path)
    member = store.spawn_member(name="Slow", role="member", runtime_prompt="work")
    task = store.create_task(title="Slow", brief="b", created_by="leader", assigned_member_id=member["id"])
    calls = 0

    async def fake_member_task(*, store, member, task, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(1)
        store.read_inbox(member["id"])
        store.complete_task(task["id"], member["id"], "late but fine")

    async def flush_events() -> None:
        return None

    monkeypatch.setattr(coordinator_module, "run_member_task", fake_member_task)
    monkeypatch.setattr(coordinator_module, "TASK_TIMEOUT_SECONDS", 0.05)
    coordinator = TeamCoordinator(store=store, ctx=SimpleNamespace(), run_data=SimpleNamespace(), flush_events=flush_events)
    asyncio.run(coordinator.run())

    assert calls == 2
    assert store.task(task["id"])["status"] == "succeeded"
    assert store.task(task["id"])["retryCount"] == 1


def test_synthesis_prompt_is_json_with_failed_tasks_and_trimmed_mail(tmp_path) -> None:
    import json

    from src.server.routes.chat.team_runtime.member_runtime import _synthesis_user_prompt

    store = create_store(tmp_path)
    member = store.spawn_member(name="M", role="member", runtime_prompt="work")
    done = store.create_task(title="Done", brief="b", created_by="leader", assigned_member_id=member["id"])
    failed = store.create_task(title="Failed", brief="b", created_by="leader")
    store.claim_task(done["id"], member["id"])
    store.read_inbox(member["id"])
    store.complete_task(done["id"], member["id"], "the result")
    store.fail_task(failed["id"], "", "network down", retryable=False)
    store.send_mail(sender="user", recipient="leader", kind="user_note", subject="补充", content="y" * 2000)

    payload = json.loads(_synthesis_user_prompt(store, "目标"))

    assert payload["userGoal"] == "目标"
    assert payload["successfulResults"] == [{"title": "Done", "result": "the result"}]
    assert payload["failedOrCancelledTasks"][0]["title"] == "Failed"
    assert "network down" in payload["failedOrCancelledTasks"][0]["reason"]
    # 结果邮件不重复进 mailbox；长邮件被裁
    assert all(mail["kind"] != "result" for mail in payload["mailbox"])
    assert max(len(mail["content"]) for mail in payload["mailbox"]) <= 600


def test_finish_coordination_if_ready_requires_member_and_task(tmp_path) -> None:
    store = create_store(tmp_path)
    assert _finish_coordination_if_ready(store, reason="empty") is False
    assert store.run()["phase"] == "created"

    store.spawn_member(name="Writer", role="writer", runtime_prompt="write")
    assert _finish_coordination_if_ready(store, reason="no-task") is False

    store.create_task(title="Draft", brief="write copy", created_by="leader")
    assert _finish_coordination_if_ready(store, reason="ready") is True
    assert store.run()["phase"] == "executing"
    assert any(
        event["type"] == "member.iteration.recovery" and event["payload"].get("autoFinish")
        for event in store.events_after(0)
    )


def test_leader_coordination_autofinishes_after_max_iters(tmp_path, monkeypatch) -> None:
    store = create_store(tmp_path)
    member = store.spawn_member(name="Writer", role="writer", runtime_prompt="write")
    store.create_task(title="Draft", brief="write copy", created_by="leader", assigned_member_id=member["id"])
    captured: dict = {}

    monkeypatch.setattr(
        "src.server.routes.chat.team_runtime.member_runtime._create_team_agent",
        lambda **kwargs: captured.update(kwargs) or object(),
    )

    async def boom(**_kwargs):
        raise TeamSessionError("Team session 达到最大 ReAct 轮数")

    monkeypatch.setattr(
        "src.server.routes.chat.team_runtime.member_runtime._run_agent_stream",
        boom,
    )
    ctx = SimpleNamespace(
        agent_id="leader-agent",
        agent_name="Leader",
        session_id="session-new",
        persist_run=False,
        memory_prompt="",
        memory_recall_text="",
        user_message_note="",
    )
    run_data = SimpleNamespace(message="写一篇发布稿", images=[], files=[])

    async def flush_events() -> None:
        return None

    asyncio.run(run_leader_coordination(
        store=store,
        ctx=ctx,
        run_data=run_data,
        flush_events=flush_events,
    ))

    assert captured["max_react_iters"] == LEADER_MAX_REACT_ITERS == 64
    assert store.run()["phase"] == "executing"
    assert store.member("leader")["status"] == "idle"


def test_leader_coordination_still_fails_without_tasks(tmp_path, monkeypatch) -> None:
    store = create_store(tmp_path)
    store.spawn_member(name="Writer", role="writer", runtime_prompt="write")

    monkeypatch.setattr(
        "src.server.routes.chat.team_runtime.member_runtime._create_team_agent",
        lambda **_kwargs: object(),
    )

    async def boom(**_kwargs):
        raise TeamSessionError("Team session 达到最大 ReAct 轮数")

    monkeypatch.setattr(
        "src.server.routes.chat.team_runtime.member_runtime._run_agent_stream",
        boom,
    )
    ctx = SimpleNamespace(
        agent_id="leader-agent",
        agent_name="Leader",
        session_id="session-new",
        persist_run=False,
        memory_prompt="",
        memory_recall_text="",
        user_message_note="",
    )
    run_data = SimpleNamespace(message="写一篇发布稿", images=[], files=[])

    async def flush_events() -> None:
        return None

    with pytest.raises(TeamSessionError, match="最大 ReAct"):
        asyncio.run(run_leader_coordination(
            store=store,
            ctx=ctx,
            run_data=run_data,
            flush_events=flush_events,
        ))

    assert store.run()["phase"] == "coordinating"
