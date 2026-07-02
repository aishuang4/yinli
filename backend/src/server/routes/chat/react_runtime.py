"""Chat ReAct Runtime。

本模块只负责 ReAct 架构下的执行链路：
1. 根据机械 executor 决定是否先运行 Team Worker。
2. 创建或复用主 Agent。
3. 消费 AgentScope 流式事件并完成成功/失败收尾。

第一层 architecture 路由和第二层 executor 选择均在进入本模块前完成。
"""

from typing import AsyncGenerator

from agentscope.message import UserMsg

from src.server.routes.chat.background_runs import current_chat_background_run_id
from src.server.routes.chat.completion import (
    emit_failed_startup_run,
    finalize_failed_leader_run,
    finalize_successful_leader_run,
    max_react_iterations_error,
    missing_final_answer_error,
)
from src.server.routes.chat.leader_stream import LeaderStreamState, stream_leader_agent_reply
from src.server.routes.chat.models import ChatMessage, ChatModelCacheRun
from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.plan_mode.middleware import PlanModeMiddleware
from src.server.routes.chat.plan_mode.tools import build_plan_mode_tools
from src.server.routes.chat.team_runtime import (
    TeamRuntimeState,
    prepare_leader_with_team_runtime,
)
from src.server.routes.chat.prompts import _build_leader_user_message
from src.server.routes.chat.runtime import _build_user_content, _guardrail_cache_key
from src.server.routes.chat.run_context import ChatRunContext
from src.server.routes.chat.runtime_builder import (
    build_main_agent_runtime_spec,
    create_main_agent_from_spec,
)
from src.server.routes.chat.trace_recorder import _make_fallback_trace_recorder


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
        team_id=run_data.team_id,
        persist=ctx.persist_run,
    )
    runtime_features_cache_key = "plan-mode-v1"
    tools = build_plan_mode_tools(manager)
    middlewares = [PlanModeMiddleware(manager)]
    return manager, runtime_features_cache_key, tools, middlewares


async def execute_react_runtime(
    *,
    ctx: ChatRunContext,
    run_data: ChatMessage,
) -> AsyncGenerator[str, None]:
    """执行 ReAct Runtime 并输出 SSE 事件。"""

    assistant_text = ""
    assistant_thinking = ""
    assistant_model_caches: list[ChatModelCacheRun] = []
    worker_outputs: list[dict[str, str]] = []
    active_skill_run = ctx.active_skill_run
    system_prompt = ctx.system_prompt
    stream_scope = "leader" if ctx.intent_route.executor.kind == "team" else "single"

    try:
        if ctx.intent_route.executor.kind == "team":
            team_state = TeamRuntimeState(leader_system_prompt=system_prompt)
            async for worker_event in prepare_leader_with_team_runtime(
                state=team_state,
                data=ctx.routed_team.run_data,
                session_id=ctx.session_id,
                team_activities=ctx.team_activities,
                model_name=ctx.model_name,
                leader_agent_id=ctx.agent_id,
                skill_runs=ctx.skill_runs,
            ):
                yield worker_event
            if team_state.waiting:
                return
            system_prompt = team_state.leader_system_prompt
            worker_outputs = team_state.worker_reports

        (
            plan_mode_manager,
            runtime_features_cache_key,
            extra_tools,
            middlewares,
        ) = _build_plan_mode_runtime_parts(ctx, run_data)
        main_agent_spec = build_main_agent_runtime_spec(
            agent_id=ctx.agent_id,
            session_id=ctx.session_id,
            agent_name=ctx.agent_name,
            system_prompt=system_prompt,
            model_config=ctx.runtime_model_config,
            deep_thinking=run_data.deep_thinking,
            used_skills=ctx.used_skills,
            guardrails=ctx.guardrails,
            mcp_servers=ctx.mcp_servers,
            knowledge_tools=ctx.knowledge_tools,
            runtime_tool_scope=ctx.runtime_tool_scope,
            guardrail_cache_key=_guardrail_cache_key(ctx.guardrails),
            runtime_features_cache_key=runtime_features_cache_key,
            extra_tools=extra_tools,
            middlewares=middlewares,
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
        async for failure_event in emit_failed_startup_run(
            session_id=ctx.session_id,
            persist=ctx.persist_run,
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
            model_name=ctx.model_name,
            assistant_error=assistant_error,
            skill_runs=ctx.skill_runs,
            team_activities=ctx.team_activities,
            sandbox=run_data.sandbox,
        ):
            yield failure_event
        return

    user_msg = UserMsg(
        name="user",
        content=_build_user_content(
            _build_leader_user_message(ctx.effective_user_message, worker_outputs),
            run_data.images,
        ),
    )

    try:
        leader_stream_state = LeaderStreamState(
            active_skill_run=active_skill_run,
        )
        async for leader_event in stream_leader_agent_reply(
            agent=agent,
            user_msg=user_msg,
            state=leader_stream_state,
            session_id=ctx.session_id,
            persist=ctx.persist_run,
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
            model_name=ctx.model_name,
            guardrails=ctx.guardrails,
            message=ctx.effective_user_message,
            stream_scope=stream_scope,
            skill_runs=ctx.skill_runs,
            team_activities=ctx.team_activities,
            plan_mode_manager=plan_mode_manager,
        ):
            yield leader_event
        active_skill_run = leader_stream_state.active_skill_run
        assistant_text = leader_stream_state.assistant_text
        assistant_thinking = leader_stream_state.assistant_thinking
        assistant_tool_calls = leader_stream_state.assistant_tool_calls
        assistant_model_caches = leader_stream_state.assistant_model_caches
        runtime_events = leader_stream_state.runtime_events
        if leader_stream_state.stopped and leader_stream_state.stop_reason != "plan_finalized":
            return
        if leader_stream_state.exceeded_max_iters:
            async for completion_event in finalize_failed_leader_run(
                session_id=ctx.session_id,
                persist=ctx.persist_run,
                agent_id=ctx.agent_id,
                agent_name=ctx.agent_name,
                model_name=ctx.model_name,
                assistant_text="",
                assistant_error=max_react_iterations_error(),
                assistant_model_caches=assistant_model_caches,
                skill_runs=ctx.skill_runs,
                team_activities=ctx.team_activities,
                sandbox=run_data.sandbox,
            ):
                yield completion_event
            return
        if not assistant_text.strip():
            async for completion_event in finalize_failed_leader_run(
                session_id=ctx.session_id,
                persist=ctx.persist_run,
                agent_id=ctx.agent_id,
                agent_name=ctx.agent_name,
                model_name=ctx.model_name,
                assistant_text="",
                assistant_error=missing_final_answer_error(),
                assistant_model_caches=assistant_model_caches,
                skill_runs=ctx.skill_runs,
                team_activities=ctx.team_activities,
                sandbox=run_data.sandbox,
            ):
                yield completion_event
            return
        async for completion_event in finalize_successful_leader_run(
            session_id=ctx.session_id,
            persist=ctx.persist_run,
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
            model_name=ctx.model_name,
            user_message=ctx.effective_user_message,
            assistant_text=assistant_text,
            assistant_thinking=assistant_thinking,
            assistant_tool_calls=assistant_tool_calls,
            assistant_model_caches=assistant_model_caches,
            runtime_events=runtime_events,
            skill_runs=ctx.skill_runs,
            active_skill_run=active_skill_run,
            team_activities=ctx.team_activities,
            team_id=run_data.team_id,
            team_name=run_data.team_name,
            sandbox=run_data.sandbox,
        ):
            yield completion_event
    except Exception as exc:
        assistant_error = f"Agent 执行异常: {exc}"
        async for completion_event in finalize_failed_leader_run(
            session_id=ctx.session_id,
            persist=ctx.persist_run,
            agent_id=ctx.agent_id,
            agent_name=ctx.agent_name,
            model_name=ctx.model_name,
            assistant_text=assistant_text,
            assistant_error=assistant_error,
            assistant_model_caches=assistant_model_caches,
            skill_runs=ctx.skill_runs,
            team_activities=ctx.team_activities,
            sandbox=run_data.sandbox,
        ):
            yield completion_event
