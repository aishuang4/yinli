"""Chat ReAct Runtime。

本模块只负责 ReAct 架构下的执行链路：
1. Team executor 交给独立 Team Runtime。
2. 单 Agent executor 创建或复用主 Agent。
3. 消费 AgentScope 流式事件并完成成功/失败收尾。

第一层 architecture 路由和第二层 executor 选择均在进入本模块前完成。

scheduled firing 使用 `scheduled_preloaded` Skill 加载模式：首轮顺序固定为 preflight → scheduled
Toolkit → Run snapshot → forced Skill activation → Context 完整性断言 → Job prompt → 首次
模型调用，任一步失败都不启动模型（见 `scheduled_skill_preload`）。
"""

import logging
from typing import AsyncGenerator

from agentscope.message import UserMsg

from src.server.routes.chat.projection.source_events import SourceEvent

from src.agents.factory import mark_agent_busy, mark_agent_idle
from src.agents.runtime_types import SKILL_LOADING_SCHEDULED, normalize_skill_loading_mode
from src.agents.session_context import seed_context_from_history
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
from src.server.routes.chat.skill_context_middleware import (
    activate_forced_skills,
    restore_activated_skills,
)
from src.server.routes.chat.steer_middleware import RunSteerMiddleware
from src.storage.database import list_recent_skill_run_refs
from src.server.routes.chat.plan_mode.tools import build_plan_mode_tools
from src.server.routes.chat.runtime import _build_user_content, _guardrail_cache_key
from src.server.routes.chat.run_context import ChatRunContext
from src.server.routes.chat.scheduled_runtime import (
    SCHEDULED_JOB_UNATTENDED_DENYLIST,
    scheduled_job_unattended_feature_key,
)
from src.server.routes.chat.scheduled_skill_preload import (
    ScheduledSkillPreloadError,
    mark_scheduled_job_prompt,
    prepare_scheduled_first_turn,
    publish_scheduled_skill_audit,
)
from src.server.routes.chat.team_runtime import execute_team_runtime
from src.server.routes.chat.runtime_builder import (
    build_main_agent_runtime_spec,
    create_main_agent_from_spec,
)
from src.sessions.run_binding import frozen_resources_for_run
from src.server.routes.chat.runtime_governance import (
    build_context_governance_middlewares,
    persist_agent_session_state,
    prepare_agent_governance,
    restore_agent_session_state,
)
from src.tools.scheduled_job_tools import (
    build_scheduled_job_tools,
    scheduled_job_prompt_addendum,
    scheduled_job_tools_cache_key,
)
from src.server.routes.chat.trace_recorder import _make_fallback_trace_recorder
from src.server.routes.chat.runtime_events import runtime_source_event
from src.subagents.manager import SubagentDelegationManager
from src.subagents.registry import load_subagent_catalog
from src.tools.delegate_task import build_subagent_tools


logger = logging.getLogger(__name__)


def _skill_loading_mode(ctx: ChatRunContext) -> str:
    """scheduled firing 一律 preloaded-only；其余入口沿用 ctx 声明（默认交互式按需加载）。"""

    if getattr(ctx, "trigger_source", "user") == "scheduled_job":
        return SKILL_LOADING_SCHEDULED
    return normalize_skill_loading_mode(getattr(ctx, "skill_loading_mode", None))


async def _build_interactive_authorization(ctx, plan_mode_manager):
    """交互式单 Agent 的真实 Run 场景安装统一授权；离线契约 fixture 沿显式入口验证。"""
    if not current_chat_background_run_id():
        return None
    from src.server.routes.chat.authorization import build_chat_authorization

    return await build_chat_authorization(ctx, plan_mode_manager=plan_mode_manager)

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
    交互式单 Agent 仍注入 ScheduleJob 工具组，方便对话里登记任务。
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
            build_context_governance_middlewares(skill_context=True, hard_stop=True),
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
        middlewares.append(RunSteerMiddleware())
        # 已读过的 SKILL.md 正文跨轮保留、压缩 / 截断 / 重建后自动补回。
        # 已读 Skill 正文保留 + 一级无模型整理 + 压缩可观测；都只读写 state.middle_context，实例搬运时随 state 一起走。
        middlewares.extend(build_context_governance_middlewares(skill_context=True))
        feature_keys.append("run-steer:skill-context:context-governance")
        if ctx.persist_run:
            job_tools = build_scheduled_job_tools(
                workspace_id="",
                project_id="",
                agent_id=ctx.agent_id,
                session_id=ctx.session_id,
                approval_mode=run_data.approval_mode or "manual",
            )
            extra_tools.extend(job_tools)
            prompt_addenda.append(scheduled_job_prompt_addendum())
            feature_keys.append(scheduled_job_tools_cache_key(job_tools))

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
            plan_mode_manager=plan_mode_manager,
        )
        extra_tools.extend(build_subagent_tools(subagent_manager))
        runtime_event_sources.append(subagent_manager)
        # DelegateTask 工具绑定的是本轮 manager（含 run_id），实例必须按 Run 隔离；
        # 会话对话上下文由工厂在实例间搬运，不因此丢失。
        feature_keys.append(f"subagents-{catalog.fingerprint}:{current_chat_background_run_id() or ''}")
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
) -> AsyncGenerator[SourceEvent | str, None]:
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
        skill_loading_mode = _skill_loading_mode(ctx)
        run_snapshot = frozen_resources_for_run(ctx.runtime_context.run_id)
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
            extra_filesystem_scopes=list(getattr(ctx, "extra_filesystem_scopes", None) or []),
            authorization=await _build_interactive_authorization(ctx, plan_mode_manager),
            run_snapshot=run_snapshot,
            apply_snapshot_filesystem=getattr(ctx, "trigger_source", "user") != "scheduled_job",
            context_scope=(
                f"firing:{ctx.scheduled_firing_id}"
                if getattr(ctx, "trigger_source", "user") == "scheduled_job"
                else ""
            ),
            skill_loading_mode=skill_loading_mode,
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
        from src.exception_detail import format_agent_exception

        assistant_error = format_agent_exception(exc, prefix="Agent 创建失败")
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
            record_chat_message=getattr(ctx, "trigger_source", "user") != "scheduled_job",
        ):
            yield failure_event
        return

    # 进程内没有这条会话的 AgentScope 上下文（首轮 / 重启 / 实例被淘汰）时，
    # 用已持久化的正文重建精简历史；否则直接沿用实例自带的对话记忆。
    # offloader 目录和压缩落盘按 Chat 会话分目录；system prompt 分段字数供 CONTEXT_USAGE 定位膨胀来源。
    prepare_agent_governance(
        agent,
        session_id=ctx.session_id,
        prompt_sections=dict(getattr(ctx, "system_prompt_sections", None) or {}),
    )
    scheduled_firing_id = str(getattr(ctx, "scheduled_firing_id", "") or "")
    if skill_loading_mode == SKILL_LOADING_SCHEDULED:
        # Toolkit 已按 scheduled_preloaded 构造；接着恢复 Run snapshot、注入 required Skill 并做
        # Context 完整性断言。任一步失败都在构造 Job prompt 之前收口，模型不会被调用。
        try:
            await prepare_scheduled_first_turn(agent, ctx, run_snapshot=run_snapshot)
        except ScheduledSkillPreloadError as exc:
            publish_scheduled_skill_audit(scheduled_firing_id, agent)
            assistant_error = f"定时任务 Skill 预加载失败（{exc.code}）：{exc.message}"
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
                record_chat_message=False,
            ):
                yield failure_event
            return
    else:
        try:
            setattr(agent, "_aw_skill_context", dict(getattr(ctx, "skill_context_audit", None) or {}))
        except Exception:
            pass
        seeded = seed_context_from_history(agent, ctx.session_history)
        isolated = getattr(ctx, "trigger_source", "user") == "scheduled_job"
        if seeded and ctx.session_id and ctx.persist_run and not isolated:
            # 先恢复上一轮落库的压缩摘要与 middleware 跨轮数据（已启用 Skill 正文、治理计数），
            # 再用 skill_runs 记录兜底恢复"模型已读过哪些说明书"；正文由 SkillContextMiddleware
            # 在本轮 on_reply 时补回 context，避免再读一遍。
            restore_agent_session_state(agent, ctx.session_id)
            restore_activated_skills(
                agent,
                recent_skill_names=list_recent_skill_run_refs(ctx.session_id, ctx.agent_id),
                available_skills=ctx.used_skills,
            )
        activate_forced_skills(agent, getattr(ctx, "forced_skills", None) or [])
    if getattr(ctx, "force_compress", False):
        from src.server.routes.chat.context_middleware import force_compress_context

        await force_compress_context(agent)
        if ctx.session_id and ctx.persist_run and getattr(ctx, "trigger_source", "user") != "scheduled_job":
            persist_agent_session_state(agent, session_id=ctx.session_id, agent_id=ctx.agent_id)
    user_text = ctx.effective_user_message
    note = str(getattr(ctx, "user_message_note", "") or "")
    if note:
        user_text = f"{user_text}\n\n{note}"
    user_msg = UserMsg(
        name="user",
        content=_build_user_content(
            user_text,
            run_data.images,
        ),
    )
    if skill_loading_mode == SKILL_LOADING_SCHEDULED:
        mark_scheduled_job_prompt(agent)

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
    # 执行期间标记实例忙碌：同会话并发新一轮时工厂会改用快照实例，闲置淘汰也会跳过它。
    mark_agent_busy(agent, current_chat_background_run_id() or "")
    try:
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
            record_chat_message=getattr(ctx, "trigger_source", "user") != "scheduled_job",
        ):
            yield event
    finally:
        mark_agent_idle(agent)
        if skill_loading_mode == SKILL_LOADING_SCHEDULED:
            # 调度器在 Chat Run 终态后 join 后台任务，再按 firing 取走这份审计写入 firing 记录。
            publish_scheduled_skill_audit(scheduled_firing_id, agent)
        if ctx.persist_run and ctx.session_id and getattr(ctx, "trigger_source", "user") != "scheduled_job":
            persist_agent_session_state(agent, session_id=ctx.session_id, agent_id=ctx.agent_id)
