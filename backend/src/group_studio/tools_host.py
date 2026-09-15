"""Host 控场工具，含黑板写入。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk

from src.group_studio.errors import HOST_REQUIRED, INTAKE_NOT_CONFIRMED, GroupStudioError
from src.group_studio.events import emit_envelope
from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID
from src.storage.database import (
    get_group_studio_member,
    get_group_studio_snapshot,
    set_group_studio_status,
)

HOST_STOP_TOOLS = {
    "group_assign_floor",
    "group_request_intake_confirm",
    "group_publish_work_items",
    "group_start_execution",
    "group_start_review",
    "group_propose_close",
}


@dataclass
class HostToolContext:
    """按群复用的 Host 工具上下文。

    Agent 工厂会缓存 Host 实例，工具闭包必须指向同一对象；
    每轮只改 run_id / assigned_member_id，不能新建 ctx。
    """

    group_id: str
    run_id: str
    host_member_id: str
    last_stop_tool: str = ""
    assigned_member_id: str = ""
    assigned_member_ids: list[str] = field(default_factory=list)
    published_work_item_ids: list[str] = field(default_factory=list)

    def begin_run(self, run_id: str) -> None:
        """绑定本轮 Run，清空上一轮指派。"""

        self.run_id = run_id
        self.last_stop_tool = ""
        self.assigned_member_id = ""
        self.assigned_member_ids = []
        self.published_work_item_ids = []


_HOST_CONTEXTS: dict[str, HostToolContext] = {}


def acquire_host_tool_context(group_id: str, host_member_id: str) -> HostToolContext:
    """返回该工作室的 Host 工具上下文；缓存 Agent 与调度器共用。"""

    existing = _HOST_CONTEXTS.get(group_id)
    if existing is None or existing.host_member_id != host_member_id:
        existing = HostToolContext(
            group_id=group_id,
            run_id="",
            host_member_id=host_member_id,
        )
        _HOST_CONTEXTS[group_id] = existing
    return existing


def drop_host_tool_context(group_id: str) -> None:
    """归档时丢掉进程内 Host 上下文。"""

    _HOST_CONTEXTS.pop(group_id, None)


class HostTool(ToolBase):
    """Host 控场工具基类，默认允许执行。"""

    is_mcp = False
    is_read_only = False
    is_concurrency_safe = False
    is_external_tool = False
    is_state_injected = False

    def __init__(self, ctx: HostToolContext) -> None:
        self.ctx = ctx

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Group Studio host control tool.",
            decision_reason="Host has no destructive default tools",
        )

    def success(self, payload: dict[str, Any]) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=json.dumps(payload, ensure_ascii=False, indent=2))],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )

    def error(self, message: str) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=json.dumps({"error": message}, ensure_ascii=False))],
            state=ToolResultState.ERROR,
            is_last=True,
        )


class GroupReadTaskCard(HostTool):
    name = "group_read_task_card"
    description = "Read the current task card: goal, constraints, and definition of done."
    input_schema = {"type": "object", "properties": {}, "required": []}
    is_read_only = True

    async def __call__(self) -> ToolChunk:
        snapshot = get_group_studio_snapshot(self.ctx.group_id) or {}
        return self.success({"taskCard": snapshot.get("taskCard")})


class GroupReadBlackboard(HostTool):
    name = "group_read_blackboard"
    description = "Read the consensus blackboard. Prefer this over long chat history."
    input_schema = {"type": "object", "properties": {}, "required": []}
    is_read_only = True

    async def __call__(self) -> ToolChunk:
        snapshot = get_group_studio_snapshot(self.ctx.group_id) or {}
        return self.success({"blackboard": snapshot.get("blackboard")})


class GroupPatchBlackboard(HostTool):
    name = "group_patch_blackboard"
    description = (
        "Update the consensus blackboard. Never drop unresolved openQuestions; "
        "use resolveQuestions or keep every unresolved id in a full replacement."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "addDecisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
            "addQuestions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
            "resolveQuestions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "resolution": {"type": "string"},
                    },
                    "required": ["id"],
                },
            },
            "openQuestions": {
                "type": "array",
                "items": {"type": "object"},
            },
            "addArtifactIds": {
                "type": "array",
                "items": {"type": "string"},
            },
            "goal": {"type": "string"},
            "constraints": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": [],
    }

    async def __call__(
        self,
        addDecisions: list | None = None,
        addQuestions: list | None = None,
        resolveQuestions: list | None = None,
        openQuestions: list | None = None,
        addArtifactIds: list | None = None,
        goal: str | None = None,
        constraints: list | None = None,
    ) -> ToolChunk:
        from src.group_studio.service import patch_blackboard

        patch: dict[str, Any] = {}
        if addDecisions:
            patch["addDecisions"] = addDecisions
        if addQuestions:
            patch["addQuestions"] = addQuestions
        if resolveQuestions:
            patch["resolveQuestions"] = resolveQuestions
        if openQuestions is not None:
            patch["openQuestions"] = openQuestions
        if addArtifactIds:
            patch["addArtifactIds"] = addArtifactIds
        if goal is not None:
            patch["goal"] = goal
        if constraints is not None:
            patch["constraints"] = constraints
        try:
            board = patch_blackboard(
                self.ctx.group_id,
                patch,
                asked_by="host",
                source_run_id=self.ctx.run_id or None,
            )
        except GroupStudioError as exc:
            return self.error(exc.code)
        return self.success({"blackboard": board})


def _assert_intake_confirmed(group_id: str) -> str:
    """drafting 下禁止邀请/点名/发布工作项。"""

    snapshot = get_group_studio_snapshot(group_id) or {}
    if str(snapshot.get("status") or "") == "drafting":
        return INTAKE_NOT_CONFIRMED
    return ""


class GroupUpdateTaskCard(HostTool):
    name = "group_update_task_card"
    description = (
        "Write the current task card: goal, background, constraints, and definition of done. "
        "Does not start group discussion."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "goal": {"type": "string"},
            "background": {"type": "string"},
            "constraints": {"type": "array", "items": {"type": "string"}},
            "dodChecklist": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "text": {"type": "string"},
                        "checked": {"type": "boolean"},
                    },
                    "required": ["text"],
                },
            },
        },
        "required": [],
    }

    async def __call__(
        self,
        goal: str | None = None,
        background: str | None = None,
        constraints: list | None = None,
        dodChecklist: list | None = None,
    ) -> ToolChunk:
        from src.group_studio.models import DodItem, GroupTaskCardUpdateRequest
        from src.group_studio.service import update_task_card

        snapshot = get_group_studio_snapshot(self.ctx.group_id) or {}
        try:
            card = update_task_card(
                self.ctx.group_id,
                GroupTaskCardUpdateRequest(
                    project_id=str(snapshot.get("projectId") or ""),
                    goal=goal,
                    background=background,
                    constraints=constraints,
                    dod_checklist=(
                        [
                            DodItem.model_validate({
                                **(item if isinstance(item, dict) else {"text": str(item)}),
                                "id": (item.get("id") if isinstance(item, dict) else "") or f"gdod_{index}",
                            })
                            for index, item in enumerate(dodChecklist)
                        ]
                        if dodChecklist is not None
                        else None
                    ),
                ),
            )
        except GroupStudioError as exc:
            return self.error(exc.code)
        return self.success({"taskCard": card.model_dump(by_alias=True)})


class GroupRequestIntakeConfirm(HostTool):
    name = "group_request_intake_confirm"
    description = (
        "Ask the human to confirm that the task card is ready so the group can start. "
        "Does not enter discussing by itself. Requires a nonempty goal. Empty DoD is allowed."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}

    async def __call__(self) -> ToolChunk:
        from src.group_studio.service import request_intake_confirm

        try:
            studio = request_intake_confirm(self.ctx.group_id)
        except GroupStudioError as exc:
            return self.error(exc.code)
        self.ctx.last_stop_tool = self.name
        return self.success({
            "awaitingIntakeConfirm": studio.awaiting_intake_confirm,
            "status": studio.status,
        })


def _resolve_assignee(group_id: str, member_id: str) -> tuple[dict[str, Any] | None, str]:
    """校验可被指派的工人；失败返回错误码。"""

    snapshot = get_group_studio_snapshot(group_id) or {}
    if str(snapshot.get("status") or "") == "awaiting_human":
        return None, "awaiting_restatement"
    member = get_group_studio_member(group_id, member_id)
    if member is None or member.get("status") == "removed":
        return None, "member_not_found"
    if member.get("role") == "host":
        return None, "cannot_assign_host"
    if member.get("muted"):
        return None, "member_muted"
    return member, ""


def _commit_floor_assignment(ctx: HostToolContext, member: dict[str, Any]) -> None:
    """单人兼容入口。"""

    _commit_floor_queue(ctx, [member])


def _commit_floor_queue(ctx: HostToolContext, members: list[dict[str, Any]]) -> None:
    """写入 FIFO 点名队列。GROUP_FLOOR 仍给队首（兼容缓存 Host 测试）。"""

    from src.group_studio.speaker_queue import set_queue

    ids = [str(item["id"]) for item in members]
    ctx.assigned_member_ids = list(ids)
    ctx.assigned_member_id = ids[0] if ids else ""
    remaining = set_queue(ctx.group_id, ids, kind="assign", run_id=ctx.run_id or None)
    if remaining:
        emit_envelope(
            group_id=ctx.group_id,
            event_type="GROUP_FLOOR",
            run_id=ctx.run_id,
            payload={"memberId": remaining[0], "runId": ctx.run_id, "reason": "host_assign"},
        )


class GroupAssignFloor(HostTool):
    name = "group_assign_floor"
    description = (
        "Give the next speaking/working turns to one or more workers. "
        "memberIds is a FIFO queue; memberId is the single-worker form. Ends this host turn."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "memberId": {"type": "string"},
            "memberIds": {"type": "array", "items": {"type": "string"}},
        },
    }

    async def __call__(self, memberId: str = "", memberIds: list | None = None) -> ToolChunk:
        blocked = _assert_intake_confirmed(self.ctx.group_id)
        if blocked:
            return self.error(blocked)
        raw_ids = [str(item) for item in (memberIds or []) if str(item).strip()]
        if not raw_ids and memberId.strip():
            raw_ids = [memberId.strip()]
        if not raw_ids:
            return self.error("member_not_found")
        unique: list[str] = []
        for item in raw_ids:
            if item not in unique:
                unique.append(item)
        resolved: list[dict[str, Any]] = []
        for mid in unique:
            member, error = _resolve_assignee(self.ctx.group_id, mid)
            if error or member is None:
                return self.error(error or "member_not_found")
            resolved.append(member)
        self.ctx.last_stop_tool = self.name
        _commit_floor_queue(self.ctx, resolved)
        return self.success({"assigned": resolved, "memberIds": [item["id"] for item in resolved]})


class GroupPublishWorkItems(HostTool):
    name = "group_publish_work_items"
    description = (
        "Publish work items onto the studio workboard. "
        "Optional assigneeMemberId claims that item and enqueues the worker. "
        "Ends this host turn."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "assigneeMemberId": {"type": "string"},
                    },
                    "required": ["title"],
                },
            },
        },
        "required": ["items"],
    }

    async def __call__(self, items: list | None = None) -> ToolChunk:
        blocked = _assert_intake_confirmed(self.ctx.group_id)
        if blocked:
            return self.error(blocked)
        from src.group_studio.workboard import publish_work_items

        payload = list(items or [])
        try:
            created, enqueue = publish_work_items(
                self.ctx.group_id,
                payload,
                run_id=self.ctx.run_id,
                assigned_member_ids=list(self.ctx.assigned_member_ids),
            )
        except GroupStudioError as exc:
            return self.error(exc.code)
        self.ctx.published_work_item_ids = [str(item.get("id") or "") for item in created]
        self.ctx.assigned_member_ids = enqueue
        self.ctx.assigned_member_id = enqueue[0] if enqueue else self.ctx.assigned_member_id
        self.ctx.last_stop_tool = self.name
        return self.success({"items": created, "queued": enqueue})


class GroupCancelWorkItem(HostTool):
    name = "group_cancel_work_item"
    description = "Cancel an open or claimed work item."
    input_schema = {
        "type": "object",
        "properties": {"workItemId": {"type": "string"}},
        "required": ["workItemId"],
    }

    async def __call__(self, workItemId: str) -> ToolChunk:
        blocked = _assert_intake_confirmed(self.ctx.group_id)
        if blocked:
            return self.error(blocked)
        from src.group_studio.workboard import cancel_item

        try:
            row = cancel_item(self.ctx.group_id, workItemId, run_id=self.ctx.run_id)
        except GroupStudioError as exc:
            return self.error(exc.code)
        return self.success({"item": row})


class GroupReviewWorkItem(HostTool):
    name = "group_review_work_item"
    description = (
        "Review a completed (done) work item during the review phase. "
        "verdict=accept marks it accepted; verdict=rework reopens it for the same worker with your note "
        "(what is missing or wrong). Use before group_propose_close; every done item should be accepted or reworked."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "workItemId": {"type": "string"},
            "verdict": {"type": "string", "enum": ["accept", "rework"]},
            "note": {"type": "string", "description": "Chinese review note; required for rework."},
        },
        "required": ["workItemId", "verdict"],
    }

    async def __call__(self, workItemId: str, verdict: str, note: str = "") -> ToolChunk:
        snapshot = get_group_studio_snapshot(self.ctx.group_id) or {}
        if str(snapshot.get("status") or "") not in {"reviewing", "executing"}:
            return self.error("illegal_transition")
        if verdict == "rework" and not str(note or "").strip():
            return self.error("review_note_required")
        from src.group_studio.workboard import review_item

        try:
            row = review_item(self.ctx.group_id, workItemId, verdict=verdict, note=note, run_id=self.ctx.run_id)
        except GroupStudioError as exc:
            return self.error(exc.code)
        if verdict == "rework":
            # 退回的工作项直接点名原认领人继续，不用再走认领轮。
            assignee = str(row.get("assigneeMemberId") or "")
            if assignee:
                assigned, error = _resolve_assignee(self.ctx.group_id, assignee)
                if assigned is not None and not error:
                    _commit_floor_assignment(self.ctx, assigned)
        return self.success({"item": row})


class GroupListAvailableAgents(HostTool):
    name = "group_list_available_agents"
    description = (
        "List project Agents that can still be invited into this studio. "
        "Use this before group_invite_member when the current members cannot cover the goal."
    )
    input_schema = {"type": "object", "properties": {}, "required": []}
    is_read_only = True

    async def __call__(self) -> ToolChunk:
        from src.group_studio.orchestrator import max_members
        from src.group_studio.service import list_invitable_agents
        from src.storage.database import count_group_studio_active_members

        agents = list_invitable_agents(self.ctx.group_id)
        remaining = max(0, max_members() - count_group_studio_active_members(self.ctx.group_id))
        return self.success({
            "agents": agents,
            "remainingSlots": remaining,
        })


class GroupInviteMember(HostTool):
    name = "group_invite_member"
    description = "Invite an existing project Agent as a worker. Do not invite the host sentinel. agentId comes from group_list_available_agents."
    input_schema = {
        "type": "object",
        "properties": {
            "agentId": {"type": "string"},
            "displayName": {"type": "string"},
        },
        "required": ["agentId"],
    }

    async def __call__(self, agentId: str, displayName: str = "") -> ToolChunk:
        blocked = _assert_intake_confirmed(self.ctx.group_id)
        if blocked:
            return self.error(blocked)
        if agentId == GROUP_STUDIO_HOST_AGENT_ID:
            return self.error(HOST_REQUIRED)
        from src.group_studio.models import GroupMemberCreateRequest
        from src.group_studio.service import add_member

        snapshot = get_group_studio_snapshot(self.ctx.group_id) or {}
        try:
            membership = add_member(
                self.ctx.group_id,
                GroupMemberCreateRequest(
                    project_id=str(snapshot.get("projectId") or ""),
                    agent_id=agentId,
                ),
            )
        except GroupStudioError as exc:
            return self.error(exc.code)
        payload = membership.model_dump(by_alias=True)
        return self.success({"member": payload})


class GroupMuteMember(HostTool):
    name = "group_mute_member"
    description = "Mute or unmute a worker. The host cannot be muted."
    input_schema = {
        "type": "object",
        "properties": {
            "memberId": {"type": "string"},
            "muted": {"type": "boolean"},
        },
        "required": ["memberId", "muted"],
    }

    async def __call__(self, memberId: str, muted: bool) -> ToolChunk:
        from src.group_studio.service import mute_member

        try:
            membership = mute_member(
                self.ctx.group_id,
                memberId,
                muted,
                run_id=self.ctx.run_id or None,
            )
        except GroupStudioError as exc:
            return self.error(exc.code)
        return self.success({"member": membership.model_dump(by_alias=True)})


class GroupRemoveMember(HostTool):
    name = "group_remove_member"
    description = "Remove a worker from the studio. The host cannot be removed."
    input_schema = {
        "type": "object",
        "properties": {"memberId": {"type": "string"}},
        "required": ["memberId"],
    }

    async def __call__(self, memberId: str) -> ToolChunk:
        from src.group_studio.service import remove_member

        try:
            membership = remove_member(
                self.ctx.group_id,
                memberId,
                run_id=self.ctx.run_id or None,
            )
        except GroupStudioError as exc:
            return self.error(exc.code)
        return self.success({"member": membership.model_dump(by_alias=True)})


class GroupStartExecution(HostTool):
    name = "group_start_execution"
    description = (
        "Move the studio from discussion into execution. Ends this host turn. "
        "Pass memberId to also assign who writes the deliverable in this same turn."
    )
    input_schema = {
        "type": "object",
        "properties": {"memberId": {"type": "string"}},
        "required": [],
    }

    async def __call__(self, memberId: str = "") -> ToolChunk:
        snapshot = get_group_studio_snapshot(self.ctx.group_id) or {}
        if str(snapshot.get("status") or "") != "discussing":
            return self.error("illegal_transition")
        goal = str((snapshot.get("taskCard") or {}).get("goal") or "").strip()
        if not goal:
            return self.error("goal_required")
        assigned = None
        if memberId:
            assigned, error = _resolve_assignee(self.ctx.group_id, memberId)
            if error or assigned is None:
                return self.error(error or "member_not_found")
        set_group_studio_status(self.ctx.group_id, "executing")
        self.ctx.last_stop_tool = self.name
        emit_envelope(
            group_id=self.ctx.group_id,
            event_type="GROUP_STATUS",
            run_id=self.ctx.run_id,
            payload={"from": "discussing", "to": "executing", "reason": "host_start_execution"},
        )
        if assigned is not None:
            _commit_floor_assignment(self.ctx, assigned)
        return self.success({"status": "executing", "assigned": assigned})


class GroupStartReview(HostTool):
    name = "group_start_review"
    description = (
        "Move the studio from execution into review. Ends this host turn. "
        "Pass memberId to assign who checks the deliverable."
    )
    input_schema = {
        "type": "object",
        "properties": {"memberId": {"type": "string"}},
        "required": [],
    }

    async def __call__(self, memberId: str = "") -> ToolChunk:
        snapshot = get_group_studio_snapshot(self.ctx.group_id) or {}
        if str(snapshot.get("status") or "") != "executing":
            return self.error("illegal_transition")
        assigned = None
        if memberId:
            assigned, error = _resolve_assignee(self.ctx.group_id, memberId)
            if error or assigned is None:
                return self.error(error or "member_not_found")
        set_group_studio_status(self.ctx.group_id, "reviewing")
        self.ctx.last_stop_tool = self.name
        emit_envelope(
            group_id=self.ctx.group_id,
            event_type="GROUP_STATUS",
            run_id=self.ctx.run_id,
            payload={"from": "executing", "to": "reviewing", "reason": "host_start_review"},
        )
        if assigned is not None:
            _commit_floor_assignment(self.ctx, assigned)
        return self.success({"status": "reviewing", "assigned": assigned})


class GroupProposeClose(HostTool):
    name = "group_propose_close"
    description = "Propose closing the studio after review. Ends this host turn. Human still confirms DoD."
    input_schema = {"type": "object", "properties": {}, "required": []}

    async def __call__(self) -> ToolChunk:
        from src.group_studio.service import propose_close

        try:
            studio = propose_close(self.ctx.group_id)
        except GroupStudioError as exc:
            return self.error(exc.code)
        self.ctx.last_stop_tool = self.name
        return self.success({"status": studio.status})


def host_tools(ctx: HostToolContext) -> list[HostTool]:
    """Host 控场工具集，含黑板写入。"""

    return [
        GroupReadTaskCard(ctx),
        GroupReadBlackboard(ctx),
        GroupPatchBlackboard(ctx),
        GroupUpdateTaskCard(ctx),
        GroupRequestIntakeConfirm(ctx),
        GroupPublishWorkItems(ctx),
        GroupCancelWorkItem(ctx),
        GroupReviewWorkItem(ctx),
        GroupAssignFloor(ctx),
        GroupListAvailableAgents(ctx),
        GroupInviteMember(ctx),
        GroupMuteMember(ctx),
        GroupRemoveMember(ctx),
        GroupStartExecution(ctx),
        GroupStartReview(ctx),
        GroupProposeClose(ctx),
    ]
