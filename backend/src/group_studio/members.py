"""拉人、静音、移出。不改发言队列的算法。"""
from __future__ import annotations

from src.group_studio.access import _assert_project, _load_studio, _new_id
from src.group_studio.errors import (
    AGENT_NOT_BOUND,
    ALREADY_MEMBER,
    CROSS_PROJECT_SESSION,
    DEFAULT_AGENT_FORBIDDEN,
    GROUP_STUDIO_NOT_FOUND,
    HOST_REQUIRED,
    JOB_AGENT_FORBIDDEN,
    MEMBER_CAP,
    GroupStudioError,
)
from src.group_studio.events import emit_envelope
from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID, GroupMemberCreateRequest, Membership
from src.group_studio.orchestrator import assert_can_mutate, max_members
from src.storage.records.group_studio_records import (
    count_group_studio_active_members,
    find_group_studio_member_by_agent,
    get_group_studio_member,
    insert_group_studio_member,
    set_group_studio_member_muted,
    set_group_studio_member_removed,
    set_group_studio_member_status,
)


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
    """列出还能拉进本群的普通 Agent。"""

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
            from src.group_studio.scheduler import schedule_next

            cancel_active_run(group_id, source="stop_button")
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
        from src.group_studio.scheduler import schedule_next

        cancel_active_run(group_id, source="stop_button")
        schedule_next(group_id)
    return membership
