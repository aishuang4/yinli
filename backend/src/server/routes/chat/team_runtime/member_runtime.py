"""Team Member 独立 Agent session 运行器。

本模块直接消费 AgentScope 事件，不复用 Chat Leader 的阶段、HITL 或最终答案流。
"""

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace

from agentscope.message import UserMsg

from src.agents.factory import RuntimeToolScope

logger = logging.getLogger(__name__)
from src.agents.runtime_types import MAX_REACT_ITERS
from src.server.routes.chat.hitl import build_automatic_confirm_resume
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.run_context import ChatRunContext
from src.server.routes.chat.prompts import compose_turn_user_text
from src.server.routes.chat.runtime import _build_user_content, _guardrail_cache_key
from src.server.routes.chat.runtime_events import (
    agentscope_reply_error_message,
    agentscope_reply_finished_reason,
)
from src.server.routes.chat.runtime_builder import build_main_agent_runtime_spec, create_main_agent_from_spec
from src.runtime.model_call_trace import observe_model_call_event
from src.server.routes.chat.runtime_governance import (
    build_context_governance_middlewares,
    build_actor_memory_prompt,
    prepare_agent_governance,
)
from src.server.routes.chat.team_runtime.store import (
    MAX_TASK_RESULT_CHARS,
    MAX_TEAM_MEMBERS,
    MAX_TEAM_TASKS,
    TERMINAL_TASK_STATUSES,
    TeamStore,
)
from src.server.routes.chat.team_runtime.tools import leader_tools, member_tools
from src.server.routes.chat.trace_recorder import _make_fallback_trace_recorder


FlushEvents = Callable[[], Awaitable[None]]
DISABLED_TOOL_SCOPE = RuntimeToolScope(
    allow_mcp=False,
    allow_knowledge=False,
    allow_bash=False,
    allow_http=False,
    allow_web=False,
)
MAX_AUTOMATIC_TOOL_DENIALS = 8
# 长任务靠工具指纹停死循环，不用小轮次把协调或成员执行打断。512 只兜住异常空转。
LEADER_MAX_REACT_ITERS = MAX_REACT_ITERS
MEMBER_MAX_REACT_ITERS = MAX_REACT_ITERS
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
    # Leader 复用 Chat 已算好的记忆两段：Markdown 摘要进 system prompt，本轮召回附在目标后面。
    agent = _create_team_agent(
        ctx=ctx,
        actor_id="leader",
        actor_name=ctx.agent_name,
        session_id=f"{ctx.session_id}:{store.ref.id}:leader",
        system_prompt=_with_memory_prompt(_leader_system_prompt(store, run_data), ctx.memory_prompt),
        runtime_key=f"team-leader-{store.ref.id}",
        extra_tools=tools,
        tool_allowlist=allowlist,
        used_skills=list(getattr(ctx, "used_skills", None) or []),
        max_react_iters=LEADER_MAX_REACT_ITERS,
        skill_context=bool(getattr(ctx, "used_skills", None)),
    )
    try:
        await _run_agent_stream(
            agent=agent,
            user_msg=UserMsg(
                name="user",
                content=_build_user_content(
                    _leader_user_text(ctx, run_data),
                    run_data.images,
                ),
            ),
            store=store,
            actor_id="leader",
            task_id="",
            flush_events=flush_events,
            stop_after_tool_name="team_finish_coordination",
            on_model_call=_model_call_observer(ctx, scope="team_leader", agent_id="leader", agent_name=ctx.agent_name),
        )
    except TeamSessionError:
        if not _finish_coordination_if_ready(store, reason="Leader session 在调用 team_finish_coordination 前结束"):
            raise
    if store.run().get("phase") != "executing":
        if not _finish_coordination_if_ready(store, reason="Leader 未调用 team_finish_coordination"):
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
        "WebFetch",
        "timeline_retrieve",
        "timeline_ingest",
        *(str(tool.name) for tool in ctx.knowledge_tools),
        *(tool.name for tool in tools),
    }
    actor_name = str(member.get("name") or member_id)
    retry_count = int(task.get("retryCount") or 0)
    # 每个成员只能写自己的目录：并行成员不会互相覆盖文件；结果通过 result 里的路径交给综合阶段。
    write_dir = _member_write_dir(ctx, store, member_id)
    member_skills = _member_runtime_skills(ctx)
    member_agent_id = str(member.get("agentId") or "") or f"team-member:{member_id}"
    member_memory_prompt = build_actor_memory_prompt(
        user_id="default",
        agent_id=member_agent_id,
        session_id=f"{ctx.session_id}:{member_id}",
    )
    agent = _create_team_agent(
        ctx=ctx,
        actor_id=member_id,
        actor_name=actor_name,
        session_id=f"{ctx.session_id}:{store.ref.id}:{member_id}:r{retry_count}",
        system_prompt=_with_memory_prompt(
            _member_system_prompt(store, member, task, write_dir=write_dir),
            member_memory_prompt,
        ),
        # 重派后的 key 不同：失败那次的实例（及其上下文）不复用。
        runtime_key=f"team-member-{store.ref.id}-{member_id}-{task_id}-r{retry_count}",
        write_scope_dir=write_dir,
        extra_tools=tools,
        tool_allowlist=allowlist,
        used_skills=member_skills,
        runtime_tool_scope=ctx.runtime_tool_scope,
        mcp_servers=ctx.mcp_servers,
        knowledge_tools=ctx.knowledge_tools,
        max_react_iters=MEMBER_MAX_REACT_ITERS,
        skill_context=bool(member_skills),
    )
    await _run_agent_stream(
        agent=agent,
        user_msg=UserMsg(
            name="user",
            content=_build_user_content(
                _member_turn_text(ctx, store, member_id, task, run_data),
                run_data.images,
            ),
        ),
        store=store,
        actor_id=member_id,
        task_id=task_id,
        flush_events=flush_events,
        allow_iteration_recovery=True,
        on_model_call=_model_call_observer(ctx, scope="team_member", agent_id=member_id, agent_name=actor_name),
    )
    if store.task(task_id).get("status") != "succeeded":
        raise TeamSessionError("Member session 结束时没有通过 team_submit_result 完成当前任务")


async def run_synthesis(
    *,
    store: TeamStore,
    ctx: ChatRunContext,
    run_data: ChatMessage,
    flush_events: FlushEvents,
    on_output_delta: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """使用独立无工具 session 综合最终答案。"""

    tasks = store.tasks()
    unfinished = [task for task in tasks if task.get("status") not in TERMINAL_TASK_STATUSES]
    if unfinished:
        raise TeamSessionError("存在未结束任务，不能进入综合阶段")
    if not any(task.get("status") == "succeeded" for task in tasks):
        raise TeamSessionError("没有任何成功任务，不能进入综合阶段")
    store.set_phase("synthesizing")
    store.set_member_status("leader", "running")
    await flush_events()
    agent = _create_team_agent(
        ctx=ctx,
        actor_id="leader",
        actor_name=ctx.agent_name,
        session_id=f"{ctx.session_id}:{store.ref.id}:synthesis",
        # 综合阶段没有工具，但用户的格式 / 语言偏好（Markdown 记忆）仍应生效。
        system_prompt=_with_memory_prompt(_synthesis_system_prompt(), ctx.memory_prompt),
        runtime_key=f"team-synthesis-{store.ref.id}",
        extra_tools=[],
        tool_allowlist=set(),
        used_skills=[],
        max_react_iters=SYNTHESIS_MAX_REACT_ITERS,
        skill_context=False,
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
        on_output_delta=on_output_delta,
        on_model_call=_model_call_observer(ctx, scope="team_synthesis", agent_id="leader", agent_name=ctx.agent_name),
    )
    if not text.strip():
        raise TeamSessionError("综合 session 没有生成最终答案")
    store.set_member_status("leader", "idle")
    await flush_events()
    return text.strip()


def _member_runtime_skills(ctx: ChatRunContext) -> list:
    """Member 只保留 Leader Agent profile 里点名的技能，不吃共享开关。

    用户本轮在发送框强制开启的 Skill 例外，随任务一起带给 Member。
    """

    configured = {
        str(item)
        for item in list(getattr(getattr(ctx, "agent_config", None), "skills", None) or [])
    }
    forced = {
        str(getattr(skill, "id", "") or "")
        for skill in list(getattr(ctx, "forced_skills", None) or [])
    }
    allowed = configured | {item for item in forced if item}
    return [
        skill
        for skill in list(getattr(ctx, "used_skills", None) or [])
        if getattr(skill, "id", "") in allowed
    ]


def _skill_paths_for(ctx: ChatRunContext, used_skills: list) -> list[str]:
    """Skill 目录路径与本次实际挂上的 used_skills 对齐。"""

    if not used_skills:
        return []
    used_ids = {getattr(skill, "id", "") for skill in used_skills}
    context_ids = {getattr(skill, "id", "") for skill in list(getattr(ctx, "used_skills", None) or [])}
    if used_ids == context_ids:
        return list(getattr(ctx, "skill_paths", None) or [])
    from src.server.routes.skills import resolve_runtime_skill_paths

    return resolve_runtime_skill_paths(used_skills, runtime_context=getattr(ctx, "runtime_context", None))


def _child_platform_binding(ctx: ChatRunContext, *, actor_id: str, write_scope_dir: str = ""):
    """Team Leader / Member 繼承父 Run，工具範圍只會再收窄。"""

    parent = getattr(ctx, "platform_binding", None)
    if parent is None:
        return None, {}
    from src.runtime.authorization import inherit_child_authorization, open_run_binding

    parent_scope = str(getattr(ctx, "platform_filesystem", "") or "")
    child_scope = write_scope_dir or parent_scope
    child_owner, denied = inherit_child_authorization(
        parent.owner,
        actor_id=actor_id,
        parent_denied=frozenset(getattr(ctx, "platform_denied", frozenset()) or ()),
        parent_filesystem_scope=parent_scope,
        child_filesystem_scope=child_scope,
    )
    holder: dict = {}

    async def names():
        agent = holder.get("agent")
        if agent is None or not hasattr(agent, "available_tool_names"):
            return frozenset()
        return await agent.available_tool_names()

    parent_reader = getattr(getattr(parent.service, "boundary_check", None), "capabilities", None)
    binding = open_run_binding(
        child_owner,
        filesystem_scope=parent_scope or child_scope or "/",
        denied_tool_names=denied,
        tool_names=names,
        parent_capabilities=parent_reader if callable(parent_reader) else None,
        share_denials_from=parent.service,
    )
    return binding, holder


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
    skill_context: bool = False,
    write_scope_dir: str = "",
):
    """使用唯一 cache key 创建隔离 Team Member Agent，并挂上与单 Agent 相同的上下文治理 middleware。"""

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
        skill_paths=_skill_paths_for(ctx, used_skills),
        guardrails=ctx.guardrails,
        mcp_servers=mcp_servers or [],
        knowledge_tools=knowledge_tools or [],
        runtime_tool_scope=runtime_tool_scope,
        guardrail_cache_key=_guardrail_cache_key(ctx.guardrails),
        runtime_features_cache_key=f"{runtime_key}-iters-{max_react_iters}",
        extra_tools=extra_tools,
        tool_name_allowlist=tool_allowlist,
        middlewares=build_context_governance_middlewares(skill_context=skill_context),
        workspace_dir=str(getattr(ctx, "session_workspace_dir", "") or ""),
        write_scope_dir=write_scope_dir,
        extra_filesystem_scopes=list(getattr(ctx, "extra_filesystem_scopes", None) or []),
    )
    platform_binding, tool_holder = _child_platform_binding(
        ctx,
        actor_id=actor_id,
        write_scope_dir=write_scope_dir,
    )
    agent = create_main_agent_from_spec(
        spec,
        on_fallback_event=_make_fallback_trace_recorder(
            session_id=ctx.session_id,
            persist=ctx.persist_run,
            scope="team_member",
            agent_id=actor_id,
            agent_name=actor_name,
        ),
        platform_binding=platform_binding,
    )
    if platform_binding is not None:
        tool_holder["agent"] = agent
    memory_chars = len(str(getattr(ctx, "memory_prompt", "") or ""))
    prepare_agent_governance(
        agent,
        session_id=session_id,
        prompt_sections={"team": max(0, len(system_prompt) - memory_chars), "memory": memory_chars},
    )
    return agent


def _with_memory_prompt(system_prompt: str, memory_prompt: str) -> str:
    return f"{system_prompt}\n\n{memory_prompt}" if memory_prompt else system_prompt


def _model_call_observer(ctx: ChatRunContext, *, scope: str, agent_id: str, agent_name: str):
    """给 `_run_agent_stream` 的模型调用回调：写 CONTEXT_COMPRESSED / CONTEXT_USAGE trace。"""

    async def observe(agent: object, event_type: str, event_data: dict) -> None:
        try:
            await observe_model_call_event(
                agent=agent,
                event_type=event_type,
                event_data=event_data,
                session_id=ctx.session_id,
                persist=ctx.persist_run,
                scope=scope,
                agent_id=agent_id,
                agent_name=agent_name,
            )
        except Exception:
            # 观测失败不能影响 Team 执行，但原因要留在日志里。
            logger.exception("Team 模型调用观测失败")
            return

    return observe


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
    on_output_delta: Callable[[str], Awaitable[None]] | None = None,
    on_model_call: Callable[[object, str, dict], Awaitable[None]] | None = None,
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
            if on_model_call is not None and event_type in {"MODEL_CALL_START", "MODEL_CALL_END"}:
                await on_model_call(agent, event_type, data)
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
            if event_type == "REPLY_END":
                finished_reason = agentscope_reply_finished_reason(data)
                recovering_from_max_iters = (
                    finished_reason == "exceed_max_iters"
                    and resume_event is not None
                    and iteration_recovery_used
                )
                if finished_reason != "completed" and not recovering_from_max_iters:
                    detail = agentscope_reply_error_message(data)
                    suffix = f"：{detail}" if detail else ""
                    raise TeamSessionError(
                        f"Team session 异常结束（{finished_reason}）{suffix}",
                    )
            if event_type == "TEXT_BLOCK_DELTA":
                delta = str(data.get("delta") or "")
                output.append(delta)
                if delta and on_output_delta is not None:
                    await on_output_delta(delta)
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
):
    """构建 Team Member 交互式工具的拒绝恢复事件。决策始终 deny。"""

    return build_automatic_confirm_resume(
        event_type=event_type,
        reply_id=str(data.get("reply_id") or ""),
        tool_calls=[dict(item) for item in (data.get("tool_calls") or []) if isinstance(item, dict)],
        default_confirmed=False,
        denial_message="Team Member 不允许暂停等待人工处理，该工具已拒绝。",
    )


def _coordination_ready(store: TeamStore) -> bool:
    """已有临时成员和至少一条任务时，协调阶段可以收口。"""

    members = [member for member in store.members() if member.get("id") != "leader"]
    return bool(members) and bool(store.tasks())


def _finish_coordination_if_ready(store: TeamStore, *, reason: str) -> bool:
    """Leader 没显式收口、但任务图已经能跑时，自动进入 executing，避免整轮在协调阶段失败。"""

    if store.run().get("phase") == "executing":
        return True
    if not _coordination_ready(store):
        return False
    store.set_phase("executing")
    store.record_member_event(
        "member.iteration.recovery",
        member_id="leader",
        status="recovering",
        payload={"reason": reason, "autoFinish": True},
    )
    return True


def _leader_system_prompt(store: TeamStore, run_data: ChatMessage) -> str:
    return "\n".join([
        "You are the coordination-only Leader of a one-off TeamRun.",
        f"TeamRun goal: {run_data.message}",
        "Create temporary members with names, roles, capability boundaries, and dynamic runtime prompts tailored to this goal.",
        f"Hard limits: at most {MAX_TEAM_MEMBERS} members and {MAX_TEAM_TASKS} tasks. If the user asks for more people, assign multiple tasks to one member instead of spawning extra members.",
        "Prefer multiple tool calls in one turn. Create one task per user-requested role; do not invent extra tasks.",
        "Create tasks using exact returned task IDs for dependencies.",
        "Model communication prerequisites explicitly: if A must message B before B can work, create A's communication task first and make B depend on it.",
        "Never create a task that waits for a member whose own task is blocked by that waiting task.",
        "Do not perform deliverable work yourself and do not answer the user.",
        "As soon as the required members and tasks exist, call team_finish_coordination in the same turn. Do not list, poll, or keep creating tasks.",
        "After team_finish_coordination succeeds, do not poll tasks or mailbox; the runtime starts members mechanically.",
        f"TeamRun id: {store.ref.id}",
    ])


def _member_write_dir(ctx: ChatRunContext, store: TeamStore, member_id: str) -> str:
    """成员私有可写目录：<session workspace>/team/<run>/<member>，不存在则创建。"""

    base = str(getattr(ctx, "session_workspace_dir", "") or "")
    if not base:
        return ""
    from pathlib import Path

    directory = Path(base) / "team" / store.ref.id / member_id
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return ""
    return str(directory)


def _member_system_prompt(store: TeamStore, member: dict, task: dict, *, write_dir: str = "") -> str:
    lines = [
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
        f"The only successful task exit is team_submit_result with a complete non-empty result (max {MAX_TASK_RESULT_CHARS} chars; put longer deliverables in files and reference their paths).",
        "Do not ask the user directly and do not claim another task.",
    ]
    if write_dir:
        lines.append(
            f"Write / Edit are restricted to your private directory: {write_dir}. Other members run in parallel; "
            "never assume you can modify shared project files. Save deliverables there and list the paths in your result."
        )
    return "\n".join(lines)


def _leader_user_text(ctx: ChatRunContext, run_data: ChatMessage) -> str:
    """Leader 看到的是同一條用戶消息：有 Skill 時總頭在前，團隊任務說明接在後面。"""

    skill_message = str(getattr(ctx, "forced_skill_message", "") or "")
    if skill_message:
        goal = f"为本轮目标组建团队并创建共享任务。\n\n{skill_message}"
    else:
        goal = f"为本轮目标组建团队并创建共享任务：\n{run_data.message}"
    return compose_turn_user_text(
        message=goal,
        note=str(getattr(ctx, "user_message_note", "") or ctx.memory_recall_text),
    )


def _member_turn_text(ctx: ChatRunContext, store: TeamStore, member_id: str, task: dict, _run_data: ChatMessage) -> str:
    """Member 的任務說明接在同一份 Skill 總頭後面，避免再抄一份截斷正文。"""

    task_text = _member_user_prompt(store, member_id, task)
    skill_message = str(getattr(ctx, "forced_skill_message", "") or "")
    body = f"{skill_message}\n\n{task_text}" if skill_message else task_text
    return compose_turn_user_text(
        message=body,
        note=str(getattr(ctx, "user_message_note", "") or ""),
    )


def _member_user_prompt(store: TeamStore, member_id: str, task: dict) -> str:
    inbox = store.read_inbox(member_id)[-20:]
    parts = [
        f"Execute task {task.get('id')}: {task.get('title')}",
        "Task brief:",
        store.task_brief(str(task["id"])),
    ]
    if int(task.get("retryCount") or 0) > 0 and task.get("lastReviewReason"):
        parts.extend([
            "This is a retry. Previous attempt outcome:",
            str(task.get("lastReviewReason") or ""),
            "Avoid the same failure path; if a tool keeps failing, deliver the best result with available evidence.",
        ])
    parts.extend(["Mailbox:", json.dumps(inbox, ensure_ascii=False, default=str)])
    return "\n\n".join(parts)


def _synthesis_system_prompt() -> str:
    return "\n".join([
        "You are the final synthesis stage of a completed TeamRun.",
        "Use only the supplied successful task results and mailbox evidence.",
        "Answer the user's goal directly with one polished final answer.",
        "If some tasks failed, say so briefly at the end: which part of the goal is not covered and why, "
        "so the user can decide whether to retry. Never invent content for failed tasks.",
        "Do not mention internal IDs, coordination steps, task states, or mailbox routing unless asked.",
        "Do not call tools.",
    ])


# 综合输入的字符预算：结果正文与邮件分别裁剪，避免把整份报告和 100 封信一起塞进无工具的综合 session。
SYNTHESIS_RESULT_CHARS = MAX_TASK_RESULT_CHARS
SYNTHESIS_MAILBOX_LIMIT = 40
SYNTHESIS_MAIL_CHARS = 600


def _synthesis_user_prompt(store: TeamStore, goal: str) -> str:
    tasks = store.tasks()
    evidence = [
        {
            "title": task.get("title"),
            "result": store.task_result(str(task["id"]))[:SYNTHESIS_RESULT_CHARS],
        }
        for task in tasks
        if task.get("status") == "succeeded"
    ]
    failed = [
        {
            "title": task.get("title"),
            "status": task.get("status"),
            "reason": str(task.get("failureReason") or "")[:300],
        }
        for task in tasks
        if task.get("status") != "succeeded"
    ]
    mailbox = [
        {
            "from": mail.get("sender") or mail.get("from"),
            "to": mail.get("recipient") or mail.get("to"),
            "kind": mail.get("kind"),
            "subject": mail.get("subject"),
            "content": str(mail.get("content") or "")[:SYNTHESIS_MAIL_CHARS],
        }
        for mail in store.mailbox()[-SYNTHESIS_MAILBOX_LIMIT:]
        # 任务结果邮件与 evidence 重复，不再抄一遍。
        if str(mail.get("kind") or "") != "result"
    ]
    payload = {
        "userGoal": goal,
        "successfulResults": evidence,
        "failedOrCancelledTasks": failed,
        "mailbox": mailbox,
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)
