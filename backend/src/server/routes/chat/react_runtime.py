"""Chat ReAct Runtime。

本模块只负责 ReAct 架构下的执行链路：
1. Team executor 交给独立 Team Runtime。
2. 单 Agent executor 创建或复用主 Agent。
3. 消费 AgentScope 流式事件并完成成功/失败收尾。

第一层 architecture 路由和第二层 executor 选择均在进入本模块前完成。
"""

from typing import AsyncGenerator

from agentscope.message import UserMsg

from src.server.routes.chat.background_runs import current_chat_background_run_id
from src.server.routes.chat.completion import (
    emit_failed_startup_run,
    finalize_successful_leader_run,
)
from src.server.routes.chat.leader_execution import execute_and_finalize_leader_reply
from src.server.routes.chat.leader_stream import LeaderStreamState
from src.server.routes.chat.models import ChatMessage
from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.plan_mode.middleware import PlanModeMiddleware
from src.server.routes.chat.plan_mode.tools import build_plan_mode_tools
from src.server.routes.chat.runtime import _build_user_content, _guardrail_cache_key
from src.server.routes.chat.run_context import ChatRunContext
from src.server.routes.chat.scheduled_runtime import (
    SCHEDULED_JOB_UNATTENDED_DENYLIST,
    scheduled_job_unattended_feature_key,
)
from src.server.routes.chat.team_runtime import execute_team_runtime
from src.server.routes.chat.runtime_builder import (
    build_main_agent_runtime_spec,
    create_main_agent_from_spec,
)
from src.server.routes.chat.trace_recorder import _make_fallback_trace_recorder
from src.server.routes.chat.runtime_events import runtime_source_event
from src.subagents.manager import SubagentDelegationManager
from src.subagents.registry import load_subagent_catalog
from src.tools.delegate_task import build_subagent_tools
from src.tools.scheduled_job_tools import (
    build_scheduled_job_tools,
    scheduled_job_prompt_addendum,
    scheduled_job_tools_cache_key,
)


def _build_plan_mode_runtime_parts(
    ctx: ChatRunContext,
    run_data: ChatMessage,
) -> tuple[PlanModeManager, str, list[object], list[object]]:
    """构造 Plan Mode middleware 和控制工具。"""

    manager = PlanModeManager(
        session_id=ctx.session_id,
        run_id=current_chat_background_run_id() or "",
        agent_id=ctx.agent_id,
        agent_name=ctx.agent_name,
        persist=ctx.persist_run,
        plan_id=run_data.plan_id or "",
        action=run_data.plan_action or "",
        feedback=run_data.plan_feedback,
        requested=run_data.plan_requested,
    )
    runtime_features_cache_key = f"plan-mode-v2:{current_chat_background_run_id()}"
    tools = build_plan_mode_tools(manager)
    middlewares = [PlanModeMiddleware(manager)]
    return manager, runtime_features_cache_key, tools, middlewares


def _build_runtime_feature_parts(
    ctx: ChatRunContext,
    run_data: ChatMessage,
) -> tuple[PlanModeManager | None, str, list[object], list[object], list[object], str]:
    """构造主 Agent 运行时增强能力。

    Team Runtime 有独立的 TeamRun / Task / Mailbox 控制面，当前不复用
    单 Agent Plan Mode 或 ScheduleJob 工具。scheduled firing 使用
    `scheduled-job-unattended`，不得走本函数注入登记工具。
    """

    feature_keys: list[str] = []
    extra_tools: list[object] = []
    middlewares: list[object] = []
    runtime_event_sources: list[object] = []
    prompt_addenda: list[str] = []
    plan_mode_manager: PlanModeManager | None = None
    is_team = ctx.intent_route.executor.kind == "team"

    if getattr(ctx, "trigger_source", "user") == "scheduled_job":
        denylist = set(getattr(ctx, "tool_name_denylist", None) or SCHEDULED_JOB_UNATTENDED_DENYLIST)
        return (
            None,
            scheduled_job_unattended_feature_key(denylist),
            [],
            [],
            [],
            "",
        )

    if is_team:
        pass
    elif run_data.plan_requested or run_data.plan_action:
        plan_mode_manager, plan_key, plan_tools, plan_middlewares = _build_plan_mode_runtime_parts(ctx, run_data)
        feature_keys.append(plan_key)
        extra_tools.extend(plan_tools)
        middlewares.extend(plan_middlewares)

    if not is_team:
        job_tools = build_scheduled_job_tools(
            workspace_id=str(ctx.runtime_context.workspace_id),
            project_id=str(ctx.runtime_context.project_id),
            agent_id=ctx.agent_id,
            session_id=ctx.session_id,
        )
        extra_tools.extend(job_tools)
        feature_keys.append(scheduled_job_tools_cache_key(job_tools))
        prompt_addenda.append(scheduled_job_prompt_addendum())

    catalog = load_subagent_catalog(workspace_dir=str(ctx.runtime_context.project_root))
    if catalog.definitions:
        subagent_manager = SubagentDelegationManager(
            catalog=catalog,
            session_id=ctx.session_id,
            run_id=current_chat_background_run_id() or "",
            parent_scope="leader" if is_team else "single",
            parent_agent_id=ctx.agent_id,
            parent_agent_name=ctx.agent_name,
            model_name=ctx.runtime_model_config.model_name,
            fallback_names=ctx.runtime_model_config.fallback_names,
            model_options=ctx.runtime_model_config.model_options,
            guardrails=ctx.guardrails,
            mcp_servers=ctx.mcp_servers,
            knowledge_tools=ctx.knowledge_tools,
            parent_tool_scope=ctx.runtime_tool_scope,
            persist=ctx.persist_run,
            runtime_context=ctx.runtime_context,
        )
        extra_tools.extend(build_subagent_tools(subagent_manager))
        runtime_event_sources.append(subagent_manager)
        feature_keys.append(f"subagents-{catalog.fingerprint}")
        prompt_addenda.append(_build_subagent_prompt_addendum(catalog.definitions))

    return (
        plan_mode_manager,
        ":".join(feature_keys) if feature_keys else "none",
        extra_tools,
        middlewares,
        runtime_event_sources,
        "\n".join(part for part in prompt_addenda if part),
    )


def _build_subagent_prompt_addendum(definitions: dict[str, object]) -> str:
    """生成主 Agent 可见的 Subagent 摘要提示。"""

    lines = [
        "",
        "Subagent delegation:",
        "You may call DelegateTask to fork self-contained attached tasks in independent contexts.",
        "DelegateTask returns an invocationId immediately; it never returns the child result.",
        "You may fork multiple independent tasks before continuing useful parent work.",
        "Before finalizing, call SubagentTaskWait for all attached tasks, then call SubagentResultRead once for every terminal invocation.",
        "A failed, cancelled, or interrupted child result must also be read and considered.",
        "Do not claim completion while any attached task is queued, running, waiting, or unconsumed.",
        "Available subagents:",
    ]
    for name, definition in sorted(definitions.items()):
        description = str(getattr(definition, "description", "") or "")
        lines.append(f"- {name}: {description}")
    lines.append("Only delegate when result.md can contain the complete information the parent needs.")
    return "\n".join(lines)


async def execute_react_runtime(
    *,
    ctx: ChatRunContext,
    run_data: ChatMessage,
) -> AsyncGenerator[str, None]:
    """执行 ReAct Runtime 并输出 SSE 事件。"""

    active_skill_run = ctx.active_skill_run
    system_prompt = ctx.system_prompt
    stream_scope = "leader" if ctx.intent_route.executor.kind == "team" else "single_agent"
    plan_mode_manager: PlanModeManager | None = None

    try:
        if ctx.intent_route.executor.kind == "team":
            async for team_event in execute_team_runtime(ctx=ctx, run_data=run_data):
                yield team_event
            return

        (
            plan_mode_manager,
            runtime_features_cache_key,
            extra_tools,
            middlewares,
            runtime_event_sources,
            runtime_prompt_addendum,
        ) = _build_runtime_feature_parts(ctx, run_data)
        if plan_mode_manager and plan_mode_manager.is_cancelled_action():
            runtime_events = plan_mode_manager.drain_runtime_events()
            for runtime_event in runtime_events:
                yield runtime_source_event(runtime_event)
            async for completion_event in finalize_successful_leader_run(
                session_id=ctx.session_id,
                persist=ctx.persist_run,
                agent_id=ctx.agent_id,
                agent_name=ctx.agent_name,
                model_name=ctx.model_name,
                user_message=ctx.effective_user_message,
                assistant_text="计划已取消。",
                assistant_thinking="",
                assistant_tool_calls=[],
                assistant_model_caches=[],
                runtime_events=runtime_events,
                skill_runs=ctx.skill_runs,
                active_skill_run=ctx.active_skill_run,
                runtime_context=ctx.runtime_context,
                session_workspace_dir=ctx.session_workspace_dir,
                session_artifact_paths_before=ctx.session_artifact_paths_before,
                sandbox=run_data.sandbox,
            ):
                yield completion_event
            return
        if runtime_prompt_addendum:
            system_prompt = f"{system_prompt}\n\n{runtime_prompt_addendum}"
        main_agent_spec = build_main_agent_runtime_spec(
            agent_id=ctx.agent_id,
            session_id=ctx.session_id,
            agent_name=ctx.agent_name,
            system_prompt=system_prompt,
            model_config=ctx.runtime_model_config,
            deep_thinking=run_data.deep_thinking,
            used_skills=ctx.used_skills,
            skill_paths=ctx.skill_paths,
            guardrails=ctx.guardrails,
            mcp_servers=ctx.mcp_servers,
            knowledge_tools=ctx.knowledge_tools,
            runtime_tool_scope=ctx.runtime_tool_scope,
            guardrail_cache_key=_guardrail_cache_key(ctx.guardrails),
            runtime_features_cache_key=runtime_features_cache_key,
            extra_tools=extra_tools,
            tool_name_denylist=(
                set(getattr(ctx, "tool_name_denylist", None) or set())
                | (set(SCHEDULED_JOB_UNATTENDED_DENYLIST) if getattr(ctx, "trigger_source", "user") == "scheduled_job" else set())
            ),
            middlewares=middlewares,
            workspace_dir=ctx.session_workspace_dir,
            write_scope_dir=str(getattr(ctx, "write_scope_dir", "") or ""),
        )
        agent = create_main_agent_from_spec(
            main_agent_spec,
            on_fallback_event=_make_fallback_trace_recorder(
                session_id=ctx.session_id,
                persist=ctx.persist_run,
                scope=stream_scope,
                agent_id=ctx.agent_id,
                agent_name=ctx.agent_name,
            ),
        )
    except Exception as exc:
        assistant_error = f"Agent 创建失败: {exc}"
        if plan_mode_manager:
            plan_mode_manager.fail_run(reason=assistant_error)
            for runtime_event in plan_mode_manager.drain_runtime_events():
                yield runtime_source_event(runtime_event)
        async for failure_event in emit_failed_startup_run(
            session_id=ctx.session_id,
            persist=ctx.persist_run,
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
            model_name=ctx.model_name,
            assistant_error=assistant_error,
            skill_runs=ctx.skill_runs,
            sandbox=run_data.sandbox,
        ):
            yield failure_event
        return

    user_msg = UserMsg(
        name="user",
        content=_build_user_content(
            ctx.effective_user_message,
            run_data.images,
        ),
    )

    leader_stream_state = LeaderStreamState(
        active_skill_run=active_skill_run,
        available_skill_details=ctx.used_skill_details,
        session_workspace_dir=ctx.session_workspace_dir,
        session_artifact_paths_before=ctx.session_artifact_paths_before,
        trigger_source=str(getattr(ctx, "trigger_source", "user") or "user"),
    )
    if plan_mode_manager:
        for runtime_event in plan_mode_manager.drain_runtime_events():
            leader_stream_state.runtime_events.append(runtime_event)
            yield runtime_source_event(runtime_event)
    async for event in execute_and_finalize_leader_reply(
        agent=agent,
        input_event=user_msg,
        state=leader_stream_state,
        session_id=ctx.session_id,
        persist=ctx.persist_run,
        agent_id=ctx.agent_id,
        agent_name=ctx.agent_name,
        model_name=ctx.model_name,
        guardrails=ctx.guardrails,
        user_message=ctx.effective_user_message,
        stream_scope=stream_scope,
        skill_runs=ctx.skill_runs,
        plan_mode_manager=plan_mode_manager,
        runtime_event_sources=runtime_event_sources,
        runtime_context=ctx.runtime_context,
        deep_thinking=run_data.deep_thinking,
        sandbox=run_data.sandbox,
    ):
        yield event
