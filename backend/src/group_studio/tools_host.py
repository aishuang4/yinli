"""Host 控场工具。PR 5 不含 group_patch_blackboard。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk

from src.group_studio.errors import HOST_REQUIRED, GroupStudioError
from src.group_studio.events import emit_envelope
from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID
from src.storage.database import (
    get_group_studio_member,
    get_group_studio_snapshot,
    set_group_studio_member_muted,
    set_group_studio_member_removed,
    set_group_studio_status,
)

HOST_STOP_TOOLS = {
    "group_assign_floor",
    "group_start_execution",
    "group_start_review",
    "group_propose_close",
}


@dataclass
class HostToolContext:
    """一次 Host Run 内的可变上下文。"""

    group_id: str
    run_id: str
    host_member_id: str
    last_stop_tool: str = ""
    assigned_member_id: str = ""


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


class GroupAssignFloor(HostTool):
    name = "group_assign_floor"
    description = "Give the next speaking/working turn to one worker member. Ends this host turn."
    input_schema = {
        "type": "object",
        "properties": {"memberId": {"type": "string"}},
        "required": ["memberId"],
    }

    async def __call__(self, memberId: str) -> ToolChunk:
        member = get_group_studio_member(self.ctx.group_id, memberId)
        if member is None or member.get("status") == "removed":
            return self.error("member_not_found")
        if member.get("role") == "host":
            return self.error("cannot_assign_host")
        if member.get("muted"):
            return self.error("member_muted")
        self.ctx.last_stop_tool = self.name
        self.ctx.assigned_member_id = memberId
        emit_envelope(
            group_id=self.ctx.group_id,
            event_type="GROUP_FLOOR",
            run_id=self.ctx.run_id,
            payload={"memberId": memberId, "runId": self.ctx.run_id, "reason": "host_assign"},
        )
        return self.success({"assigned": member})


class GroupInviteMember(HostTool):
    name = "group_invite_member"
    description = "Invite an existing project Agent as a worker. Do not invite the host sentinel."
    input_schema = {
        "type": "object",
        "properties": {
            "agentId": {"type": "string"},
            "displayName": {"type": "string"},
        },
        "required": ["agentId"],
    }

    async def __call__(self, agentId: str, displayName: str = "") -> ToolChunk:
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
        member = get_group_studio_member(self.ctx.group_id, memberId)
        if member is None:
            return self.error("member_not_found")
        if member.get("role") == "host":
            return self.error(HOST_REQUIRED)
        updated = set_group_studio_member_muted(self.ctx.group_id, memberId, muted)
        emit_envelope(
            group_id=self.ctx.group_id,
            event_type="GROUP_MEMBER",
            run_id=self.ctx.run_id,
            payload={**(updated or member), "change": "muted" if muted else "unmuted"},
        )
        return self.success({"member": updated})


class GroupRemoveMember(HostTool):
    name = "group_remove_member"
    description = "Remove a worker from the studio. The host cannot be removed."
    input_schema = {
        "type": "object",
        "properties": {"memberId": {"type": "string"}},
        "required": ["memberId"],
    }

    async def __call__(self, memberId: str) -> ToolChunk:
        member = get_group_studio_member(self.ctx.group_id, memberId)
        if member is None:
            return self.error("member_not_found")
        if member.get("role") == "host":
            return self.error(HOST_REQUIRED)
        updated = set_group_studio_member_removed(self.ctx.group_id, memberId)
        emit_envelope(
            group_id=self.ctx.group_id,
            event_type="GROUP_MEMBER",
            run_id=self.ctx.run_id,
            payload={**(updated or member), "change": "removed"},
        )
        return self.success({"member": updated})


class GroupStartExecution(HostTool):
    name = "group_start_execution"
    description = "Move the studio from discussion into execution. Ends this host turn."
    input_schema = {"type": "object", "properties": {}, "required": []}

    async def __call__(self) -> ToolChunk:
        snapshot = get_group_studio_snapshot(self.ctx.group_id) or {}
        if str(snapshot.get("status") or "") != "discussing":
            return self.error("illegal_transition")
        goal = str((snapshot.get("taskCard") or {}).get("goal") or "").strip()
        if not goal:
            return self.error("goal_required")
        set_group_studio_status(self.ctx.group_id, "executing")
        self.ctx.last_stop_tool = self.name
        emit_envelope(
            group_id=self.ctx.group_id,
            event_type="GROUP_STATUS",
            run_id=self.ctx.run_id,
            payload={"from": "discussing", "to": "executing", "reason": "host_start_execution"},
        )
        return self.success({"status": "executing"})


class GroupStartReview(HostTool):
    name = "group_start_review"
    description = "Move the studio from execution into review. Ends this host turn."
    input_schema = {"type": "object", "properties": {}, "required": []}

    async def __call__(self) -> ToolChunk:
        snapshot = get_group_studio_snapshot(self.ctx.group_id) or {}
        if str(snapshot.get("status") or "") != "executing":
            return self.error("illegal_transition")
        set_group_studio_status(self.ctx.group_id, "reviewing")
        self.ctx.last_stop_tool = self.name
        emit_envelope(
            group_id=self.ctx.group_id,
            event_type="GROUP_STATUS",
            run_id=self.ctx.run_id,
            payload={"from": "executing", "to": "reviewing", "reason": "host_start_review"},
        )
        return self.success({"status": "reviewing"})


class GroupProposeClose(HostTool):
    name = "group_propose_close"
    description = "Propose closing the studio after review. Ends this host turn. Human still confirms DoD."
    input_schema = {"type": "object", "properties": {}, "required": []}

    async def __call__(self) -> ToolChunk:
        snapshot = get_group_studio_snapshot(self.ctx.group_id) or {}
        if str(snapshot.get("status") or "") not in {"reviewing", "discussing", "executing"}:
            return self.error("illegal_transition")
        previous = str(snapshot.get("status") or "")
        set_group_studio_status(self.ctx.group_id, "closing")
        self.ctx.last_stop_tool = self.name
        emit_envelope(
            group_id=self.ctx.group_id,
            event_type="GROUP_STATUS",
            run_id=self.ctx.run_id,
            payload={"from": previous, "to": "closing", "reason": "host_propose_close"},
        )
        return self.success({"status": "closing"})


def host_tools(ctx: HostToolContext) -> list[HostTool]:
    """PR 5 Host 工具集，不含黑板写入。"""

    return [
        GroupReadTaskCard(ctx),
        GroupReadBlackboard(ctx),
        GroupAssignFloor(ctx),
        GroupInviteMember(ctx),
        GroupMuteMember(ctx),
        GroupRemoveMember(ctx),
        GroupStartExecution(ctx),
        GroupStartReview(ctx),
        GroupProposeClose(ctx),
    ]
