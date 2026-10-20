"""单 Agent Leader 流消费与终态收口的统一编排。"""

import logging
from typing import AsyncGenerator

from src.server.routes.chat.completion import (
    finalize_failed_leader_run,
    finalize_successful_leader_run,
    max_react_iterations_error,
    missing_final_answer_error,
)
from src.server.routes.chat.leader_stream import LeaderStreamState, stream_leader_agent_reply
from src.server.routes.chat.models import ChatSkillRun
from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.runtime_events import runtime_source_event
from src.exception_detail import format_agent_exception
from src.runtime_context import RuntimeContext

logger = logging.getLogger(__name__)


async def _emit_plan_run_failure(
    manager: PlanModeManager | None,
    state: LeaderStreamState,
    reason: str,
) -> AsyncGenerator[str, None]:
    """先持久化 Plan 失败/回退状态，再让通用 Run failure 收口。"""

    if manager is None:
        return
    manager.fail_run(reason=reason)
    for runtime_event in manager.drain_runtime_events():
        state.runtime_events.append(runtime_event)
        yield runtime_source_event(runtime_event)


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
    plan_mode_manager: PlanModeManager | None = None,
    runtime_event_sources: list[object] | None = None,
    deep_thinking: bool = False,
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
            deep_thinking=deep_thinking,
            sandbox=sandbox,
        ):
            yield event

        if state.stopped and state.stop_reason in {"hitl_denied", "job_needs_human"}:
            assistant_error = (
                "定时任务遇到需要人工判断的动作，本次运行已结束，未执行该动作。"
                if state.stop_reason == "job_needs_human"
                else "hitl_denied: 无人值守 HITL 拒绝次数超过上限"
            )
            async for event in finalize_failed_leader_run(
                session_id=session_id,
                persist=persist,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                assistant_text=state.assistant_text,
                assistant_error=assistant_error,
                assistant_model_caches=state.assistant_model_caches,
                skill_runs=skill_runs,
                sandbox=sandbox,
            ):
                yield event
            return
        if state.stopped and state.stop_reason != "plan_review_requested":
            return
        if state.exceeded_max_iters:
            assistant_error = max_react_iterations_error()
            async for event in _emit_plan_run_failure(plan_mode_manager, state, assistant_error):
                yield event
            async for event in finalize_failed_leader_run(
                session_id=session_id,
                persist=persist,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                assistant_text="",
                assistant_error=assistant_error,
                assistant_model_caches=state.assistant_model_caches,
                skill_runs=skill_runs,
                sandbox=sandbox,
            ):
                yield event
            return
        if state.reply_finished_reason != "completed":
            if state.reply_finished_reason == "interrupted" and await _current_run_is_cancelled():
                return
            assistant_error = _reply_finished_error(state)
            async for event in _emit_plan_run_failure(plan_mode_manager, state, assistant_error):
                yield event
            async for event in finalize_failed_leader_run(
                session_id=session_id,
                persist=persist,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                assistant_text=state.assistant_text,
                assistant_error=assistant_error,
                assistant_model_caches=state.assistant_model_caches,
                skill_runs=skill_runs,
                sandbox=sandbox,
            ):
                yield event
            return
        plan_completion_error = plan_mode_manager.run_completion_error() if plan_mode_manager else ""
        if plan_completion_error:
            async for event in _emit_plan_run_failure(plan_mode_manager, state, plan_completion_error):
                yield event
            async for event in finalize_failed_leader_run(
                session_id=session_id,
                persist=persist,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                assistant_text=state.assistant_text,
                assistant_error=plan_completion_error,
                assistant_model_caches=state.assistant_model_caches,
                skill_runs=skill_runs,
                sandbox=sandbox,
            ):
                yield event
            return
        if not state.assistant_text.strip():
            assistant_error = missing_final_answer_error()
            async for event in _emit_plan_run_failure(plan_mode_manager, state, assistant_error):
                yield event
            async for event in finalize_failed_leader_run(
                session_id=session_id,
                persist=persist,
                agent_id=agent_id,
                agent_name=agent_name,
                model_name=model_name,
                assistant_text="",
                assistant_error=assistant_error,
                assistant_model_caches=state.assistant_model_caches,
                skill_runs=skill_runs,
                sandbox=sandbox,
            ):
                yield event
            return

        from src.memory.model_review import snapshot_review_context

        async for event in finalize_successful_leader_run(
            review_context=snapshot_review_context(agent, skill_runs),
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
        assistant_error = format_agent_exception(exc, prefix=exception_prefix)
        logger.error("Leader 执行失败\n%s", assistant_error)
        async for event in _emit_plan_run_failure(plan_mode_manager, state, assistant_error):
            yield event
        async for event in finalize_failed_leader_run(
            session_id=session_id,
            persist=persist,
            agent_id=agent_id,
            agent_name=agent_name,
            model_name=model_name,
            assistant_text=state.assistant_text,
            assistant_error=assistant_error,
            assistant_model_caches=state.assistant_model_caches,
            skill_runs=skill_runs,
            sandbox=sandbox,
        ):
            yield event


async def _current_run_is_cancelled() -> bool:
    """应用层取消已经成立时，不再把 SDK interrupted 覆盖为 failed。"""

    from src.server.routes.chat.background_runs import (
        current_chat_background_run_id,
        get_chat_background_run,
    )

    run_id = current_chat_background_run_id()
    if not run_id:
        return False
    run = await get_chat_background_run(run_id)
    return bool(run and run.status == "cancelled")


def _reply_finished_error(state: LeaderStreamState) -> str:
    """把 AgentScope Reply 终态转换为应用失败说明。"""

    if state.reply_error:
        return state.reply_error
    return {
        "interrupted": "Agent 执行已中断。",
        "exceed_max_iters": max_react_iterations_error(),
        "error": "AgentScope 返回了失败终态。",
    }.get(state.reply_finished_reason, f"AgentScope Reply 异常结束：{state.reply_finished_reason}")
