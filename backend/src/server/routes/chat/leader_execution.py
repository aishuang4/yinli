"""单 Agent Leader 流消费与终态收口的统一编排。"""

from typing import AsyncGenerator

from src.server.routes.chat.completion import (
    finalize_failed_leader_run,
    finalize_successful_leader_run,
    max_react_iterations_error,
    missing_final_answer_error,
)
from src.server.routes.chat.leader_stream import LeaderStreamState, stream_leader_agent_reply
from src.server.routes.chat.models import ChatSkillRun
from src.runtime_context import RuntimeContext


async def execute_and_finalize_leader_reply(
    *,
    agent: object,
    input_event: object,
    state: LeaderStreamState,
    session_id: str,
    persist: bool,
    agent_id: str,
    agent_name: str,
    model_name: str,
    guardrails: object,
    user_message: str,
    stream_scope: str,
    skill_runs: list[ChatSkillRun],
    plan_mode_manager: object | None = None,
    runtime_event_sources: list[object] | None = None,
    sandbox: bool = False,
    exception_prefix: str = "Agent 执行异常",
    runtime_context: RuntimeContext | None = None,
) -> AsyncGenerator[str, None]:
    """执行一次 Leader reply，并保证非暂停路径只经过统一 completion 收口。"""

    try:
        async for event in stream_leader_agent_reply(
            agent=agent,
            user_msg=input_event,
            state=state,
            session_id=session_id,
            persist=persist,
            agent_id=agent_id,
            agent_name=agent_name,
            model_name=model_name,
            guardrails=guardrails,
            message=user_message,
            stream_scope=stream_scope,
            skill_runs=skill_runs,
            plan_mode_manager=plan_mode_manager,
            runtime_event_sources=runtime_event_sources,
            sandbox=sandbox,
        ):
            yield event

        if state.stopped and state.stop_reason != "plan_finalized":
            return
        if state.exceeded_max_iters:
            async for event in finalize_failed_leader_run(
                session_id=session_id,
                persist=persist,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                assistant_text="",
                assistant_error=max_react_iterations_error(),
                assistant_model_caches=state.assistant_model_caches,
                skill_runs=skill_runs,
                sandbox=sandbox,
            ):
                yield event
            return
        if not state.assistant_text.strip():
            async for event in finalize_failed_leader_run(
                session_id=session_id,
                persist=persist,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                assistant_text="",
                assistant_error=missing_final_answer_error(),
                assistant_model_caches=state.assistant_model_caches,
                skill_runs=skill_runs,
                sandbox=sandbox,
            ):
                yield event
            return

        async for event in finalize_successful_leader_run(
            session_id=session_id,
            persist=persist,
            agent_id=agent_id,
            agent_name=agent_name,
            model_name=model_name,
            user_message=user_message,
            assistant_text=state.assistant_text,
            assistant_thinking=state.assistant_thinking,
            assistant_tool_calls=state.assistant_tool_calls,
            assistant_model_caches=state.assistant_model_caches,
            runtime_events=state.runtime_events,
            skill_runs=skill_runs,
            active_skill_run=state.active_skill_run,
            runtime_context=runtime_context,
            session_workspace_dir=state.session_workspace_dir,
            session_artifact_paths_before=state.session_artifact_paths_before,
            sandbox=sandbox,
        ):
            yield event
    except Exception as exc:
        async for event in finalize_failed_leader_run(
            session_id=session_id,
            persist=persist,
            agent_id=agent_id,
            agent_name=agent_name,
            model_name=model_name,
            assistant_text=state.assistant_text,
            assistant_error=f"{exception_prefix}: {exc}",
            assistant_model_caches=state.assistant_model_caches,
            skill_runs=skill_runs,
            sandbox=sandbox,
        ):
            yield event
