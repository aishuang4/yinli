"""Team Member 独立 Agent session 运行器。

本模块直接消费 AgentScope 事件，不复用 Chat Leader 的阶段、HITL 或最终答案流。
"""

from collections.abc import Awaitable, Callable
from dataclasses import replace

from agentscope.event import (
    ConfirmResult,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
)
from agentscope.message import UserMsg
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    ToolResultState,
)

from src.agents.factory import RuntimeToolScope
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.run_context import ChatRunContext
from src.server.routes.chat.runtime import _build_user_content, _guardrail_cache_key
from src.server.routes.chat.runtime_builder import build_main_agent_runtime_spec, create_main_agent_from_spec
from src.server.routes.chat.team_runtime.store import TeamStore, TeamStoreError
from src.server.routes.chat.team_runtime.tools import leader_tools, member_tools
from src.server.routes.chat.trace_recorder import _make_fallback_trace_recorder


FlushEvents = Callable[[], Awaitable[None]]
DISABLED_TOOL_SCOPE = RuntimeToolScope(
    allow_mcp=False,
    allow_knowledge=False,
    allow_bash=False,
    allow_http=False,
)
MAX_AUTOMATIC_TOOL_DENIALS = 8
LEADER_MAX_REACT_ITERS = 16
MEMBER_MAX_REACT_ITERS = 32
MEMBER_RECOVERY_MAX_REACT_ITERS = 8
SYNTHESIS_MAX_REACT_ITERS = 8
MEMBER_RECOVERY_TOOLS = {
    "team_read_tasks",
    "team_read_mailbox",
    "team_send_mail",
    "team_submit_result",
}


class TeamSessionError(RuntimeError):
    """Team 成员 session 未按领域协议结束。"""


async def run_leader_coordination(
    *,
    store: TeamStore,
    ctx: ChatRunContext,
    run_data: ChatMessage,
    flush_events: FlushEvents,
) -> None:
    """运行只负责建队和任务拆分的 Leader session。"""

    store.set_phase("coordinating")
    store.set_member_status("leader", "running")
    await flush_events()
    tools = leader_tools(store)
    allowlist = {tool.name for tool in tools}
    agent = _create_team_agent(
        ctx=ctx,
        actor_id="leader",
        actor_name=ctx.agent_name,
        session_id=f"{ctx.session_id}:{store.ref.id}:leader",
        system_prompt=_leader_system_prompt(store, run_data),
        runtime_key=f"team-leader-{store.ref.id}",
        extra_tools=tools,
        tool_allowlist=allowlist,
        used_skills=[],
        max_react_iters=LEADER_MAX_REACT_ITERS,
    )
    await _run_agent_stream(
        agent=agent,
        user_msg=UserMsg(
            name="user",
            content=_build_user_content(
                f"为本轮目标组建团队并创建共享任务：\n{run_data.message}",
                run_data.images,
            ),
        ),
        store=store,
        actor_id="leader",
        task_id="",
        flush_events=flush_events,
        stop_after_tool_name="team_finish_coordination",
    )
    if store.run().get("phase") != "executing":
        raise TeamSessionError("Leader 未调用 team_finish_coordination")
    store.set_member_status("leader", "idle")
    await flush_events()


async def run_member_task(
    *,
    store: TeamStore,
    ctx: ChatRunContext,
    run_data: ChatMessage,
    member: dict,
    task: dict,
    flush_events: FlushEvents,
) -> None:
    """运行一个 Member 的单任务隔离 session。"""

    member_id = str(member["id"])
    task_id = str(task["id"])
    tools = member_tools(store, member_id)
    allowlist = {
        "Read",
        "Write",
        "Edit",
        "Grep",
        "Glob",
        "Bash",
        "HttpRequest",
        "memory_node_tree",
        "memory_node_query",
        *(str(tool.name) for tool in ctx.knowledge_tools),
        *(tool.name for tool in tools),
    }
    agent = _create_team_agent(
        ctx=ctx,
        actor_id=member_id,
        actor_name=str(member.get("name") or member_id),
        session_id=f"{ctx.session_id}:{store.ref.id}:{member_id}",
        system_prompt=_member_system_prompt(store, member, task),
        runtime_key=f"team-member-{store.ref.id}-{member_id}-{task_id}",
        extra_tools=tools,
        tool_allowlist=allowlist,
        used_skills=ctx.used_skills,
        runtime_tool_scope=ctx.runtime_tool_scope,
        mcp_servers=ctx.mcp_servers,
        knowledge_tools=ctx.knowledge_tools,
        max_react_iters=MEMBER_MAX_REACT_ITERS,
    )
    await _run_agent_stream(
        agent=agent,
        user_msg=UserMsg(
            name="user",
            content=_build_user_content(_member_user_prompt(store, member_id, task), run_data.images),
        ),
        store=store,
        actor_id=member_id,
        task_id=task_id,
        flush_events=flush_events,
        allow_iteration_recovery=True,
    )
    if store.task(task_id).get("status") != "succeeded":
        raise TeamSessionError("Member session 结束时没有通过 team_submit_result 完成当前任务")


async def run_synthesis(
    *,
    store: TeamStore,
    ctx: ChatRunContext,
    run_data: ChatMessage,
    flush_events: FlushEvents,
) -> str:
    """使用独立无工具 session 综合最终答案。"""

    unfinished = [task for task in store.tasks() if task.get("status") != "succeeded"]
    if unfinished:
        raise TeamSessionError("存在未成功任务，不能进入综合阶段")
    store.set_phase("synthesizing")
    store.set_member_status("leader", "running")
    await flush_events()
    agent = _create_team_agent(
        ctx=ctx,
        actor_id="leader",
        actor_name=ctx.agent_name,
        session_id=f"{ctx.session_id}:{store.ref.id}:synthesis",
        system_prompt=_synthesis_system_prompt(),
        runtime_key=f"team-synthesis-{store.ref.id}",
        extra_tools=[],
        tool_allowlist=set(),
        used_skills=[],
        max_react_iters=SYNTHESIS_MAX_REACT_ITERS,
    )
    text = await _run_agent_stream(
        agent=agent,
        user_msg=UserMsg(
            name="user",
            content=_build_user_content(_synthesis_user_prompt(store, run_data.message), run_data.images),
        ),
        store=store,
        actor_id="leader",
        task_id="",
        flush_events=flush_events,
    )
    if not text.strip():
        raise TeamSessionError("综合 session 没有生成最终答案")
    store.set_member_status("leader", "idle")
    await flush_events()
    return text.strip()


def _create_team_agent(
    *,
    ctx: ChatRunContext,
    actor_id: str,
    actor_name: str,
    session_id: str,
    system_prompt: str,
    runtime_key: str,
    extra_tools: list,
    tool_allowlist: set[str],
    used_skills: list,
    runtime_tool_scope: RuntimeToolScope = DISABLED_TOOL_SCOPE,
    mcp_servers: list | None = None,
    knowledge_tools: list | None = None,
    max_react_iters: int,
):
    """使用唯一 cache key 创建隔离 Team Member Agent。"""

    team_model_config = replace(
        ctx.runtime_model_config,
        model_options=replace(
            ctx.runtime_model_config.model_options,
            max_react_iters=max_react_iters,
        ),
    )
    spec = build_main_agent_runtime_spec(
        agent_id=f"{ctx.agent_id}:{runtime_key}",
        session_id=session_id,
        agent_name=actor_name,
        system_prompt=system_prompt,
        model_config=team_model_config,
        deep_thinking=False,
        used_skills=used_skills,
        guardrails=ctx.guardrails,
        mcp_servers=mcp_servers or [],
        knowledge_tools=knowledge_tools or [],
        runtime_tool_scope=runtime_tool_scope,
        guardrail_cache_key=_guardrail_cache_key(ctx.guardrails),
        runtime_features_cache_key=f"{runtime_key}-iters-{max_react_iters}",
        extra_tools=extra_tools,
        tool_name_allowlist=tool_allowlist,
    )
    return create_main_agent_from_spec(
        spec,
        on_fallback_event=_make_fallback_trace_recorder(
            session_id=ctx.session_id,
            persist=ctx.persist_run,
            scope="team_member",
            agent_id=actor_id,
            agent_name=actor_name,
        ),
    )


async def _run_agent_stream(
    *,
    agent: object,
    user_msg: UserMsg,
    store: TeamStore,
    actor_id: str,
    task_id: str,
    flush_events: FlushEvents,
    allow_iteration_recovery: bool = False,
    stop_after_tool_name: str = "",
) -> str:
    """把 AgentScope 过程转换为 Team domain event。"""

    output: list[str] = []
    pending_delta_type = ""
    pending_delta_chunks: list[str] = []
    pending_delta_size = 0
    tool_names: dict[str, str] = {}
    last_tool_name = ""
    iteration_recovery_used = False

    async def flush_pending_delta() -> None:
        nonlocal pending_delta_type, pending_delta_chunks, pending_delta_size
        if not pending_delta_chunks:
            return
        store.record_member_event(
            pending_delta_type,
            member_id=actor_id,
            task_id=task_id,
            payload={"delta": "".join(pending_delta_chunks)},
        )
        pending_delta_type = ""
        pending_delta_chunks = []
        pending_delta_size = 0
        await flush_events()

    next_input: object = user_msg
    automatic_denials = 0
    while True:
        resume_event = None
        async for raw_event in agent.reply_stream(next_input):
            data = raw_event.model_dump(mode="json")
            event_type = str(data.pop("type", "UNKNOWN"))
            if event_type in {"REQUIRE_USER_CONFIRM", "REQUIRE_EXTERNAL_EXECUTION"}:
                await flush_pending_delta()
                automatic_denials += 1
                if automatic_denials > MAX_AUTOMATIC_TOOL_DENIALS:
                    raise TeamSessionError(
                        f"Member session 累计请求交互式工具超过 {MAX_AUTOMATIC_TOOL_DENIALS} 次",
                    )
                resume_event = _build_team_tool_denial(event_type, data)
                reason = (
                    "requires_user_confirmation"
                    if event_type == "REQUIRE_USER_CONFIRM"
                    else "requires_external_execution"
                )
                for tool_call in data.get("tool_calls") or []:
                    store.record_member_event(
                        "member.tool.denied",
                        member_id=actor_id,
                        task_id=task_id,
                        status="denied",
                        payload={
                            "toolCallId": str(tool_call.get("id") or ""),
                            "name": str(tool_call.get("name") or "tool"),
                            "reason": reason,
                        },
                    )
                await flush_events()
                break
            if event_type == "EXCEED_MAX_ITERS":
                await flush_pending_delta()
                if allow_iteration_recovery and not iteration_recovery_used:
                    iteration_recovery_used = True
                    _restrict_member_recovery_tools(agent)
                    agent.react_config.max_iters = MEMBER_RECOVERY_MAX_REACT_ITERS
                    store.record_member_event(
                        "member.iteration.recovery",
                        member_id=actor_id,
                        task_id=task_id,
                        status="recovering",
                        payload={
                            "baseLimit": MEMBER_MAX_REACT_ITERS,
                            "recoveryLimit": MEMBER_RECOVERY_MAX_REACT_ITERS,
                            "lastTool": last_tool_name,
                        },
                    )
                    await flush_events()
                    resume_event = UserMsg(
                        name="user",
                        content=(
                            "You reached the Team Member iteration budget. "
                            "Ordinary tools are now unavailable. Stop further research, use the evidence "
                            "already gathered, call team_read_mailbox once, then immediately call "
                            "team_submit_result with the best complete result. Do not narrate or retry "
                            "unavailable tools."
                        ),
                    )
                    continue
                if allow_iteration_recovery:
                    store.record_member_event(
                        "member.iteration.exhausted",
                        member_id=actor_id,
                        task_id=task_id,
                        status="failed",
                        payload={
                            "baseLimit": MEMBER_MAX_REACT_ITERS,
                            "recoveryLimit": MEMBER_RECOVERY_MAX_REACT_ITERS,
                            "lastTool": last_tool_name,
                        },
                    )
                    await flush_events()
                    suffix = f"，最后工具: {last_tool_name}" if last_tool_name else ""
                    raise TeamSessionError(
                        f"Member session 已耗尽 {MEMBER_MAX_REACT_ITERS}+"
                        f"{MEMBER_RECOVERY_MAX_REACT_ITERS} 轮 ReAct 恢复预算{suffix}",
                    )
                raise TeamSessionError("Team session 达到最大 ReAct 轮数")
            if event_type == "TEXT_BLOCK_DELTA":
                delta = str(data.get("delta") or "")
                output.append(delta)
                domain_type = "member.output.delta"
            elif event_type == "THINKING_BLOCK_DELTA":
                delta = str(data.get("delta") or "")
                domain_type = "member.thinking.delta"
            else:
                delta = ""
                domain_type = ""

            if domain_type:
                if pending_delta_type and pending_delta_type != domain_type:
                    await flush_pending_delta()
                pending_delta_type = domain_type
                pending_delta_chunks.append(delta)
                pending_delta_size += len(delta)
                if pending_delta_size >= 240:
                    await flush_pending_delta()
                continue

            await flush_pending_delta()
            recorded = False
            if event_type == "TOOL_CALL_START":
                tool_call_id = str(data.get("tool_call_id") or "")
                tool_name = str(data.get("tool_call_name") or "tool")
                last_tool_name = tool_name
                if tool_call_id:
                    tool_names[tool_call_id] = tool_name
                store.record_member_event(
                    "member.tool.started",
                    member_id=actor_id,
                    task_id=task_id,
                    payload={
                        "toolCallId": tool_call_id,
                        "name": tool_name,
                    },
                )
                recorded = True
            elif event_type == "TOOL_RESULT_END":
                tool_call_id = str(data.get("tool_call_id") or "")
                failed = str(data.get("state") or "").lower() in {"error", "failed", "denied"}
                completed_tool_name = str(
                    data.get("tool_call_name") or tool_names.get(tool_call_id) or "tool",
                )
                store.record_member_event(
                    "member.tool.failed" if failed else "member.tool.completed",
                    member_id=actor_id,
                    task_id=task_id,
                    status="failed" if failed else "completed",
                    payload={
                        "toolCallId": tool_call_id,
                        "name": completed_tool_name,
                        "state": str(data.get("state") or ""),
                    },
                )
                recorded = True
            if recorded:
                await flush_events()
            if (
                event_type == "TOOL_RESULT_END"
                and not failed
                and stop_after_tool_name
                and completed_tool_name == stop_after_tool_name
            ):
                return "".join(output)
        if resume_event is None:
            break
        next_input = resume_event
    await flush_pending_delta()
    return "".join(output)


def _restrict_member_recovery_tools(agent: object) -> None:
    """恢复轮只保留 Team 协作工具，禁止继续扩散检索或产生副作用。"""

    toolkit = getattr(agent, "toolkit", None)
    for group in getattr(toolkit, "tool_groups", []):
        group.tools = [
            tool
            for tool in group.tools
            if str(getattr(tool, "name", "")) in MEMBER_RECOVERY_TOOLS
        ]
        group.mcps = []
        group.skills_or_loaders = []


def _build_team_tool_denial(
    event_type: str,
    data: dict,
) -> UserConfirmResultEvent | ExternalExecutionResultEvent:
    """构建 Team Member 交互式工具的拒绝恢复事件。"""

    tool_calls = [ToolCallBlock.model_validate(item) for item in data.get("tool_calls") or []]
    reply_id = str(data.get("reply_id") or "")
    if event_type == "REQUIRE_USER_CONFIRM":
        return UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=[
                ConfirmResult(confirmed=False, tool_call=tool_call, rules=None)
                for tool_call in tool_calls
            ],
        )
    return ExternalExecutionResultEvent(
        reply_id=reply_id,
        execution_results=[
            ToolResultBlock(
                id=tool_call.id,
                name=tool_call.name,
                output=[TextBlock(text="Team Member 不允许暂停等待人工处理，该工具已拒绝。")],
                state=ToolResultState.DENIED,
            )
            for tool_call in tool_calls
        ],
    )


def _leader_system_prompt(store: TeamStore, run_data: ChatMessage) -> str:
    return "\n".join([
        "You are the coordination-only Leader of a one-off TeamRun.",
        f"TeamRun goal: {run_data.message}",
        "Create temporary members with names, roles, capability boundaries, and dynamic runtime prompts tailored to this goal.",
        "Create tasks using exact returned task IDs for dependencies.",
        "Model communication prerequisites explicitly: if A must message B before B can work, create A's communication task first and make B depend on it.",
        "Never create a task that waits for a member whose own task is blocked by that waiting task.",
        "Do not perform deliverable work yourself and do not answer the user.",
        "When member creation and task planning are complete, call team_finish_coordination.",
        "After team_finish_coordination succeeds, do not poll tasks or mailbox; the runtime starts members mechanically.",
        f"TeamRun id: {store.ref.id}",
    ])


def _member_system_prompt(store: TeamStore, member: dict, task: dict) -> str:
    return "\n".join([
        "You are an isolated temporary TeamRun member.",
        f"TeamRun id: {store.ref.id}",
        f"Member id: {member.get('id')}",
        f"Role: {member.get('role')}",
        f"Current task: {task.get('title')} ({task.get('id')})",
        "Dynamic runtime prompt:",
        str(member.get("runtimePrompt") or ""),
        "Read shared context only through team_read_tasks and team_read_mailbox.",
        "Communicate only through team_send_mail.",
        "Interactive tools are denied in TeamRun; if one is denied, continue with available tools and submit the result.",
        "Call team_read_mailbox immediately before team_submit_result and incorporate every new inbound message.",
        "The only successful task exit is team_submit_result with a complete non-empty result.",
        "Do not ask the user directly and do not claim another task.",
    ])


def _member_user_prompt(store: TeamStore, member_id: str, task: dict) -> str:
    inbox = store.read_inbox(member_id)[-20:]
    return "\n\n".join([
        f"Execute task {task.get('id')}: {task.get('title')}",
        "Task brief:",
        store.task_brief(str(task["id"])),
        "Mailbox:",
        str(inbox),
    ])


def _synthesis_system_prompt() -> str:
    return "\n".join([
        "You are the final synthesis stage of a completed TeamRun.",
        "Use only the supplied successful task results and mailbox evidence.",
        "Answer the user's goal directly with one polished final answer.",
        "Do not mention internal IDs, coordination steps, task states, or mailbox routing unless asked.",
        "Do not call tools.",
    ])


def _synthesis_user_prompt(store: TeamStore, goal: str) -> str:
    evidence = [{
        "title": task.get("title"),
        "result": store.task_result(str(task["id"])),
    } for task in store.tasks() if task.get("status") == "succeeded"]
    mailbox = store.mailbox()[-100:]
    return f"User goal:\n{goal}\n\nSuccessful Team results:\n{evidence}\n\nTeam Mailbox:\n{mailbox}"
