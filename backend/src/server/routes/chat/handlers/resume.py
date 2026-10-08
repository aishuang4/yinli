"""人工确认或外部执行结果后恢复 Agent / Team 运行。"""

import asyncio
from pathlib import Path
from typing import AsyncGenerator

from fastapi import HTTPException

from src.agents.runtime_types import AgentGuardrails
from src.server.routes.agents import get_registered_agent

from src.server.routes.chat.helpers import source_event, _trim_text
from src.server.routes.chat.hitl import (
    _build_resume_event,
    _clear_pending_interaction,
    _create_pending_interaction,
    _hitl_payload,
)
from src.server.routes.chat.models import (
    PENDING_AGENTS,
    PENDING_INTERACTIONS,
    HITL_EVENT_TYPES,
    ChatResumeRequest,
)
from src.server.routes.chat.projection.source_events import SourceEvent
from src.server.routes.chat.router import router
from src.server.routes.chat.background_runs import (
    attach_chat_background_run_task,
    mark_chat_background_run_resuming,
    relay_chat_background_run_events,
)
from src.server.routes.chat.completion import max_react_iterations_error, missing_final_answer_error
from src.server.routes.chat.leader_stream import LeaderStreamState
from src.server.routes.chat.leader_execution import execute_and_finalize_leader_reply
from src.server.routes.chat.lifecycle import waiting_reason, waiting_status_for_interaction
from src.server.routes.chat.runtime import (
    _build_runtime_guardrails,
)
from src.server.routes.chat.runtime_events import (
    agentscope_reply_error_message,
    agentscope_reply_finished_reason,
    runtime_event_from_agentscope_event,
    runtime_trace_source_event,
)
from src.server.routes.chat.skill_runs import (
    get_active_session_skill_run,
    get_skill_run_for_pending_interaction,
    update_skill_run_status,
)
from src.server.routes.chat.tool_state import _pending_tool_calls_to_runs
from src.server.routes.chat.trace_recorder import (
    _attach_provider_cache_usage,
    _record_agent_scope_event,
    _record_permission_decision,
    _record_run_event,
)
from src.storage.database import update_subagent_invocation, update_subagent_invocation_delivery
from src.subagents.task_store import SubagentTaskStore


def _is_final_answer_text_scope(scope: str) -> bool:
    """判断 resume 文本 delta 是否应作为最终答案输出。"""

    return scope in {"single", "single_agent"}


def _should_wrap_resume_text_as_final(
    event_type: str,
    scope: str,
    final_answer_started: bool,
) -> bool:
    """单 Agent 恢复的正文在生成结束且没有工具调用后才是最终答案。"""

    return final_answer_started and event_type == "TEXT_BLOCK_DELTA" and _is_final_answer_text_scope(scope)


def _should_emit_raw_resume_event(event_type: str, scope: str) -> bool:
    """正文改由带 generation 的最终答案事件投影，原始文本事件不再向外送。"""

    del scope
    if event_type == "TEXT_BLOCK_DELTA":
        return False
    return True


def resume_interaction_events(data: ChatResumeRequest) -> AsyncGenerator[SourceEvent, None]:
    """构造恢复原 AgentScope ReAct 的结构化源信号流。"""

    async def event_stream() -> AsyncGenerator[SourceEvent, None]:
        interaction = PENDING_INTERACTIONS.get(data.interaction_id)
        agent = PENDING_AGENTS.get(data.interaction_id)
        if interaction is None or agent is None:
            stale_skill_run = get_skill_run_for_pending_interaction(data.interaction_id)
            if stale_skill_run:
                update_skill_run_status(
                    stale_skill_run,
                    status="completed",
                    current_step="stale_after_backend_restart",
                    pending_interaction_id="",
                )
            yield source_event("ERROR", {
                "message": "待处理交互不存在或后端已重启，无法恢复原 AgentScope ReAct。请重新发送上一条需求继续。",
                "recoverable": True,
                "reason": "missing_pending_interaction",
            })
            return

        try:
            resume_event = _build_resume_event(interaction, data)
        except Exception as exc:
            yield source_event("ERROR", {"message": f"恢复事件构建失败: {exc}"})
            return

        skill_runs = interaction.skill_runs

        _record_run_event(
            session_id=interaction.session_id,
            event_type="HITL_RESOLVED",
            persist=True,
            scope=interaction.scope,
            agent_id=interaction.agent_id,
            agent_name=interaction.agent_name,
            status="completed" if data.action == "approve" else "rejected",
            title=f"{interaction.scope.title()} human approval {data.action}",
            payload={
                "interactionId": interaction.id,
                "interactionType": interaction.type,
                "action": data.action,
                "externalResultCount": len(data.external_results),
                "externalResultsPreview": _external_results_preview(data.external_results),
            },
        )
        yield runtime_trace_source_event(
            "HITL_RESOLVED",
            session_id=interaction.session_id,
            run_id=interaction.run_id,
            scope=interaction.scope,
            agent_id=interaction.agent_id,
            agent_name=interaction.agent_name,
            status="completed" if data.action == "approve" else "failed",
            payload={
                "interactionId": interaction.id,
                "interactionType": interaction.type,
                "action": data.action,
                "externalResultCount": len(data.external_results),
                "externalResultsPreview": _external_results_preview(data.external_results),
            },
        )
        yield runtime_trace_source_event(
            "RUN_RESUMED",
            session_id=interaction.session_id,
            run_id=interaction.run_id,
            scope="run",
            agent_id=interaction.agent_id,
            agent_name=interaction.agent_name,
            status="running",
            payload={
                "interactionId": interaction.id,
                "interactionType": interaction.type,
                "action": data.action,
            },
        )
        yield runtime_trace_source_event(
            "AGENT_RESUMED",
            session_id=interaction.session_id,
            run_id=interaction.run_id,
            scope=interaction.scope,
            agent_id=interaction.agent_id,
            agent_name=interaction.agent_name,
            status="running",
            payload={
                "interactionId": interaction.id,
                "interactionType": interaction.type,
                "action": data.action,
            },
        )

        try:
            if interaction.scope == "subagent":
                subagent_data = dict(interaction.chat_data or {})
                invocation_id = str(subagent_data.get("invocationId") or "")
                tool_call_id = str(subagent_data.get("toolCallId") or "")
                subagent_name = str(subagent_data.get("subagentName") or interaction.agent_name)
                subagent_guardrails = AgentGuardrails()
                subagent_stream_state = LeaderStreamState()
                reply_generation = 0
                generation_text = ""
                generation_had_tool = False
                final_text = ""
                reasoning_text = interaction.assistant_text or ""
                duration_ms = 0
                subagent_seq = 0
                project_root = str(subagent_data.get("projectRoot") or "")
                subagent_task_store = (
                    SubagentTaskStore(project_root=Path(project_root), session_id=interaction.session_id)
                    if project_root
                    else None
                )

                async for event in agent.reply_stream(resume_event):
                    subagent_seq += 1
                    event_data = event.model_dump(mode="json")
                    event_type = event_data.pop("type", "UNKNOWN")
                    _attach_provider_cache_usage(event_type, event_data)
                    _record_agent_scope_event(
                        session_id=interaction.session_id,
                        event_type=event_type,
                        event_data=event_data,
                        persist=True,
                        scope="subagent",
                        agent_id=interaction.agent_id,
                        agent_name=interaction.agent_name,
                    )
                    _record_permission_decision(
                        session_id=interaction.session_id,
                        event_type=event_type,
                        event_data=event_data,
                        persist=True,
                        scope="subagent",
                        agent_id=interaction.agent_id,
                        agent_name=interaction.agent_name,
                        guardrails=subagent_guardrails,
                    )
                    if event_type == "MODEL_CALL_START":
                        reply_generation += 1
                        generation_text = ""
                        generation_had_tool = False
                    elif event_type in {"TOOL_CALL_START", "TOOL_CALL_DELTA", "TOOL_CALL_END"}:
                        generation_had_tool = True
                    if event_type in HITL_EVENT_TYPES:
                        agentscope_waiting_tool_calls = list(event_data.get("tool_calls") or [])
                        next_interaction = _create_pending_interaction(
                            interaction_type=event_type,
                            agent=agent,
                            session_id=interaction.session_id,
                            agent_id=interaction.agent_id,
                            agent_name=interaction.agent_name,
                            model_name=interaction.model_name,
                            reply_id=str(event_data.get("reply_id") or ""),
                            tool_calls=agentscope_waiting_tool_calls,
                            resume_tool_calls=agentscope_waiting_tool_calls,
                            message=interaction.message,
                            assistant_text=final_text or reasoning_text,
                            skill_runs=[],
                            scope="subagent",
                            chat_data=subagent_data,
                            run_id=interaction.run_id,
                        )
                        partial = _trim_text(final_text or reasoning_text, 1200)
                        if invocation_id:
                            update_subagent_invocation(
                                invocation_id,
                                status="waiting",
                                result_preview=partial,
                                error="Subagent paused for another human approval.",
                            )
                            if subagent_task_store is not None:
                                subagent_task_store.set_status(
                                    invocation_id,
                                    "waiting",
                                    error="Subagent paused for another human approval.",
                                )
                        _clear_pending_interaction(interaction.id)
                        wait_state = waiting_status_for_interaction(
                            next_interaction.type,
                            next_interaction.resume_tool_calls,
                        )
                        yield runtime_trace_source_event(
                            "RUN_WAITING",
                            session_id=interaction.session_id,
                            run_id=interaction.run_id,
                            scope="run",
                            agent_id=interaction.agent_id,
                            agent_name=interaction.agent_name,
                            status=wait_state,
                            reason=waiting_reason(wait_state),
                            payload={
                                "interactionId": next_interaction.id,
                                "interactionType": next_interaction.type,
                                "waitState": wait_state,
                                "reason": waiting_reason(wait_state),
                            },
                        )
                        yield runtime_trace_source_event(
                            "SUBAGENT_WAITING_HUMAN",
                            session_id=interaction.session_id,
                            run_id=interaction.run_id,
                            scope="subagent",
                            agent_id=interaction.agent_id,
                            agent_name=interaction.agent_name,
                            status="waiting",
                            reason="hitl_required",
                            payload={
                                "invocationId": invocation_id,
                                "toolCallId": tool_call_id,
                                "subagentName": subagent_name,
                                "interactionId": next_interaction.id,
                                "interactionType": event_type,
                                "replyId": str(event_data.get("reply_id") or ""),
                                "toolCalls": agentscope_waiting_tool_calls,
                                "partialResultPreview": partial,
                                "durationMs": duration_ms,
                                "seq": subagent_seq,
                            },
                        )
                        yield source_event("HITL_REQUIRED", _hitl_payload(next_interaction))
                        return
                    if event_type == "EXCEED_MAX_ITERS":
                        subagent_stream_state.exceeded_max_iters = True
                        break
                    if event_type == "REPLY_END":
                        subagent_stream_state.reply_finished_reason = (
                            agentscope_reply_finished_reason(event_data)
                        )
                        subagent_stream_state.reply_error = (
                            agentscope_reply_error_message(event_data)
                        )
                    if event_type == "TEXT_BLOCK_DELTA":
                        delta = str(event_data.get("delta") or "")
                        if reply_generation <= 0:
                            reply_generation = 1
                        generation_text += delta
                        reasoning_text += delta
                    if event_type == "REPLY_END":
                        finished = agentscope_reply_finished_reason(event_data)
                        if finished == "completed" and not generation_had_tool and generation_text.strip():
                            final_text = generation_text
                        elif finished == "completed":
                            final_text = ""
                    runtime_event = runtime_event_from_agentscope_event(
                        event_type,
                        session_id=interaction.session_id,
                        run_id=interaction.run_id,
                        scope="subagent",
                        agent_id=interaction.agent_id,
                        agent_name=interaction.agent_name,
                        event_data=event_data,
                        payload_extra={
                            "invocationId": invocation_id,
                            "toolCallId": tool_call_id,
                            "subagentName": subagent_name,
                            "seq": subagent_seq,
                            "resumedFromHitl": True,
                        },
                    )
                    if runtime_event:
                        yield runtime_event

                if (
                    subagent_stream_state.exceeded_max_iters
                    or subagent_stream_state.reply_finished_reason != "completed"
                    or not final_text.strip()
                ):
                    error_message = (
                        max_react_iterations_error()
                        if subagent_stream_state.exceeded_max_iters
                        else (
                            subagent_stream_state.reply_error
                            or (
                                "Subagent 执行已中断。"
                                if subagent_stream_state.reply_finished_reason == "interrupted"
                                else missing_final_answer_error()
                            )
                        )
                    )
                    partial = _trim_text(final_text or reasoning_text, 1200)
                    if invocation_id:
                        if subagent_task_store is not None:
                            content = "\n\n".join(part for part in [
                                f"Subagent {subagent_name} failed after resume.",
                                error_message,
                                f"Partial result:\n{partial}" if partial else "",
                            ] if part)
                            subagent_task_store.write_result(invocation_id, content, error=error_message)
                            failed_status = subagent_task_store.set_status(invocation_id, "failed", error=error_message)
                            update_subagent_invocation_delivery(
                                invocation_id,
                                delivery_status=str(failed_status.get("deliveryStatus") or "pending"),
                                result_ref=str(failed_status.get("resultRef") or ""),
                            )
                        update_subagent_invocation(
                            invocation_id,
                            status="failed",
                            result_preview=partial,
                            error=error_message,
                        )
                    _clear_pending_interaction(interaction.id)
                    yield runtime_trace_source_event(
                        "SUBAGENT_FAILED",
                        session_id=interaction.session_id,
                        run_id=interaction.run_id,
                        scope="subagent",
                        agent_id=interaction.agent_id,
                        agent_name=interaction.agent_name,
                        status="failed",
                        reason="resume_failed",
                        payload={
                            "invocationId": invocation_id,
                            "toolCallId": tool_call_id,
                            "subagentName": subagent_name,
                            "error": error_message,
                            "partialResultPreview": partial,
                            "seq": subagent_seq,
                        },
                    )
                    return

                result_preview = _trim_text(final_text, 1200)
                if invocation_id:
                    if subagent_task_store is not None:
                        subagent_task_store.write_result(invocation_id, final_text)
                        resumed_status = subagent_task_store.set_status(invocation_id, "completed")
                        update_subagent_invocation_delivery(
                            invocation_id,
                            delivery_status=str(resumed_status.get("deliveryStatus") or "pending"),
                            result_ref=str(resumed_status.get("resultRef") or ""),
                        )
                    else:
                        resumed_status = {}
                    update_subagent_invocation(
                        invocation_id,
                        status="completed",
                        result_preview=result_preview,
                    )
                _clear_pending_interaction(interaction.id)
                yield runtime_trace_source_event(
                    "SUBAGENT_COMPLETED",
                    session_id=interaction.session_id,
                    run_id=interaction.run_id,
                    scope="subagent",
                    agent_id=interaction.agent_id,
                    agent_name=interaction.agent_name,
                    status="completed",
                    payload={
                        "invocationId": invocation_id,
                        "toolCallId": tool_call_id,
                        "subagentName": subagent_name,
                        "status": "completed",
                        "deliveryStatus": resumed_status.get("deliveryStatus", "pending"),
                        "resultRef": resumed_status.get("resultRef", ""),
                        "resultPreview": result_preview,
                        "durationMs": duration_ms,
                        "resumedFromHitl": True,
                        "seq": subagent_seq,
                    },
                )
                return

            resume_stream_state = LeaderStreamState(
                assistant_text=interaction.assistant_text,
                reply_generation=interaction.reply_generation,
                assistant_tool_calls=_pending_tool_calls_to_runs(
                    interaction.tool_calls,
                    interaction.assistant_tool_calls,
                ),
                active_skill_run=get_active_session_skill_run(
                    interaction.session_id,
                    interaction.agent_id,
                ),
                session_workspace_dir=str((interaction.chat_data or {}).get("sessionWorkspaceDir") or ""),
                session_artifact_paths_before=[
                    str(path)
                    for path in (interaction.chat_data or {}).get("sessionArtifactPathsBefore", [])
                    if isinstance(path, str) and path
                ],
            )
            interaction_guardrails = _build_runtime_guardrails(get_registered_agent(interaction.agent_id))
            _clear_pending_interaction(interaction.id)
            async for event in execute_and_finalize_leader_reply(
                agent=agent,
                input_event=resume_event,
                state=resume_stream_state,
                session_id=interaction.session_id,
                persist=True,
                agent_id=interaction.agent_id,
                agent_name=interaction.agent_name,
                model_name=interaction.model_name,
                guardrails=interaction_guardrails,
                user_message=interaction.message,
                stream_scope=interaction.scope,
                skill_runs=skill_runs,
                plan_mode_manager=interaction.plan_mode_manager,
                runtime_event_sources=interaction.runtime_event_sources,
                deep_thinking=bool((interaction.chat_data or {}).get("deepThinking")),
                sandbox=bool((interaction.chat_data or {}).get("sandbox")),
                exception_prefix="Agent 恢复执行异常",
            ):
                yield event
            return
        except Exception as exc:
            _clear_pending_interaction(interaction.id)
            error_message = f"Agent 恢复执行异常: {exc}"
            yield runtime_trace_source_event(
                "RUN_FAILED",
                session_id=interaction.session_id,
                run_id=interaction.run_id,
                scope="run",
                agent_id=interaction.agent_id,
                agent_name=interaction.agent_name,
                status="failed",
                reason="resume_error",
                payload={"message": error_message},
            )
            yield source_event("ERROR", {"message": error_message})

    return event_stream()


@router.post("/resume/start")
async def start_resume_interaction(data: ChatResumeRequest) -> dict:
    """后台恢复 HITL Run，并返回原 Run ID 供前端继续订阅 RuntimeFrame。"""

    pending_interaction = PENDING_INTERACTIONS.get(data.interaction_id)
    if pending_interaction is None or not pending_interaction.run_id:
        raise HTTPException(status_code=404, detail="Pending interaction not found")
    await mark_chat_background_run_resuming(
        pending_interaction.run_id,
        interaction_id=pending_interaction.id,
        action=data.action,
        external_results_preview=_external_results_preview(data.external_results),
    )
    stream = relay_chat_background_run_events(
        pending_interaction.run_id,
        resume_interaction_events(data),
    )
    resume_task = asyncio.create_task(
        _drain_resume_stream(stream),
        name=f"chat-resume-{pending_interaction.run_id}",
    )
    await attach_chat_background_run_task(pending_interaction.run_id, resume_task)
    return {
        "runId": pending_interaction.run_id,
        "sessionId": pending_interaction.session_id,
        "interactionId": pending_interaction.id,
    }


async def _drain_resume_stream(stream) -> None:
    """消费结构化 resume 源流，让事件进入原 Run 的投影序列。"""

    try:
        async for _ in stream:
            pass
    except Exception:
        # 后台恢复流内部已经尽量发出 RUN_FAILED/ERROR；这里兜底避免 task 异常泄漏。
        return


def _external_results_preview(external_results: dict[str, str]) -> dict[str, str]:
    """返回外部执行/AskUser 结果预览，供过程面板展示用户实际传回模型的信息。"""

    return {
        str(key): _trim_text(str(value or ""), 2000)
        for key, value in external_results.items()
        if str(key).strip()
    }
