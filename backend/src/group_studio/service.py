"""群聊工作室应用服务：人类 API 编排。

本文件只编排进群、成员、置顶、发言、暂停、结束。
队列算法在 `speaker_queue`，点火在 `scheduler`，硬规则在 `orchestrator`。
"""

from __future__ import annotations

import uuid
from pathlib import Path

from src.group_studio.errors import (
    AGENT_NOT_BOUND,
    CHAT_RUN_ACTIVE,
    CROSS_PROJECT_SESSION,
    DEFAULT_AGENT_FORBIDDEN,
    GROUP_STUDIO_NOT_FOUND,
    ALREADY_MEMBER,
    HOST_REQUIRED,
    ILLEGAL_TRANSITION,
    JOB_AGENT_FORBIDDEN,
    MEMBER_CAP,
    NO_ELIGIBLE_SPEAKER,
    NO_LAST_TURN,
    STUDIO_BUSY,
    STUDIO_NOT_OPEN,
    GroupStudioError,
)
from src.group_studio.files import ensure_studio_dirs, save_studio_upload
from src.group_studio.models import (
    GROUP_STUDIO_HOST_AGENT_ID,
    GroupApproval,
    GroupArtifact,
    GroupMemberCreateRequest,
    GroupMessage,
    GroupStudio,
    GroupStudioFromChatRequest,
    GroupTaskCardUpdateRequest,
    Membership,
    TaskCard,
)
from src.group_studio.orchestrator import (
    assert_can_mutate,
    max_members,
)
from src.group_studio.settings import get_group_studio_settings_public
from src.runtime_context import resolve_runtime_context
from src.runtime.chat_session import (
    ACTIVE_RUN_STATUSES,
    ChatSessionProjectConflict,
    ensure_chat_session,
    find_chat_session,
)
from src.group_studio.events import emit_envelope
from src.group_studio.scheduler import schedule_next
from src.storage.database import (
    apply_group_studio_task_card_update,
    archive_group_studio,
    count_group_studio_active_members,
    create_group_studio_from_chat,
    find_group_studio_member_by_agent,
    get_active_group_studio_by_chat_session,
    get_group_studio_member,
    get_group_studio_snapshot,
    insert_group_studio_artifact,
    insert_group_studio_member,
    insert_group_studio_message,
    list_chat_runs_by_statuses,
    list_group_studio_artifacts,
    list_group_studios_by_project,
    list_group_studio_messages,
    set_group_studio_last_turn,
    set_group_studio_member_muted,
    set_group_studio_member_removed,
    set_group_studio_member_status,
    update_group_studio_model_config,
    update_group_studio_state,
)


def _new_id(prefix: str) -> str:
    """生成带前缀的短 id。"""

    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _raise_if_chat_run_active(session_id: str) -> None:
    """Chat 进行中禁止升级群聊。"""

    for run in list_chat_runs_by_statuses(ACTIVE_RUN_STATUSES):
        if str(run.get("sessionId") or "") == session_id:
            raise GroupStudioError(409, CHAT_RUN_ACTIVE)


def _resolve_worker(agent_id: str) -> dict[str, str]:
    """校验可入座的 Chat Agent。系统 Agent 与 default 不要求租户内注册。"""

    if agent_id == "default":
        return {"id": "default", "name": "默认助手", "role": "chat"}
    from src.server.routes.agents import get_registered_agent

    agent = get_registered_agent(agent_id)
    if agent is None:
        raise GroupStudioError(403, AGENT_NOT_BOUND)
    if agent.role == "job":
        raise GroupStudioError(403, JOB_AGENT_FORBIDDEN)
    return {
        "id": agent_id,
        "name": agent.name or agent_id,
        "role": agent.role,
    }


def list_invitable_agents(group_id: str) -> list[dict[str, str]]:
    """列出当前 Workspace 里还能拉进本群的普通 Agent。"""

    studio = _load_studio(group_id)
    occupied = {
        str(member.agent_id)
        for member in studio.members
        if member.status != "removed"
    }
    occupied.add("default")
    occupied.add(GROUP_STUDIO_HOST_AGENT_ID)
    candidates: list[dict[str, str]] = []
    from src.server.routes.agents import list_registered_agents

    for agent in list_registered_agents():
        agent_id = agent.id
        if not agent_id or agent_id in occupied:
            continue
        if agent.role == "job":
            continue
        candidates.append({
            "id": agent_id,
            "name": agent.name or agent_id,
            "description": agent.description or "",
        })
    return candidates


def _snapshot_to_model(raw: dict) -> GroupStudio:
    """存储快照转对外模型。"""

    return GroupStudio.model_validate(raw)


def get_studio(group_id: str) -> GroupStudio:
    """按 id 取房间；不存在则 404。"""

    raw = get_group_studio_snapshot(group_id)
    if raw is None:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    return _snapshot_to_model(raw)


def get_studio_by_chat_session(session_id: str) -> GroupStudio | None:
    """当前 Chat session 未结束的房间；没有则 None。"""

    raw = get_active_group_studio_by_chat_session(session_id)
    if raw is None:
        return None
    return _snapshot_to_model(raw)


def list_studios(project_slug: str) -> list[dict]:
    """群聊侧栏列表；不经过 Chat catalog。"""

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


def _maybe_seat_worker(agent_id: str) -> dict[str, str] | None:
    """default / Job / Host 不能入座。"""

    if not agent_id or agent_id in {"default", GROUP_STUDIO_HOST_AGENT_ID}:
        return None
    try:
        return _resolve_worker(agent_id)
    except GroupStudioError:
        return None


def archive_studio_for_session_delete(group_id: str) -> None:
    """删除 Chat session 时归档工作室。"""

    from src.group_studio.sessions import drop_group_tool_contexts

    archive_group_studio(group_id)
    drop_group_tool_contexts(group_id)


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


def _load_studio(group_id: str) -> GroupStudio:
    """内部读房间别名。"""

    return get_studio(group_id)


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


def add_member(group_id: str, request: GroupMemberCreateRequest) -> Membership:
    """加人。open/paused 都可；不加入当前进行中的队列。"""

    studio = _load_studio(group_id)
    if request.project_slug and request.project_slug != studio.project_slug:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION)
    assert_can_mutate(studio.status)
    if request.agent_id == "default":
        raise GroupStudioError(403, DEFAULT_AGENT_FORBIDDEN)
    if request.agent_id == GROUP_STUDIO_HOST_AGENT_ID:
        raise GroupStudioError(409, HOST_REQUIRED)

    worker = _resolve_worker(request.agent_id)
    existing = find_group_studio_member_by_agent(group_id, worker["id"])
    if existing is not None:
        if str(existing.get("status") or "") == "crashed":
            row = set_group_studio_member_status(
                group_id,
                str(existing["id"]),
                "active",
                refresh_joined_at=True,
            )
            membership = Membership.model_validate(row or existing)
            emit_envelope(
                group_id=group_id,
                event_type="GROUP_MEMBER",
                payload={**membership.model_dump(by_alias=True), "change": "reactivated"},
            )
            return membership
        raise GroupStudioError(409, ALREADY_MEMBER)

    if count_group_studio_active_members(group_id) >= max_members():
        raise GroupStudioError(409, MEMBER_CAP)

    row = insert_group_studio_member(group_id, {
        "id": _new_id("gmbr"),
        "role": "worker",
        "agentId": worker["id"],
        "displayName": worker["name"],
        "status": "active",
        "muted": False,
        "crashCount": 0,
    })
    membership = Membership.model_validate(row)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_MEMBER",
        payload={**membership.model_dump(by_alias=True), "change": "joined"},
    )
    return membership


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
    from src.group_studio.interrupt import cancel_active_run
    from src.group_studio.notify import KIND_COMPLETED, notify_studio
    from src.group_studio.speaker_queue import clear_queue

    cancel_active_run(group_id, source="pause")
    clear_queue(group_id, kind="interrupt")
    archive_group_studio(group_id, status="done")
    from src.group_studio.sessions import drop_group_tool_contexts

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
    from src.group_studio.archive_memory import schedule_archive_memory

    schedule_archive_memory(snapshot)
    return snapshot


def decide_studio_approval(
    group_id: str,
    *,
    project_slug: str,
    approval_id: str,
    decision: str,
) -> GroupApproval:
    from src.group_studio.approvals import decide_approval

    return decide_approval(
        group_id,
        project_slug=project_slug,
        approval_id=approval_id,
        decision=decision,
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


def interrupt_run(group_id: str, *, project_slug: str) -> None:
    """只停不说话。无活动 Run 视为成功。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_slug)
    from src.group_studio.interrupt import cancel_active_run

    cancel_active_run(group_id, source="stop_button")
    from src.group_studio.speaker_queue import clear_queue, reset_wave_spoken

    clear_queue(group_id, kind="interrupt")
    reset_wave_spoken(group_id)


def rerun_last_turn(group_id: str, *, project_slug: str) -> GroupStudio:
    """按上一波人类点名重跑，不写新的人类气泡。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_slug)
    if studio.status != "open":
        raise GroupStudioError(409, STUDIO_NOT_OPEN)
    if studio.current_run_id or studio.speaker_queue:
        raise GroupStudioError(409, STUDIO_BUSY)
    last_turn = studio.last_turn
    if last_turn is None:
        raise GroupStudioError(409, NO_LAST_TURN)
    from src.group_studio.speaker_queue import build_turn_queue, reset_wave_spoken, set_queue

    snapshot = get_group_studio_snapshot(group_id) or {}
    queue, _kind = build_turn_queue(snapshot, list(last_turn.mentions))
    if not queue:
        raise GroupStudioError(409, NO_ELIGIBLE_SPEAKER)
    reset_wave_spoken(group_id)
    set_queue(group_id, queue, kind="rerun")
    schedule_next(group_id)
    return get_studio(group_id)


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


def _assert_project(studio: GroupStudio, project_slug: str) -> None:
    """请求 project_slug 必须与房间一致。"""

    if project_slug and project_slug != studio.project_slug:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION)


def mute_member(
    group_id: str,
    member_id: str,
    muted: bool,
    *,
    project_slug: str = "",
    run_id: str | None = None,
) -> Membership:
    """静音或唤醒工人。主持人不可静音。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_slug)
    assert_can_mutate(studio.status)
    member = get_group_studio_member(group_id, member_id)
    if member is None or member.get("status") == "removed":
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    if member.get("role") == "host":
        raise GroupStudioError(409, HOST_REQUIRED)
    updated = set_group_studio_member_muted(group_id, member_id, muted)
    if updated is None:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    membership = Membership.model_validate(updated)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_MEMBER",
        run_id=run_id,
        payload={**membership.model_dump(by_alias=True), "change": "muted" if muted else "unmuted"},
    )
    if muted:
        from src.group_studio.speaker_queue import drop_member

        drop_member(group_id, member_id)
        if studio.current_run_id:
            from src.group_studio.interrupt import cancel_active_run

            cancel_active_run(group_id, source="stop_button")
            from src.group_studio.scheduler import schedule_next

            schedule_next(group_id)
    return membership


def remove_member(
    group_id: str,
    member_id: str,
    *,
    project_slug: str = "",
    run_id: str | None = None,
) -> Membership:
    """移除工人。主持人不可移除。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_slug)
    assert_can_mutate(studio.status)
    member = get_group_studio_member(group_id, member_id)
    if member is None:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    if member.get("role") == "host":
        raise GroupStudioError(409, HOST_REQUIRED)
    if member.get("status") == "removed":
        return Membership.model_validate(member)
    updated = set_group_studio_member_removed(group_id, member_id)
    if updated is None:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    membership = Membership.model_validate(updated)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_MEMBER",
        run_id=run_id,
        payload={**membership.model_dump(by_alias=True), "change": "removed"},
    )
    from src.group_studio.speaker_queue import drop_member

    drop_member(group_id, member_id)
    if studio.current_run_id:
        from src.group_studio.interrupt import cancel_active_run

        cancel_active_run(group_id, source="stop_button")
        from src.group_studio.scheduler import schedule_next

        schedule_next(group_id)
    return membership


def list_studio_files(group_id: str) -> list[dict]:
    """列出工作室产物。"""

    _load_studio(group_id)
    return list_group_studio_artifacts(group_id)


def update_studio_model(group_id: str, project_slug: str, model_config_id: str) -> GroupStudio:
    """无当前 Run 时允许切换模型。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_slug)
    if studio.status not in {"open", "paused"} or studio.current_run_id:
        raise GroupStudioError(409, ILLEGAL_TRANSITION)
    from src.config.runtime_settings import resolve_model_binding
    resolve_model_binding(model_config_id)
    return _snapshot_to_model(update_group_studio_model_config(group_id, model_config_id))


def resolve_studio_file(group_id: str, artifact_id: str) -> dict[str, object]:
    """解析工作室产物，限制在该会话的 group-studios 目录内。"""

    studio = _load_studio(group_id)
    artifact = next(
        (item for item in list_group_studio_artifacts(group_id) if str(item.get("id")) == artifact_id),
        None,
    )
    if artifact is None:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    studio_root = ensure_studio_dirs(studio.chat_session_id, group_id).resolve()
    candidate = (studio_root / str(artifact.get("path") or "")).resolve()
    if not candidate.is_relative_to(studio_root) or not candidate.is_file():
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    return {**artifact, "absolutePath": candidate}


async def upload_studio_file(
    group_id: str,
    upload,
    *,
    project_slug: str = "",
    created_by: str = "human",
) -> GroupArtifact:
    """上传到工作室 inputs/。产物入库；不再写黑板。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_slug)
    assert_can_mutate(studio.status)
    stored = await save_studio_upload(
        session_id=studio.chat_session_id,
        group_id=group_id,
        upload=upload,
    )
    row = insert_group_studio_artifact({
        "id": _new_id("gart"),
        "groupId": group_id,
        "title": stored["title"],
        "path": stored["path"],
        "mediaType": stored["mediaType"],
        "byteSize": stored["byteSize"],
        "createdBy": created_by,
    })
    artifact = GroupArtifact.model_validate(row)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_FILE",
        payload=artifact.model_dump(by_alias=True),
    )
    return artifact
