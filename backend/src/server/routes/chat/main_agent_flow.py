"""Chat 主 Agent 执行流程。

负责 Team worker 预执行、主 Agent 构建、Leader 流式事件处理和收尾。
send.py 只调用本模块，不直接关心 AgentScope 执行细节。
"""

from typing import AsyncGenerator

from agentscope.message import UserMsg

from src.server.routes.chat.completion import (
    emit_failed_startup_run,
    finalize_failed_leader_run,
    finalize_successful_leader_run,
)
from src.server.routes.chat.leader_stream import LeaderStreamState, stream_leader_agent_reply
from src.server.routes.chat.models import ChatMessage, ChatModelCacheRun
from src.server.routes.chat.orchestration import (
    LeaderPreparationState,
    prepare_leader_after_team_workers,
)
from src.server.routes.chat.prompts import _build_leader_user_message
from src.server.routes.chat.runtime import _build_user_content, _guardrail_cache_key
from src.server.routes.chat.run_context import ChatRunContext
from src.server.routes.chat.runtime_builder import (
    build_main_agent_runtime_spec,
    create_main_agent_from_spec,
)
from src.server.routes.chat.trace_recorder import _make_fallback_trace_recorder


async def execute_main_agent_flow(
    *,
    ctx: ChatRunContext,
    run_data: ChatMessage,
) -> AsyncGenerator[str, None]:
    """执行主 Agent 流程并输出 SSE 事件。"""

    assistant_text = ""
    assistant_thinking = ""
    assistant_error: str | None = None
    assistant_model_caches: list[ChatModelCacheRun] = []
    worker_outputs: list[dict[str, str]] = []
    active_skill_run = ctx.active_skill_run
    system_prompt = ctx.system_prompt

    try:
        leader_prep = LeaderPreparationState(system_prompt=system_prompt)
        async for worker_event in prepare_leader_after_team_workers(
            state=leader_prep,
            data=ctx.routed_team.run_data,
            session_id=ctx.session_id,
            team_activities=ctx.team_activities,
            model_name=ctx.model_name,
            leader_agent_id=ctx.agent_id,
            skill_runs=ctx.skill_runs,
        ):
            yield worker_event
        if leader_prep.waiting:
            return
        system_prompt = leader_prep.system_prompt
        worker_outputs = leader_prep.worker_outputs

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
        )
        agent = create_main_agent_from_spec(
            main_agent_spec,
            on_fallback_event=_make_fallback_trace_recorder(
                session_id=ctx.session_id,
                persist=ctx.persist_run,
                scope="leader",
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
            skill_runs=ctx.skill_runs,
            team_activities=ctx.team_activities,
        ):
            yield leader_event
        active_skill_run = leader_stream_state.active_skill_run
        assistant_text = leader_stream_state.assistant_text
        assistant_thinking = leader_stream_state.assistant_thinking
        assistant_tool_calls = leader_stream_state.assistant_tool_calls
        assistant_model_caches = leader_stream_state.assistant_model_caches
        runtime_events = leader_stream_state.runtime_events
        if leader_stream_state.stopped:
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
