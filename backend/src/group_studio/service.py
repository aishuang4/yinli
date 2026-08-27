"""群聊工作室应用服务：绑定、任务卡、成员、黑板与文件库。"""

from __future__ import annotations

import uuid
from pathlib import Path

from src.group_studio.blackboard import merge_blackboard
from src.group_studio.errors import (
    AGENT_NOT_BOUND,
    CHAT_RUN_ACTIVE,
    CROSS_PROJECT_SESSION,
    DOD_INCOMPLETE,
    GOAL_REQUIRED,
    GROUP_STUDIO_NOT_FOUND,
    HOST_REQUIRED,
    ILLEGAL_TRANSITION,
    JOB_AGENT_FORBIDDEN,
    MEMBER_CAP,
    NOT_AWAITING_RESTATEMENT,
    RESTATEMENT_MISSING,
    STUDIO_CIRCUIT_BROKEN,
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
    assert_can_start_execution,
    max_members,
)
from src.group_studio.settings import get_group_studio_settings_public
from src.project_resources import resolve_project_resource_access
from src.runtime_context import resolve_runtime_context
from src.server.routes.chat.lifecycle import ACTIVE_RUN_STATUSES
from src.server.routes.chat.store import (
    ChatSessionProjectConflict,
    _load_store,
    ensure_chat_session,
)
from src.group_studio.events import emit_envelope
from src.group_studio.scheduler import schedule_host_turn
from src.storage.database import (
    apply_group_studio_task_card_update,
    archive_group_studio,
    count_group_studio_active_members,
    create_group_studio_from_chat,
    find_blank_group_studio_draft_id,
    find_group_studio_member_by_agent,
    get_active_group_studio_by_chat_session,
    get_group_studio_member,
    get_group_studio_snapshot,
    get_project,
    group_studio_has_runs,
    insert_group_studio_artifact,
    insert_group_studio_member,
    insert_group_studio_message,
    list_agents,
    list_chat_runs_by_statuses,
    list_group_studio_artifacts,
    list_group_studios_by_project,
    list_group_studio_messages,
    replace_group_studio_blackboard,
    set_group_studio_member_muted,
    set_group_studio_member_removed,
    set_group_studio_pending_speaker,
    set_group_studio_status,
    update_group_studio_state,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _raise_if_chat_run_active(session_id: str) -> None:
    for run in list_chat_runs_by_statuses(ACTIVE_RUN_STATUSES):
        if str(run.get("sessionId") or "") == session_id:
            raise GroupStudioError(409, CHAT_RUN_ACTIVE)


def _resolve_worker(agent_id: str) -> dict[str, str]:
    if agent_id == "default":
        return {"id": "default", "name": "默认助手", "role": "chat"}
    for agent in list_agents():
        if str(agent.get("id") or "") != agent_id:
            continue
        role = str(agent.get("role") or "chat")
        if role == "job":
            raise GroupStudioError(403, JOB_AGENT_FORBIDDEN)
        return {
            "id": agent_id,
            "name": str(agent.get("name") or agent_id),
            "role": role,
        }
    raise GroupStudioError(403, AGENT_NOT_BOUND)


def list_invitable_agents(group_id: str) -> list[dict[str, str]]:
    """列出当前项目里还能拉进本群的 Agent（已在群内 / Job / Host 哨兵除外）。"""

    studio = _load_studio(group_id)
    occupied = {
        str(member.agent_id)
        for member in studio.members
        if member.status != "removed"
    }
    occupied.add(GROUP_STUDIO_HOST_AGENT_ID)
    try:
        context = resolve_runtime_context(
            project_id=studio.project_id,
            session_id=studio.chat_session_id,
            run_id="",
            agent_id="default",
        )
        access = resolve_project_resource_access(context)
        allowed = access.agent_ids
    except ValueError:
        allowed = None
    candidates: list[dict[str, str]] = []
    for agent in list_agents():
        agent_id = str(agent.get("id") or "")
        if not agent_id or agent_id in occupied:
            continue
        if str(agent.get("role") or "chat") == "job":
            continue
        if agent_id != "default" and allowed is not None and agent_id not in allowed:
            continue
        candidates.append({
            "id": agent_id,
            "name": str(agent.get("name") or agent_id),
            "description": str(agent.get("description") or ""),
        })
    return candidates


def _last_user_message(session_id: str) -> str:
    store = _load_store()
    messages = store.messages.get(session_id) or []
    for message in reversed(messages):
        if message.role == "user" and message.content.strip():
            return message.content.strip()
    return ""


def _message_count(session_id: str) -> int:
    store = _load_store()
    session = next((item for item in store.sessions if item.id == session_id), None)
    if session is not None:
        return int(session.message_count or 0)
    return len(store.messages.get(session_id) or [])


def _snapshot_to_model(raw: dict) -> GroupStudio:
    return GroupStudio.model_validate(raw)


def get_studio(group_id: str) -> GroupStudio:
    raw = get_group_studio_snapshot(group_id)
    if raw is None:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    return _snapshot_to_model(raw)


def get_studio_by_chat_session(session_id: str) -> GroupStudio | None:
    raw = get_active_group_studio_by_chat_session(session_id)
    if raw is None:
        return None
    return _snapshot_to_model(raw)


def list_studios(project_id: str) -> list[dict]:
    """群聊侧栏列表；不经过 Chat catalog。"""

    return list_group_studios_by_project(project_id)


def list_studio_messages(
    group_id: str,
    *,
    before: str | None = None,
    limit: int = 100,
) -> list[dict]:
    if get_group_studio_snapshot(group_id, message_limit=1) is None:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    return list_group_studio_messages(group_id, before=before, limit=limit)


def from_chat(request: GroupStudioFromChatRequest) -> GroupStudio:
    """升级或回到当前 Chat 会话对应的未归档工作室。"""

    session_id = request.chat_session_id.strip()
    _raise_if_chat_run_active(session_id)

    existing = get_active_group_studio_by_chat_session(session_id)
    if existing is not None:
        if request.project_id and str(existing.get("projectId") or "") != request.project_id:
            raise GroupStudioError(422, CROSS_PROJECT_SESSION)
        return _snapshot_to_model(existing)

    if request.reuse_blank_draft and not request.draft_text.strip() and not request.file_paths:
        blank_id = find_blank_group_studio_draft_id(request.project_id)
        if blank_id:
            blank = get_group_studio_snapshot(blank_id)
            if blank is not None:
                return _snapshot_to_model(blank)

    worker = _resolve_worker(request.agent_id)
    try:
        context = resolve_runtime_context(
            project_id=request.project_id,
            session_id="",
            run_id="",
            agent_id=worker["id"],
        )
    except ValueError as exc:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION) from exc

    try:
        access = resolve_project_resource_access(context)
        access.require_agent(worker["id"])
    except ValueError as exc:
        raise GroupStudioError(403, AGENT_NOT_BOUND) from exc

    try:
        ensure_chat_session(
            session_id=session_id,
            agent_id=worker["id"],
            agent_name=worker["name"],
            workspace_id=context.workspace_id,
            project_id=context.project_id,
            title=request.title or request.draft_text,
        )
    except ChatSessionProjectConflict as exc:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION) from exc

    try:
        context = resolve_runtime_context(
            project_id=request.project_id,
            session_id=session_id,
            run_id="",
            agent_id=worker["id"],
        )
    except ValueError as exc:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION) from exc

    message_count = _message_count(session_id)
    draft = request.draft_text.strip()
    last_user = _last_user_message(session_id) if message_count >= 1 else ""
    if message_count >= 1:
        goal = last_user
        seed = True
    else:
        goal = draft
        seed = False
    studio_status = "discussing" if goal else "drafting"
    card_status = "active" if goal else "draft"
    title = (request.title or "").strip() or (goal[:28] + ("..." if len(goal) > 28 else "") if goal else "未命名工作室")
    settings = get_group_studio_settings_public()
    group_id = _new_id("gstu")
    task_id = _new_id("gtask")
    host_id = _new_id("gmbr")
    worker_id = _new_id("gmbr")
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
    payload = {
        "id": group_id,
        "workspaceId": context.workspace_id,
        "projectId": context.project_id,
        "chatSessionId": session_id,
        "title": title,
        "status": studio_status,
        "budget": {
            "maxTurns": int(settings["maxTurns"]),
            "maxNoProgressTurns": 3,
            "usedTurns": 0,
            "noProgressTurns": 0,
        },
        "taskCard": {
            "id": task_id,
            "revision": 1,
            "status": card_status,
            "goal": goal,
            "background": "",
            "constraints": [],
            "dodChecklist": [],
            "seedFromChat": seed,
        },
        "blackboard": {
            "revision": 1,
            "goal": goal,
            "constraints": [],
            "decisions": [],
            "openQuestions": [],
            "artifacts": [item["id"] for item in artifacts],
        },
        "members": [
            {
                "id": host_id,
                "role": "host",
                "agentId": GROUP_STUDIO_HOST_AGENT_ID,
                "displayName": "主持人",
                "status": "active",
                "muted": False,
                "crashCount": 0,
            },
            {
                "id": worker_id,
                "role": "worker",
                "agentId": worker["id"],
                "displayName": worker["name"],
                "status": "active",
                "muted": False,
                "crashCount": 0,
            },
        ],
        "artifacts": artifacts,
    }
    snapshot = create_group_studio_from_chat(payload)
    ensure_studio_dirs(context.project_root, snapshot["id"])
    studio = _snapshot_to_model(snapshot)
    if studio.status == "discussing" and studio.task_card.goal.strip():
        schedule_host_turn(studio.id)
    return studio


def archive_studio_for_session_delete(group_id: str) -> None:
    """删除 Chat session 时归档工作室。"""

    from src.group_studio.sessions import drop_group_tool_contexts

    archive_group_studio(group_id)
    drop_group_tool_contexts(group_id)


def _load_studio(group_id: str) -> GroupStudio:
    return get_studio(group_id)


def update_task_card(group_id: str, request: GroupTaskCardUpdateRequest) -> TaskCard:
    """按转移表更新任务卡。drafting 首次填 Goal 不 restatement。"""

    studio = _load_studio(group_id)
    if request.project_id and request.project_id != studio.project_id:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION)
    assert_can_mutate(studio.status)

    card = studio.task_card
    next_goal = card.goal if request.goal is None else request.goal
    next_background = card.background if request.background is None else request.background
    next_constraints = card.constraints if request.constraints is None else request.constraints
    next_dod = (
        [item.model_dump(by_alias=True) for item in card.dod_checklist]
        if request.dod_checklist is None
        else [item.model_dump(by_alias=True) for item in request.dod_checklist]
    )
    goal = next_goal.strip()
    old_goal = card.goal.strip()
    old_constraints = list(card.constraints)
    goal_changed = goal != old_goal
    constraints_changed = next_constraints != old_constraints
    background_changed = next_background != card.background
    dod_changed = request.dod_checklist is not None

    studio_status = studio.status
    card_status = card.status
    awaiting = studio.awaiting_restatement
    phase_before = studio.phase_before_pause
    bump = False
    title: str | None = None

    if studio.status == "drafting":
        if goal:
            studio_status = "discussing"
            card_status = "active"
            bump = True
            title = goal[:28] + ("..." if len(goal) > 28 else "")
        elif not (background_changed or dod_changed or constraints_changed):
            raise GroupStudioError(422, GOAL_REQUIRED)
    elif studio.status in {"discussing", "executing", "reviewing"}:
        semantic_change = goal_changed or constraints_changed
        had_old_target = bool(old_goal or old_constraints)
        if semantic_change and had_old_target and group_studio_has_runs(group_id):
            studio_status = "awaiting_human"
            awaiting = True
            phase_before = studio.status
            bump = True
        elif semantic_change or background_changed or dod_changed:
            bump = semantic_change or dod_changed
            if goal and studio.status == "discussing":
                title = goal[:28] + ("..." if len(goal) > 28 else "")
    elif studio.status == "awaiting_human":
        if semantic_change := (goal_changed or constraints_changed):
            bump = True
        elif background_changed or dod_changed:
            bump = dod_changed
        else:
            raise GroupStudioError(409, ILLEGAL_TRANSITION)
    elif studio.status == "closing":
        if goal_changed or constraints_changed:
            raise GroupStudioError(409, ILLEGAL_TRANSITION)
        bump = dod_changed
    else:
        raise GroupStudioError(409, ILLEGAL_TRANSITION)

    snapshot = apply_group_studio_task_card_update(
        group_id,
        goal=goal,
        background=next_background,
        constraints=next_constraints,
        dod_checklist=next_dod,
        card_status=card_status,
        studio_status=studio_status,
        awaiting_restatement=awaiting,
        phase_before_pause=phase_before,
        bump_revision=bump,
        title=title,
    )
    if not snapshot:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    updated = _snapshot_to_model(snapshot)
    if studio.status != updated.status:
        emit_envelope(
            group_id=group_id,
            event_type="GROUP_STATUS",
            payload={"from": studio.status, "to": updated.status, "reason": "task_card"},
        )
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_TASK_CARD",
        payload=updated.task_card.model_dump(by_alias=True),
    )
    if dod_changed:
        emit_envelope(
            group_id=group_id,
            event_type="GROUP_DOD",
            payload={
                "taskCardId": updated.task_card.id,
                "dodChecklist": [
                    item.model_dump(by_alias=True) for item in updated.task_card.dod_checklist
                ],
            },
        )
    if updated.status == "awaiting_human" and studio.status != "awaiting_human":
        from src.group_studio.interrupt import cancel_active_run
        from src.group_studio.notify import KIND_AWAITING, notify_studio

        cancel_active_run(group_id, source="task_card")
        notify_studio(updated.model_dump(by_alias=True), kind=KIND_AWAITING, body="目标已变更，请确认复述。")
        schedule_host_turn(group_id)
    if studio.status == "drafting" and updated.status == "discussing":
        schedule_host_turn(group_id)
    return updated.task_card


def add_member(group_id: str, request: GroupMemberCreateRequest) -> Membership:
    """加人：Project 白名单、Job 过滤、人数硬顶 6、Host 哨兵不可当 Worker。"""

    studio = _load_studio(group_id)
    if request.project_id and request.project_id != studio.project_id:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION)
    assert_can_mutate(studio.status)
    if request.agent_id == GROUP_STUDIO_HOST_AGENT_ID:
        raise GroupStudioError(409, HOST_REQUIRED)

    worker = _resolve_worker(request.agent_id)
    existing = find_group_studio_member_by_agent(group_id, worker["id"])
    if existing is not None:
        return Membership.model_validate(existing)

    if count_group_studio_active_members(group_id) >= max_members():
        raise GroupStudioError(409, MEMBER_CAP)

    try:
        context = resolve_runtime_context(
            project_id=studio.project_id,
            session_id=studio.chat_session_id,
            run_id="",
            agent_id=worker["id"],
        )
        resolve_project_resource_access(context).require_agent(worker["id"])
    except ValueError as exc:
        raise GroupStudioError(403, AGENT_NOT_BOUND) from exc

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


def prepare_start_execution(group_id: str, project_id: str) -> GroupStudio:
    """进入执行期。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_id)
    assert_can_start_execution(studio.status, studio.task_card.goal)
    set_group_studio_status(group_id, "executing")
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_STATUS",
        payload={"from": studio.status, "to": "executing", "reason": "human_start_execution"},
    )
    return get_studio(group_id)


def prepare_start_review(group_id: str, project_id: str) -> GroupStudio:
    """进入验收期。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_id)
    if studio.status != "executing":
        raise GroupStudioError(409, ILLEGAL_TRANSITION)
    set_group_studio_status(group_id, "reviewing")
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_STATUS",
        payload={"from": "executing", "to": "reviewing", "reason": "human_start_review"},
    )
    return get_studio(group_id)


def _dod_complete(studio: GroupStudio) -> bool:
    """未设验收清单视为已完成；有条目则必须全部勾选。"""

    items = studio.task_card.dod_checklist
    if not items:
        return True
    return all(item.checked for item in items)


def propose_close(group_id: str, *, project_id: str = "") -> GroupStudio:
    """进入 closing，任务卡冻结 Goal/约束，DoD 仍可勾。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_id)
    if studio.status not in {"reviewing", "discussing", "executing"}:
        raise GroupStudioError(409, ILLEGAL_TRANSITION)
    from src.group_studio.interrupt import cancel_active_run

    cancel_active_run(group_id, source="pause")
    apply_group_studio_task_card_update(
        group_id,
        goal=studio.task_card.goal,
        background=studio.task_card.background,
        constraints=list(studio.task_card.constraints),
        dod_checklist=[item.model_dump(by_alias=True) for item in studio.task_card.dod_checklist],
        card_status="frozen_for_close",
        studio_status="closing",
        awaiting_restatement=False,
        phase_before_pause=None,
        bump_revision=False,
    )
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_STATUS",
        payload={"from": studio.status, "to": "closing", "reason": "propose_close"},
    )
    return get_studio(group_id)


def close_studio(group_id: str, *, project_id: str, confirm: bool) -> GroupStudio:
    """confirm=false 进入 closing；confirm=true 在 DoD 齐后归档。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_id)
    if not confirm:
        if studio.status != "reviewing":
            raise GroupStudioError(409, ILLEGAL_TRANSITION)
        return propose_close(group_id, project_id=project_id)
    if studio.status != "closing":
        raise GroupStudioError(409, ILLEGAL_TRANSITION)
    if not _dod_complete(studio):
        raise GroupStudioError(409, DOD_INCOMPLETE)
    from src.group_studio.interrupt import cancel_active_run
    from src.group_studio.notify import KIND_COMPLETED, notify_studio

    cancel_active_run(group_id, source="pause")
    archive_group_studio(group_id)
    from src.group_studio.sessions import drop_group_tool_contexts

    drop_group_tool_contexts(group_id)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_DOD",
        payload={
            "taskCardId": studio.task_card.id,
            "dodChecklist": [item.model_dump(by_alias=True) for item in studio.task_card.dod_checklist],
        },
    )
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_STATUS",
        payload={"from": "closing", "to": "archived", "reason": "close_confirm"},
    )
    snapshot = get_studio(group_id)
    notify_studio(
        snapshot.model_dump(by_alias=True),
        kind=KIND_COMPLETED,
        body=studio.task_card.goal or studio.title,
    )
    return snapshot


def decide_studio_approval(
    group_id: str,
    *,
    project_id: str,
    approval_id: str,
    decision: str,
) -> GroupApproval:
    from src.group_studio.approvals import decide_approval

    return decide_approval(
        group_id,
        project_id=project_id,
        approval_id=approval_id,
        decision=decision,
    )


def post_human_message(group_id: str, *, project_id: str, body: str, mentions: list[str] | None = None) -> GroupMessage:
    """人类输入。paused 只入列；否则 inline interrupt 后派 Host。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_id)
    if studio.status == "circuit_broken":
        raise GroupStudioError(409, STUDIO_CIRCUIT_BROKEN)
    if studio.status in {"archived", "failed"}:
        raise GroupStudioError(409, ILLEGAL_TRANSITION)
    queued = studio.status == "paused"
    if not queued:
        from src.group_studio.interrupt import cancel_active_run

        cancel_active_run(group_id, source="human_message")
        set_group_studio_pending_speaker(group_id, None)
    text = body.strip()
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
    if not queued and studio.status in {"discussing", "executing", "reviewing", "awaiting_human"}:
        schedule_host_turn(group_id)
    return message


def interrupt_run(group_id: str, *, project_id: str) -> None:
    """只停不说话。无活动 Run 视为成功。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_id)
    from src.group_studio.interrupt import cancel_active_run

    cancel_active_run(group_id, source="stop_button")


def pause_studio(group_id: str, *, project_id: str) -> GroupStudio:
    """暂停工作室并取消当前 Run。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_id)
    if studio.status in {"paused", "archived", "failed"}:
        raise GroupStudioError(409, ILLEGAL_TRANSITION)
    from src.group_studio.interrupt import cancel_active_run

    cancel_active_run(group_id, source="pause")
    previous = studio.status
    snapshot = update_group_studio_state(
        group_id,
        status="paused",
        phase_before_pause=previous,
    )
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_STATUS",
        payload={"from": previous, "to": "paused", "reason": "pause"},
    )
    return _snapshot_to_model(snapshot)


def resume_studio(group_id: str, *, project_id: str, max_turns: int | None = None) -> GroupStudio:
    """从 paused / circuit_broken 恢复。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_id)
    if studio.status not in {"paused", "circuit_broken"}:
        raise GroupStudioError(409, ILLEGAL_TRANSITION)
    nxt = studio.phase_before_pause or "discussing"
    if nxt not in {"discussing", "executing", "reviewing", "awaiting_human"}:
        nxt = "discussing"
    budget = studio.budget.model_dump(by_alias=True)
    budget["noProgressTurns"] = 0
    if max_turns is not None:
        budget["maxTurns"] = int(max_turns)
    snapshot = update_group_studio_state(
        group_id,
        status=nxt,
        clear_phase_before_pause=True,
        budget=budget,
    )
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_STATUS",
        payload={"from": studio.status, "to": nxt, "reason": "resume"},
    )
    updated = _snapshot_to_model(snapshot)
    if nxt in {"discussing", "executing", "reviewing", "awaiting_human"}:
        schedule_host_turn(group_id)
    return updated


def confirm_restatement(group_id: str, *, project_id: str) -> GroupStudio:
    """人类确认 Host 复述后恢复阶段。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_id)
    if not studio.awaiting_restatement:
        raise GroupStudioError(409, NOT_AWAITING_RESTATEMENT)
    messages = list_group_studio_messages(group_id, limit=50)
    restated = any(
        item.get("role") == "host" and item.get("intent") == "restatement" and str(item.get("body") or "").strip()
        for item in messages
    )
    if not restated:
        raise GroupStudioError(409, RESTATEMENT_MISSING)
    nxt = studio.phase_before_pause or "discussing"
    if nxt not in {"discussing", "executing", "reviewing"}:
        nxt = "discussing"
    snapshot = update_group_studio_state(
        group_id,
        status=nxt,
        awaiting_restatement=False,
        clear_phase_before_pause=True,
    )
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_STATUS",
        payload={"from": studio.status, "to": nxt, "reason": "confirm_restatement"},
    )
    updated = _snapshot_to_model(snapshot)
    schedule_host_turn(group_id)
    return updated


def _assert_project(studio: GroupStudio, project_id: str) -> None:
    if project_id and project_id != studio.project_id:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION)


def _blackboard_inner(payload: dict) -> dict:
    return {
        "taskCardId": payload.get("taskCardId") or "",
        "goal": payload.get("goal") or "",
        "constraints": list(payload.get("constraints") or []),
        "decisions": list(payload.get("decisions") or []),
        "openQuestions": list(payload.get("openQuestions") or []),
        "artifacts": list(payload.get("artifacts") or []),
    }


def patch_blackboard(
    group_id: str,
    patch: dict,
    *,
    asked_by: str = "host",
    source_message_id: str | None = None,
    source_run_id: str | None = None,
) -> dict:
    """合并黑板补丁。丢弃未决问题会抛 `open_questions_dropped`。"""

    studio = _load_studio(group_id)
    assert_can_mutate(studio.status)
    current = studio.blackboard.model_dump(by_alias=True)
    merged = merge_blackboard(
        current,
        patch,
        asked_by=asked_by,
        source_message_id=source_message_id,
        source_run_id=source_run_id,
    )
    inner = _blackboard_inner(merged)
    if inner == _blackboard_inner(current):
        return current
    board = replace_group_studio_blackboard(group_id, inner)
    if not board:
        raise GroupStudioError(404, GROUP_STUDIO_NOT_FOUND)
    emit_envelope(
        group_id=group_id,
        event_type="GROUP_BLACKBOARD",
        run_id=source_run_id,
        payload=board,
    )
    return board


_DELIVERY_SUFFIXES = {
    ".md",
    ".txt",
    ".pdf",
    ".docx",
    ".xlsx",
    ".csv",
    ".json",
    ".html",
    ".png",
    ".jpg",
}
_SKIP_DIR_NAMES = {".git", "__pycache__", "node_modules", ".venv", ".DS_Store"}


def record_worker_delivery(
    group_id: str,
    *,
    member: dict,
    body: str,
    run_id: str,
    message_id: str,
) -> None:
    """执行/验收期工人交稿后写入黑板，并登记 workspace/outputs 新文件。"""

    text = str(body or "").strip()
    if not text:
        return
    snapshot = get_group_studio_snapshot(group_id, message_limit=1)
    if snapshot is None:
        return
    status = str(snapshot.get("status") or "")
    if status not in {"executing", "reviewing"}:
        return
    board = snapshot.get("blackboard") or {}
    already = any(
        str(item.get("sourceRunId") or "") == run_id
        for item in (board.get("decisions") or [])
        if isinstance(item, dict)
    )
    name = str(member.get("displayName") or member.get("agentId") or "工人")
    snippet = " ".join(text.split())
    if len(snippet) > 240:
        snippet = snippet[:239] + "…"
    artifact_ids = _register_untracked_workspace_files(
        group_id,
        member_id=str(member.get("id") or ""),
        project_id=str(snapshot.get("projectId") or ""),
    )
    patch: dict = {}
    if not already:
        patch["addDecisions"] = [{
            "text": f"「{name}」已交稿：{snippet}",
            "sourceMessageId": message_id,
            "sourceRunId": run_id,
        }]
    if artifact_ids:
        patch["addArtifactIds"] = artifact_ids
    if not patch:
        return
    try:
        patch_blackboard(
            group_id,
            patch,
            asked_by=str(member.get("id") or "worker"),
            source_message_id=message_id,
            source_run_id=run_id,
        )
    except GroupStudioError:
        return


def _register_untracked_workspace_files(
    group_id: str,
    *,
    member_id: str,
    project_id: str,
) -> list[str]:
    """把尚未入库的 outputs/workspace 交付文件登记为产物。"""

    from src.group_studio.files import ensure_studio_dirs

    project = get_project(project_id) or {}
    project_root = Path(str(project.get("rootPath") or "")).expanduser().resolve()
    if not project_root.is_dir():
        return []
    studio_root = ensure_studio_dirs(project_root, group_id)
    existing = {str(item.get("path") or "") for item in list_group_studio_artifacts(group_id)}
    registered: list[str] = []
    for folder in ("outputs", "workspace"):
        base = studio_root / folder
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if any(part in _SKIP_DIR_NAMES or part.startswith(".") for part in path.relative_to(studio_root).parts):
                continue
            if folder == "workspace" and path.suffix.lower() not in _DELIVERY_SUFFIXES:
                continue
            try:
                rel = str(path.relative_to(project_root))
            except ValueError:
                continue
            if rel in existing:
                continue
            row = insert_group_studio_artifact({
                "id": _new_id("gart"),
                "groupId": group_id,
                "title": path.name,
                "path": rel,
                "mediaType": "",
                "byteSize": path.stat().st_size,
                "createdBy": member_id,
            })
            existing.add(rel)
            artifact_id = str(row.get("id") or "")
            if not artifact_id:
                continue
            registered.append(artifact_id)
            emit_envelope(
                group_id=group_id,
                event_type="GROUP_FILE",
                payload=row,
            )
    return registered


def mute_member(
    group_id: str,
    member_id: str,
    muted: bool,
    *,
    project_id: str = "",
    run_id: str | None = None,
) -> Membership:
    """静音或唤醒工人。主持人不可静音。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_id)
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
    return membership


def remove_member(
    group_id: str,
    member_id: str,
    *,
    project_id: str = "",
    run_id: str | None = None,
) -> Membership:
    """移除工人。主持人不可移除。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_id)
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
    return membership


def list_studio_files(group_id: str) -> list[dict]:
    _load_studio(group_id)
    return list_group_studio_artifacts(group_id)


async def upload_studio_file(
    group_id: str,
    upload,
    *,
    project_id: str = "",
    created_by: str = "human",
) -> GroupArtifact:
    """上传到工作室 inputs/，并写入黑板 artifact 列表。"""

    studio = _load_studio(group_id)
    _assert_project(studio, project_id)
    assert_can_mutate(studio.status)
    project = get_project(studio.project_id)
    if project is None:
        raise GroupStudioError(422, CROSS_PROJECT_SESSION)
    project_root = Path(str(project.get("rootPath") or "")).expanduser().resolve()
    stored = await save_studio_upload(
        project_root=project_root,
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
    patch_blackboard(group_id, {"addArtifactIds": [artifact.id]})
    return artifact

