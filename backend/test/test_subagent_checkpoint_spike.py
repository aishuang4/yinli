"""AgentScope Subagent 跨进程恢复能力 spike。

AgentScope 2.0.3 没有 Agent 级 checkpoint API，但 AgentState 是可序列化的
Pydantic 模型。这里验证当前版本的精确恢复边界：用相同 runtime 配置重建
Agent 并恢复 AgentState 后，ASKING 和 SUBMITTED 两种暂停状态都能继续。
"""

from src.storage.records.trace_records import list_subagent_transcript_items
from src.storage.records.trace_records import append_subagent_transcript_item
from src.storage.records.trace_records import reconcile_interrupted_subagent_invocations
from src.storage.records.trace_records import get_subagent_invocation
from src.storage.records.trace_records import create_subagent_invocation

import asyncio

from agentscope.agent import Agent
from agentscope.event import ExternalExecutionResultEvent, UserConfirmResultEvent
from agentscope.message import (
    Msg,
    TextBlock,
    ToolCallBlock,
    ToolCallState,
    ToolResultBlock,
    ToolResultState,
)
from agentscope.model import ChatResponse
from agentscope.permission import PermissionContext, PermissionDecision, PermissionBehavior
from agentscope.state import AgentState
from agentscope.tool import ToolBase, ToolChunk, Toolkit

from src.storage.engine import database
from src.subagents.task_service import InvalidSubagentTaskTransition, SubagentTaskService
from src.subagents.supervisor import SubagentSupervisor


class _ResumeModel:
    """离线模型：收到恢复结果后直接生成最终文本。"""

    model = "checkpoint-spike"
    context_size = 32_768

    async def count_tokens(self, *_args: object, **_kwargs: object) -> int:
        return 1

    async def __call__(self, *_args: object, **_kwargs: object) -> ChatResponse:
        return ChatResponse(content=[TextBlock(text="resumed-after-restart")], is_last=True)


class _ConfirmedTool(ToolBase):
    """用于验证人工确认后仍可执行的最小工具。"""

    name = "ConfirmedTool"
    description = "Checkpoint spike tool."
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    is_concurrency_safe = True
    is_read_only = True

    async def check_permissions(
        self,
        tool_input: dict,
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(behavior=PermissionBehavior.ALLOW)

    async def call(self, **_kwargs: object) -> ToolChunk:
        return ToolChunk(content=[TextBlock(text="confirmed")], state=ToolResultState.SUCCESS, is_last=True)


def _waiting_state(*, tool_name: str, tool_state: ToolCallState) -> AgentState:
    """构造 AgentScope 已经暂停后的状态快照。"""

    return AgentState(
        session_id="checkpoint-session",
        reply_id="checkpoint-reply",
        cur_iter=1,
        context=[
            Msg(
                id="checkpoint-reply",
                role="assistant",
                name="Checkpoint Agent",
                content=[
                    ToolCallBlock(
                        id="checkpoint-tool",
                        name=tool_name,
                        input="{}",
                        state=tool_state,
                    ),
                ],
            ),
        ],
    )


def _rebuild_agent(state: AgentState, *, needs_tool: bool = False) -> Agent:
    """模拟后端重启后按同一 runtime spec 重建 Agent。"""

    return Agent(
        name="Checkpoint Agent",
        system_prompt="checkpoint spike",
        model=_ResumeModel(),
        toolkit=Toolkit(tools=[_ConfirmedTool()]) if needs_tool else Toolkit(),
        state=state,
    )


def _collect_events(agent: Agent, event: object) -> list[object]:
    async def collect() -> list[object]:
        return [item async for item in agent.reply_stream(event)]

    return asyncio.run(collect())


def test_child_confirmation_checkpoint_restores_and_continues() -> None:
    """child 的 REQUIRE_USER_CONFIRM 可由持久 AgentState 精确继续。"""

    snapshot = _waiting_state(
        tool_name="ConfirmedTool",
        tool_state=ToolCallState.ASKING,
    ).model_dump_json()
    restored_state = AgentState.model_validate_json(snapshot)
    agent = _rebuild_agent(restored_state, needs_tool=True)
    tool_call = restored_state.context[-1].get_content_blocks("tool_call")[0]

    events = _collect_events(
        agent,
        UserConfirmResultEvent.model_validate({
            "reply_id": "checkpoint-reply",
            "confirm_results": [{"confirmed": True, "tool_call": tool_call.model_dump(mode="json")}],
        }),
    )

    assert any(getattr(event, "delta", "") == "resumed-after-restart" for event in events)
    assert restored_state.context[-1].get_content_blocks("tool_call")[0].state == ToolCallState.FINISHED
    assert restored_state.context[-1].get_content_blocks("tool_result")[0].output[0].text == "confirmed"


def test_parent_external_wait_checkpoint_restores_and_continues() -> None:
    """parent 的 DelegateTask external wait 可由持久 AgentState 精确继续。"""

    snapshot = _waiting_state(
        tool_name="DelegateTask",
        tool_state=ToolCallState.SUBMITTED,
    ).model_dump_json()
    restored_state = AgentState.model_validate_json(snapshot)
    agent = _rebuild_agent(restored_state)

    events = _collect_events(
        agent,
        ExternalExecutionResultEvent(
            reply_id="checkpoint-reply",
            execution_results=[
                ToolResultBlock(
                    id="checkpoint-tool",
                    name="DelegateTask",
                    output=[TextBlock(text="child completed")],
                    state=ToolResultState.SUCCESS,
                ),
            ],
        ),
    )

    assert any(getattr(event, "delta", "") == "resumed-after-restart" for event in events)
    assert restored_state.context[-1].get_content_blocks("tool_call")[0].state == ToolCallState.FINISHED
    assert restored_state.context[-1].get_content_blocks("tool_result")[0].output[0].text == "child completed"


def test_task_service_rejects_terminal_state_regression(tmp_path, monkeypatch) -> None:
    """迟到 worker 不能把 completed task 写回 running。"""

    service = SubagentTaskService()
    task = service.create({
        "run_id": "run-1", "session_id": "session-1", "parent_agent_id": "agent-1",
        "parent_agent_name": "Agent", "subagent_name": "reviewer", "task": "Review.",
    })
    service.transition(task["id"], target_status="running")
    service.transition(task["id"], target_status="completed", result_preview="Done.")

    try:
        service.transition(task["id"], target_status="running")
    except InvalidSubagentTaskTransition:
        pass
    else:
        raise AssertionError("Terminal task must not transition back to running.")

    assert get_subagent_invocation(task["id"])["status"] == "completed"


def test_supervisor_limits_concurrency_and_cleans_completed_handles() -> None:
    """Supervisor 只管理当前进程 handle，并遵守全局并发上限。"""

    async def run_case() -> tuple[int, set[str]]:
        supervisor = SubagentSupervisor(max_concurrency=1)
        active = 0
        peak = 0

        async def operation() -> str:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
            return "done"

        first = await supervisor.start("one", operation)
        second = await supervisor.start("two", operation)
        assert await supervisor.active_invocation_ids() == {"one", "two"}
        await asyncio.gather(first, second)
        await asyncio.sleep(0)
        return peak, await supervisor.active_invocation_ids()

    peak, remaining = asyncio.run(run_case())

    assert peak == 1
    assert remaining == set()


def test_supervisor_cancellation_reaches_running_operation() -> None:
    """取消请求必须进入实际执行 coroutine，而不仅改变数据库状态。"""

    async def run_case() -> tuple[bool, bool]:
        supervisor = SubagentSupervisor()
        entered = asyncio.Event()
        cancelled = False

        async def operation() -> None:
            nonlocal cancelled
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled = True
                raise

        task = await supervisor.start("cancel-me", operation)
        await entered.wait()
        accepted = await supervisor.cancel("cancel-me")
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0)
        return accepted, cancelled

    accepted, cancelled = asyncio.run(run_case())

    assert accepted is True
    assert cancelled is True


def test_subagent_transcript_query_respects_invocation_identity(tmp_path, monkeypatch) -> None:
    """Task detail 查询必须按 invocation identity 返回对应 transcript。"""

    first = create_subagent_invocation({
        "id": "subagent-one", "session_id": "session-1", "parent_agent_id": "agent-1",
        "parent_agent_name": "Agent", "subagent_name": "reviewer", "task": "One.",
    })
    second = create_subagent_invocation({
        "id": "subagent-two", "session_id": "session-1", "parent_agent_id": "agent-1",
        "parent_agent_name": "Agent", "subagent_name": "reviewer", "task": "Two.",
    })
    append_subagent_transcript_item(invocation_id=first["id"], kind="one", payload={})
    append_subagent_transcript_item(invocation_id=second["id"], kind="two", payload={})

    assert [item["kind"] for item in list_subagent_transcript_items(first["id"])] == ["one"]


def test_startup_reconciliation_interrupts_running_but_preserves_waiting(tmp_path, monkeypatch) -> None:
    """重启不应伪造 running，也不能把可见 HITL waiting 倒退为 interrupted。"""

    common = {
        "session_id": "session-1", "parent_agent_id": "agent-1", "parent_agent_name": "Agent",
        "subagent_name": "reviewer", "task": "Review.",
    }
    running = create_subagent_invocation({"id": "running", "status": "running", **common})
    waiting = create_subagent_invocation({"id": "waiting", "status": "waiting", **common})

    assert reconcile_interrupted_subagent_invocations() == 1
    assert get_subagent_invocation(running["id"])["status"] == "interrupted"
    assert get_subagent_invocation(waiting["id"])["status"] == "waiting"
