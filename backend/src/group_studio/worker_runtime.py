"""Worker 串行运行时。群聊内工具权限 BYPASS；禁止接入 Chat pending 字典。"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable

from agentscope.message import UserMsg

from src.group_studio.approvals import create_pending_approval
from src.group_studio.circuit import on_run_finished, progress_fingerprint
from src.group_studio.errors import FLOOR_NOT_GRANTED, MODEL_NOT_CONFIGURED, GroupStudioError
from src.group_studio.events import emit_envelope
from src.group_studio.orchestrator import assert_floor_allowed, assert_serial_run
from src.group_studio.sessions import (
    WorkerSession,
    drop_run_task,
    drop_session,
    register_run_task,
    register_session,
    remember_fingerprint,
    take_interrupt,
)
from src.group_studio.startup_checks import get_group_studio_model_error
from src.group_studio.text_events import message_text_delta
from src.group_studio.tools_worker import WORKER_STOP_TOOLS
from src.group_studio.turn_context import format_turn_context, note_member_spoke
from src.group_studio.worker_spec import build_worker_runtime_spec
from src.runtime.agent_spec import create_main_agent_from_spec
from src.runtime.governance import persist_agent_session_state, prepare_agent_governance, restore_agent_session_state
from src.runtime.user_note import append_user_note
from src.storage.records.group_studio_records import (
    get_group_studio_snapshot,
    insert_group_studio_message,
    insert_group_studio_run,
    set_group_studio_current_run_id,
    update_group_studio_message_body,
    update_group_studio_run_status,
)

AgentFactory = Callable[[Any], Any]


def _worker_user_message(
    snapshot: dict[str, Any],
    member: dict[str, Any],
    *,
    duty: str = "queue",
) -> str:
    pin = snapshot.get("pin") or snapshot.get("taskCard") or {}
    return (
        "轮到你发言。用 group_post_message 发到群里，然后结束。\n"
        f"置顶：{pin.get('goal') or pin.get('title') or '（无）'}\n"
        f"{format_turn_context(snapshot)}"
    )


async def run_worker_turn(
    group_id: str,
    member_id: str,
    *,
    agent_factory: AgentFactory | None = None,
    duty: str = "queue",
) -> None:
    """一次 Worker 发言固定工作室的模型连接。"""
    from src.config.runtime_settings import resolve_model_binding, model_binding_scope
    snapshot = get_group_studio_snapshot(group_id)
    if snapshot is None:
        return
    with model_binding_scope(resolve_model_binding(snapshot.get("modelConfigId"), validate=False)):
        await _run_worker_turn(group_id, member_id, agent_factory=agent_factory, duty=duty)


async def _run_worker_turn(
    group_id: str,
    member_id: str,
    *,
    agent_factory: AgentFactory | None = None,
    duty: str = "queue",
) -> None:
    """给指定工人一轮发言/执行权。"""

    snapshot = get_group_studio_snapshot(group_id)
    if snapshot is None:
        return
    assert_floor_allowed(str(snapshot.get("status") or ""), role="worker")
    assert_serial_run(snapshot.get("currentRunId"))
    member = next(
        (
            item for item in (snapshot.get("members") or [])
            if item.get("id") == member_id and item.get("status") != "removed"
        ),
        None,
    )
    if member is None or member.get("role") != "worker":
        from src.group_studio.speaker_queue import pop_head, pop_unrunnable_head

        pop_unrunnable_head(group_id)
        emit_envelope(group_id=group_id, event_type="GROUP_ERROR", payload={"code": "not_found", "message": "工人不存在"})
        return
    if member.get("muted"):
        from src.group_studio.speaker_queue import pop_unrunnable_head

        pop_unrunnable_head(group_id)
        emit_envelope(group_id=group_id, event_type="GROUP_ERROR", payload={"code": "member_muted", "message": "工人已静音"})
        return
    if agent_factory is None:
        model_error = get_group_studio_model_error(snapshot.get("modelConfigId"))
        if model_error:
            emit_envelope(
                group_id=group_id,
                event_type="GROUP_ERROR",
                payload={"code": MODEL_NOT_CONFIGURED, "message": model_error},
            )
            return

    spec, ctx, _tools = build_worker_runtime_spec(snapshot, member)
    run_id = f"grun_{uuid.uuid4().hex[:16]}"
    ctx.begin_run(run_id)
    from src.group_studio.speaker_queue import get_queue, pop_head

    queue_before = get_queue(group_id)
    if queue_before and queue_before[0] == member_id:
        pop_head(group_id, member_id)
        floor_reason = "mention" if len(queue_before) > 1 else "queue_next"
    elif queue_before and queue_before[0] != member_id:
        emit_envelope(
            group_id=group_id,
            event_type="GROUP_ERROR",
            payload={"code": FLOOR_NOT_GRANTED, "message": "当前没有你的发言权"},
        )
        return
    else:
        floor_reason = "queue_next"
    kind = "discussion"
    intent = "discuss"
    insert_group_studio_run({
        "id": run_id,
        "groupId": group_id,
        "kind": kind,
        "status": "running",
        "speakerMemberId": member_id,
    })
    set_group_studio_current_run_id(group_id, run_id)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_FLOOR",
        run_id=run_id,
        payload={"memberId": member_id, "runId": run_id, "reason": floor_reason},
    )
    message_id = f"gmsg_{uuid.uuid4().hex[:16]}"
    insert_group_studio_message({
        "id": message_id,
        "groupId": group_id,
        "runId": run_id,
        "speakerId": member_id,
        "role": "worker",
        "intent": intent,
        "body": "",
        "visibleInTimeline": True,
    })
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_MESSAGE_OPEN",
        run_id=run_id,
        payload={
            "messageId": message_id,
            "speakerId": member_id,
            "role": "worker",
            "intent": intent,
            "visibleInTimeline": True,
        },
    )
    factory = agent_factory or (lambda built_spec: create_main_agent_from_spec(built_spec))
    agent = factory(spec)
    # 工作室跨多轮：offloader 按 studio+member 分目录；实例重建时恢复压缩摘要与已读 Skill。
    prepare_agent_governance(agent, session_id=spec.session_id)
    if not list(getattr(getattr(agent, "state", None), "context", []) or []):
        restore_agent_session_state(agent, spec.session_id)
    recall_note = ""
    session = register_session(WorkerSession(
        group_id=group_id,
        run_id=run_id,
        member_id=member_id,
        agent=agent,
    ))
    body_parts: list[str] = []
    thinking_parts: list[str] = []
    outcome = "completed"
    run_status = "completed"
    error_text = ""
    remember_fingerprint(run_id, progress_fingerprint(snapshot))
    register_run_task(group_id, asyncio.current_task())
    try:
        next_input: Any = UserMsg(
            name="user",
            content=append_user_note(_worker_user_message(snapshot, member, duty=duty), recall_note),
        )
        last_chars_flush = 0
        while not session.cancelled:
            paused = False
            async for raw_event in agent.reply_stream(next_input):
                data = raw_event.model_dump(mode="json") if hasattr(raw_event, "model_dump") else dict(raw_event)
                event_type = str(data.get("type") or "")
                text_delta = message_text_delta(data)
                if text_delta is not None:
                    kind, delta = text_delta
                    target = thinking_parts if kind == "thinking" else body_parts
                    target.append(delta)
                    emit_envelope(
                        group_id=group_id,
                        event_type="GROUP_MESSAGE_DELTA",
                        run_id=run_id,
                        persist=False,
                        payload={"messageId": message_id, "text": delta, "kind": kind},
                    )
                    if kind == "answer":
                        joined = "".join(body_parts)
                        if len(joined) - last_chars_flush >= 256:
                            update_group_studio_message_body(
                                message_id,
                                joined,
                                thinking="".join(thinking_parts),
                            )
                            last_chars_flush = len(joined)
                elif event_type in {"TOOL_RESULT_START", "TOOL_CALL", "TOOL_START"}:
                    emit_envelope(
                        group_id=group_id,
                        event_type="GROUP_ACTIVITY_OPEN",
                        run_id=run_id,
                        payload={
                            "runId": run_id,
                            "memberId": member_id,
                            "toolName": str(data.get("tool_call_name") or data.get("name") or ""),
                        },
                    )
                elif event_type == "TOOL_RESULT_DELTA":
                    emit_envelope(
                        group_id=group_id,
                        event_type="GROUP_ACTIVITY_DELTA",
                        run_id=run_id,
                        persist=False,
                        payload={
                            "runId": run_id,
                            "memberId": member_id,
                            "text": str(data.get("delta") or ""),
                        },
                    )
                elif event_type == "TOOL_RESULT_END":
                    tool_name = str(data.get("tool_call_name") or data.get("name") or "")
                    emit_envelope(
                        group_id=group_id,
                        event_type="GROUP_ACTIVITY_CLOSE",
                        run_id=run_id,
                        payload={"runId": run_id, "memberId": member_id, "toolName": tool_name},
                    )
                    failed = str(data.get("state") or "").lower() in {"error", "failed", "denied"}
                    if not failed and tool_name in WORKER_STOP_TOOLS:
                        break
                elif event_type == "REQUIRE_USER_CONFIRM":
                    reply_id = str(data.get("reply_id") or data.get("replyId") or "")
                    tool_calls = list(data.get("tool_calls") or data.get("toolCalls") or [])
                    create_pending_approval(
                        group_id=group_id,
                        run_id=run_id,
                        member_id=member_id,
                        reply_id=reply_id,
                        tool_calls=tool_calls,
                    )
                    loop = asyncio.get_running_loop()
                    session.resume = loop.create_future()
                    resumed = await session.resume
                    if resumed is None or session.cancelled:
                        outcome = "cancelled"
                        run_status = "cancelled"
                        error_text = "cancelled"
                        paused = False
                        next_input = None
                        break
                    next_input = resumed
                    paused = True
                    update_group_studio_run_status(run_id, "running")
                    break
                elif event_type == "EXCEED_MAX_ITERS":
                    outcome = "failed"
                    run_status = "failed"
                    error_text = "worker_max_iters"
                    break
                elif event_type == "REPLY_END":
                    reason = str(data.get("finished_reason") or "completed").lower()
                    if reason not in {"completed", ""}:
                        outcome = "failed"
                        run_status = "failed"
                        error_text = reason
            if paused and next_input is not None:
                continue
            break
        if ctx.last_stop_tool:
            outcome = "completed"
            run_status = "completed"
        if ctx.posted_body:
            body_parts = [ctx.posted_body]
    except GroupStudioError:
        raise
    except asyncio.CancelledError:
        outcome = "cancelled"
        run_status = "cancelled"
        error_text = "cancelled"
    except Exception as exc:
        outcome = "failed"
        run_status = "failed"
        from src.exception_detail import format_agent_exception

        error_text = format_agent_exception(exc, prefix="群聊 Worker 执行失败")
        emit_envelope(
            group_id=group_id,
            event_type="GROUP_ERROR",
            run_id=run_id,
            payload={"code": "worker_failed", "message": error_text},
        )
    finally:
        persist_agent_session_state(agent, session_id=spec.session_id, agent_id=spec.parent_agent_id)
        drop_session(group_id, member_id, run_id)
        drop_run_task(group_id)

    body = "".join(body_parts)
    thinking = "".join(thinking_parts)
    update_group_studio_message_body(message_id, body, thinking)
    note_member_spoke(group_id, member_id, role="worker", body=body)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_MESSAGE_CLOSE",
        run_id=run_id,
        payload={"messageId": message_id, "body": body, "thinking": thinking, "outcome": outcome},
    )
    interrupted = take_interrupt(group_id)
    if interrupted:
        return
    if run_status in {"completed", "failed"}:
        from src.group_studio.speaker_queue import enqueue_handoff, note_wave_spoken

        note_wave_spoken(group_id, member_id)
        if run_status == "completed" and ctx.posted_next_member_id:
            enqueue_handoff(group_id, member_id, ctx.posted_next_member_id)
    if run_status == "completed" and body.strip():
        from src.config.runtime_settings import get_effective_model_settings
        from src.memory.professional_update import schedule_professional_update

        pin = snapshot.get("pin") or snapshot.get("taskCard") or {}
        schedule_professional_update(
            agent_id=str(spec.parent_agent_id or ""),
            agent_name=str(spec.parent_agent_name or ""),
            user_message=str(pin.get("goal") or pin.get("title") or ""),
            assistant_text=body,
            model_name=get_effective_model_settings().default_model,
            session_id=str(snapshot.get("chatSessionId") or ""),
        )
    update_group_studio_run_status(run_id, run_status, error_text)
    set_group_studio_current_run_id(group_id, None)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_RUN_ENDED",
        run_id=run_id,
        payload={"runId": run_id, "status": run_status},
    )
    on_run_finished(
        group_id,
        run_id=run_id,
        run_status=run_status,
        interrupt_source=interrupted,
        duty=duty,
    )
