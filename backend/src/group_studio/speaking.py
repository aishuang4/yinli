"""人类发言、打断和重跑。队列算法在 `speaker_queue`，点火在 `scheduler`。"""
from __future__ import annotations

from src.group_studio.access import _assert_project, _load_studio, _new_id, get_studio
from src.group_studio.errors import (
    NO_ELIGIBLE_SPEAKER,
    NO_LAST_TURN,
    STUDIO_BUSY,
    STUDIO_NOT_OPEN,
    GroupStudioError,
)
from src.group_studio.events import emit_envelope
from src.group_studio.models import GroupMessage, GroupStudio
from src.group_studio.scheduler import schedule_next
from src.storage.records.group_studio_records import (
    apply_group_studio_task_card_update,
    get_group_studio_snapshot,
    insert_group_studio_message,
    set_group_studio_last_turn,
    update_group_studio_state,
)


def _emit_human_timeline_message(
    group_id: str,
    text: str,
    *,
    mentions: list[str] | None = None,
) -> GroupMessage:
    """写入一条人类可见消息并发 OPEN/CLOSE，不调度。"""

    row = insert_group_studio_message({
        "id": _new_id("gmsg"),
        "groupId": group_id,
        "speakerId": "human",
        "role": "human",
        "intent": "human_directive",
        "body": text,
        "mentions": mentions or [],
        "visibleInTimeline": True,
    })
    message = GroupMessage.model_validate(row)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_MESSAGE_OPEN",
        payload={
            "messageId": message.id,
            "speakerId": "human",
            "role": "human",
            "intent": "human_directive",
            "visibleInTimeline": True,
        },
    )
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_MESSAGE_CLOSE",
        payload={"messageId": message.id, "body": text, "outcome": "completed"},
    )
    return message


def _emit_system_notice(group_id: str, text: str) -> None:
    """时间线系统提示，不调度工人。"""

    row = insert_group_studio_message({
        "id": _new_id("gmsg"),
        "groupId": group_id,
        "speakerId": "system",
        "role": "system",
        "intent": "system_notice",
        "body": text,
        "visibleInTimeline": True,
    })
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_MESSAGE_OPEN",
        payload={
            "messageId": row["id"],
            "speakerId": "system",
            "role": "system",
            "intent": "system_notice",
            "visibleInTimeline": True,
        },
    )
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_MESSAGE_CLOSE",
        payload={"messageId": row["id"], "body": text, "outcome": "completed"},
    )


def post_human_message(group_id: str, *, project_slug: str, body: str, mentions: list[str] | None = None) -> GroupMessage:
    """人类输入：取消当前生成，按 mentions 重算队列，只跑 Worker。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_slug)
    if studio.status in {"done", "deleted"}:
        raise GroupStudioError(409, STUDIO_NOT_OPEN)
    from src.group_studio.interrupt import cancel_active_run
    from src.group_studio.speaker_queue import (
        build_turn_queue,
        clear_queue,
        reset_wave_spoken,
        set_queue,
    )

    if studio.status == "paused":
        update_group_studio_state(group_id, status="open", clear_phase_before_pause=True)
        emit_envelope(
            group_id=group_id,
            event_type="GROUP_STATUS",
            payload={"from": "paused", "to": "open", "reason": "human_message"},
        )
        studio = get_studio(group_id)

    cancel_active_run(group_id, source="human_message")
    clear_queue(group_id, kind="interrupt")
    reset_wave_spoken(group_id)
    text = body.strip()
    if not text:
        raise GroupStudioError(422, "empty_body")
    mentions = [str(item).strip() for item in (mentions or []) if str(item).strip()]
    message = _emit_human_timeline_message(group_id, text, mentions=mentions)
    set_group_studio_last_turn(group_id, {
        "kind": "assign" if mentions else "all_round",
        "mentions": mentions,
    })
    if not studio.title or studio.title == "未命名工作室":
        apply_group_studio_task_card_update(
            group_id,
            goal=studio.task_card.goal,
            background=studio.task_card.background,
            constraints=list(studio.task_card.constraints),
            dod_checklist=[item.model_dump(by_alias=True) for item in studio.task_card.dod_checklist],
            card_status="active",
            studio_status="open",
            awaiting_restatement=False,
            phase_before_pause=None,
            bump_revision=False,
            title=text[:40] + ("..." if len(text) > 40 else ""),
        )

    snapshot = get_group_studio_snapshot(group_id) or {}
    queue, kind = build_turn_queue(snapshot, mentions)
    if mentions and not queue:
        emit_envelope(
            group_id=group_id,
            event_type="GROUP_ERROR",
            payload={"code": NO_ELIGIBLE_SPEAKER, "message": "点到的人现在不能发言（已静音、已移出或不在群里）。"},
        )
        _emit_system_notice(group_id, "点到的人现在不能发言（已静音、已移出或不在群里）。")
        return message
    if not mentions and not queue:
        _emit_system_notice(group_id, "还没有可以发言的成员，请先在右侧把人拉进来。")
        return message
    set_queue(group_id, queue, kind=kind)
    _defer_workers_until_habit(group_id, text)
    return message


def _defer_workers_until_habit(group_id: str, text: str) -> None:
    """习惯判断完成后再让工人开口，这样这一轮就能用上新习惯。"""

    import asyncio

    async def _run() -> None:
        try:
            from src.config.runtime_settings import get_effective_model_settings, model_binding_scope, resolve_model_binding
            from src.memory.habit_update import apply_habit_update

            studio = get_studio(group_id)
            model_config_id = str(getattr(studio, "model_config_id", "") or "")
            with model_binding_scope(resolve_model_binding(model_config_id, validate=False)):
                await apply_habit_update(
                    user_message=text,
                    model_name=get_effective_model_settings().default_model,
                    session_id=str(getattr(studio, "chat_session_id", "") or ""),
                    agent_id="default",
                    agent_name="习惯",
                )
        except Exception:
            import logging
            logging.getLogger(__name__).warning("群聊习惯判断失败", exc_info=True)
        schedule_next(group_id)

    from src.tenancy.context import current_tenant, spawn_in_tenant

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _run.close()
        schedule_next(group_id)
        return
    spawn_in_tenant(current_tenant(), _run(), name=f"group-habit-{group_id}")


def interrupt_run(group_id: str, *, project_slug: str) -> None:
    """只停不说话。无活动 Run 视为成功。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_slug)
    from src.group_studio.interrupt import cancel_active_run
    from src.group_studio.speaker_queue import clear_queue, reset_wave_spoken

    cancel_active_run(group_id, source="stop_button")
    clear_queue(group_id, kind="interrupt")
    reset_wave_spoken(group_id)


def rerun_last_turn(group_id: str, *, project_slug: str) -> GroupStudio:
    """按上一波人类点名重跑，不写新的人类气泡。"""

    from src.group_studio.speaker_queue import build_turn_queue, reset_wave_spoken, set_queue

    studio = _load_studio(group_id)
    _assert_project(studio, project_slug)
    if studio.status != "open":
        raise GroupStudioError(409, STUDIO_NOT_OPEN)
    if studio.current_run_id or studio.speaker_queue:
        raise GroupStudioError(409, STUDIO_BUSY)
    last_turn = studio.last_turn
    if last_turn is None:
        raise GroupStudioError(409, NO_LAST_TURN)
    snapshot = get_group_studio_snapshot(group_id) or {}
    queue, _kind = build_turn_queue(snapshot, list(last_turn.mentions))
    if not queue:
        raise GroupStudioError(409, NO_ELIGIBLE_SPEAKER)
    reset_wave_spoken(group_id)
    set_queue(group_id, queue, kind="rerun")
    schedule_next(group_id)
    return get_studio(group_id)
