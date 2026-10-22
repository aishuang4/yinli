"""进群、读取、暂停、结束、置顶和改模型。"""
from __future__ import annotations

from pathlib import Path

from src.group_studio.access import _assert_project, _load_studio, _new_id, _snapshot_to_model, get_studio
from src.group_studio.errors import (
    CHAT_RUN_ACTIVE,
    CROSS_PROJECT_SESSION,
    GROUP_STUDIO_NOT_FOUND,
    ILLEGAL_TRANSITION,
    STUDIO_NOT_OPEN,
    GroupStudioError,
)
from src.group_studio.events import emit_envelope
from src.group_studio.files import ensure_studio_dirs
from src.group_studio.models import (
    GROUP_STUDIO_HOST_AGENT_ID,
    GroupStudio,
    GroupStudioFromChatRequest,
    GroupTaskCardUpdateRequest,
    TaskCard,
)
from src.group_studio.orchestrator import assert_can_mutate
from src.group_studio.settings import get_group_studio_settings_public
from src.runtime_context import resolve_runtime_context
from src.storage.records.chat_run_records import list_chat_runs_by_statuses
from src.storage.records.group_studio_records import (
    apply_group_studio_task_card_update,
    archive_group_studio,
    create_group_studio_from_chat,
    get_active_group_studio_by_chat_session,
    get_group_studio_snapshot,
    list_group_studio_messages,
    list_group_studios_by_project,
    update_group_studio_model_config,
    update_group_studio_state,
)


def _raise_if_chat_run_active(session_id: str) -> None:
    """Chat 进行中禁止升级群聊。"""

    from src.runtime.chat_session import ACTIVE_RUN_STATUSES

    for run in list_chat_runs_by_statuses(ACTIVE_RUN_STATUSES):
        if str(run.get("sessionId") or "") == session_id:
            raise GroupStudioError(409, CHAT_RUN_ACTIVE)


def _maybe_seat_worker(agent_id: str) -> dict[str, str] | None:
    """default / Job / Host 不能入座。"""

    if not agent_id or agent_id in {"default", GROUP_STUDIO_HOST_AGENT_ID}:
        return None
    from src.group_studio.members import _resolve_worker

    try:
        return _resolve_worker(agent_id)
    except GroupStudioError:
        return None


def get_studio_by_chat_session(session_id: str) -> GroupStudio | None:
    """当前 Chat session 未结束的房间；没有则 None。"""

    raw = get_active_group_studio_by_chat_session(session_id)
    if raw is None:
        return None
    return _snapshot_to_model(raw)


def list_studios(project_slug: str) -> list[dict]:
    """群聊侧栏列表。"""

    return list_group_studios_by_project(project_slug=project_slug)


def list_studio_messages(
    group_id: str,
    *,
    before: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """分页读时间线。"""

    if get_group_studio_snapshot(group_id, message_limit=1) is None:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    return list_group_studio_messages(group_id, before=before, limit=limit)


def from_chat(request: GroupStudioFromChatRequest) -> GroupStudio:
    """升级或回到当前 Chat 会话对应的未结束房间。进群即 open，不自动开跑。"""

    session_id = request.chat_session_id.strip()
    from src.config.runtime_settings import resolve_model_binding
    from src.runtime.chat_session import (
        ChatSessionProjectConflict,
        ensure_chat_session,
        find_chat_session,
    )

    session = find_chat_session(session_id)
    model_binding = resolve_model_binding(request.model_config_id or (session.model_config_id if session else None), validate=False)
    _raise_if_chat_run_active(session_id)

    existing = get_active_group_studio_by_chat_session(session_id)
    if existing is not None:
        if request.project_slug and str(existing.get("projectSlug") or "") != request.project_slug:
            raise GroupStudioError(422, CROSS_PROJECT_SESSION)
        return _snapshot_to_model(existing)

    try:
        context = resolve_runtime_context(
            project_slug=request.project_slug or None,
            session_id=session_id,
            run_id="",
            agent_id=request.agent_id or "default",
        )
    except ValueError as exc:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION) from exc

    seated = _maybe_seat_worker(request.agent_id)
    session_agent = seated or {"id": "default", "name": "默认助手"}

    try:
        ensure_chat_session(
            session_id=session_id,
            agent_id=session_agent["id"],
            agent_name=session_agent["name"],
            project_slug=context.project_slug,
            cwd=str(context.project_root),
            title=request.title or request.draft_text,
        )
    except ChatSessionProjectConflict as exc:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION) from exc

    try:
        context = resolve_runtime_context(
            project_slug=request.project_slug,
            session_id=session_id,
            run_id="",
            agent_id=session_agent["id"],
        )
    except ValueError as exc:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION) from exc

    title = (request.title or "").strip() or "未命名工作室"
    settings = get_group_studio_settings_public()
    group_id = _new_id("gstu")
    task_id = _new_id("gtask")
    artifacts = []
    for path in request.file_paths:
        cleaned = str(path or "").strip()
        if not cleaned:
            continue
        artifacts.append({
            "id": _new_id("gart"),
            "path": cleaned,
            "title": Path(cleaned).name,
            "mediaType": "",
            "byteSize": 0,
            "createdBy": "human",
        })
    members = []
    if seated:
        members.append({
            "id": _new_id("gmbr"),
            "role": "worker",
            "agentId": seated["id"],
            "displayName": seated["name"],
            "status": "active",
            "muted": False,
            "crashCount": 0,
        })
    payload = {
        "id": group_id,
        "projectSlug": context.project_slug,
        "chatSessionId": session_id,
        "modelConfigId": model_binding.primary.entry.id,
        "title": title,
        "status": "open",
        "budget": {
            "maxTurns": int(settings["maxTurns"]),
            "maxNoProgressTurns": 3,
            "usedTurns": 0,
            "noProgressTurns": 0,
        },
        "taskCard": {
            "id": task_id,
            "revision": 1,
            "status": "active",
            "goal": "",
            "background": "",
            "constraints": [],
            "dodChecklist": [],
            "seedFromChat": False,
        },
        "blackboard": {
            "revision": 1,
            "goal": "",
            "constraints": [],
            "decisions": [],
            "openQuestions": [],
            "artifacts": [item["id"] for item in artifacts],
        },
        "members": members,
        "artifacts": artifacts,
    }
    snapshot = create_group_studio_from_chat(payload)
    ensure_studio_dirs(session_id, snapshot["id"])
    studio = _snapshot_to_model(snapshot)
    emit_envelope(
        group_id=studio.id,
        event_type="GROUP_STATUS",
        payload={"from": None, "to": "open", "reason": "created"},
    )
    return studio


def delete_studio(group_id: str, *, project_slug: str) -> None:
    """删除群聊：标为 deleted，不再出现在群聊历史。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_slug)
    if studio.current_run_id:
        raise GroupStudioError(409, ILLEGAL_TRANSITION)
    if studio.status == "deleted":
        return
    archive_group_studio(group_id, status="deleted")
    from src.group_studio.sessions import drop_group_tool_contexts

    drop_group_tool_contexts(group_id)


def update_task_card(group_id: str, request: GroupTaskCardUpdateRequest) -> TaskCard:
    """更新置顶备注。不切状态、不取消当前生成。"""

    studio = _load_studio(group_id)
    if request.project_slug and request.project_slug != studio.project_slug:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION)
    assert_can_mutate(studio.status)

    card = studio.task_card
    next_goal = card.goal if request.goal is None else request.goal
    next_background = card.background if request.background is None else request.background
    next_constraints = card.constraints if request.constraints is None else request.constraints
    snapshot = apply_group_studio_task_card_update(
        group_id,
        goal=next_goal.strip(),
        background=next_background,
        constraints=next_constraints,
        dod_checklist=[item.model_dump(by_alias=True) for item in card.dod_checklist],
        card_status="active",
        studio_status=studio.status,
        awaiting_restatement=False,
        phase_before_pause=studio.phase_before_pause,
        bump_revision=True,
        title=studio.title,
    )
    if not snapshot:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    updated = _snapshot_to_model(snapshot)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_PIN",
        payload=updated.pin.model_dump(by_alias=True),
    )
    return updated.task_card


def close_studio(group_id: str, *, project_slug: str, confirm: bool) -> GroupStudio:
    """confirm=true 结束房间为 done 并走归档记忆。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_slug)
    if studio.status == "done":
        return studio
    if studio.status not in {"open", "paused"}:
        raise GroupStudioError(409, STUDIO_NOT_OPEN)
    if not confirm:
        raise GroupStudioError(409, ILLEGAL_TRANSITION)
    from src.group_studio.archive_memory import schedule_archive_memory
    from src.group_studio.interrupt import cancel_active_run
    from src.group_studio.notify import KIND_COMPLETED, notify_studio
    from src.group_studio.sessions import drop_group_tool_contexts
    from src.group_studio.speaker_queue import clear_queue

    cancel_active_run(group_id, source="pause")
    clear_queue(group_id, kind="interrupt")
    archive_group_studio(group_id, status="done")
    drop_group_tool_contexts(group_id)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_STATUS",
        payload={"from": studio.status, "to": "done", "reason": "close"},
    )
    snapshot = get_studio(group_id)
    notify_studio(
        snapshot.model_dump(by_alias=True),
        kind=KIND_COMPLETED,
        body=studio.pin.goal or studio.title,
    )
    schedule_archive_memory(snapshot)
    return snapshot


def pause_studio(group_id: str, *, project_slug: str) -> GroupStudio:
    """暂停：取消当前 Run，保留队列。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_slug)
    if studio.status != "open":
        raise GroupStudioError(409, ILLEGAL_TRANSITION)
    from src.group_studio.interrupt import cancel_active_run

    cancel_active_run(group_id, source="pause")
    snapshot = update_group_studio_state(
        group_id,
        status="paused",
        phase_before_pause="user",
    )
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_STATUS",
        payload={"from": "open", "to": "paused", "reason": "human_pause"},
    )
    return _snapshot_to_model(snapshot)


def resume_studio(group_id: str, *, project_slug: str, max_turns: int | None = None) -> GroupStudio:
    """从暂停恢复。有残留队列就继续跑。"""

    from src.group_studio.scheduler import schedule_next

    studio = _load_studio(group_id)
    _assert_project(studio, project_slug)
    if studio.status != "paused":
        raise GroupStudioError(409, ILLEGAL_TRANSITION)
    budget = studio.budget.model_dump(by_alias=True)
    budget["noProgressTurns"] = 0
    if studio.pause_reason == "circuit" or studio.phase_before_pause == "circuit":
        budget["maxTurns"] = max(int(budget.get("maxTurns") or 40) + 20, int(max_turns or 0) or 0)
    elif max_turns is not None:
        budget["maxTurns"] = int(max_turns)
    snapshot = update_group_studio_state(
        group_id,
        status="open",
        clear_phase_before_pause=True,
        budget=budget,
    )
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_STATUS",
        payload={"from": "paused", "to": "open", "reason": "human_resume"},
    )
    updated = _snapshot_to_model(snapshot)
    schedule_next(group_id)
    return updated


def update_studio_model(group_id: str, project_slug: str, model_config_id: str) -> GroupStudio:
    """无当前 Run 时允许切换模型。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_slug)
    if studio.status not in {"open", "paused"} or studio.current_run_id:
        raise GroupStudioError(409, ILLEGAL_TRANSITION)
    from src.config.runtime_settings import resolve_model_binding
    resolve_model_binding(model_config_id)
    return _snapshot_to_model(update_group_studio_model_config(group_id, model_config_id))
