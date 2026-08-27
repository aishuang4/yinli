"""Host 控场工具，含黑板写入。"""

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
    set_group_studio_pending_speaker,
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
    """按群复用的 Host 工具上下文。

    Agent 工厂会缓存 Host 实例，工具闭包必须指向同一对象；
    每轮只改 run_id / assigned_member_id，不能新建 ctx。
    """

    group_id: str
    run_id: str
    host_member_id: str
    last_stop_tool: str = ""
    assigned_member_id: str = ""

    def begin_run(self, run_id: str) -> None:
        """绑定本轮 Run，清空上一轮指派。"""

        self.run_id = run_id
        self.last_stop_tool = ""
        self.assigned_member_id = ""


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
    """写入本轮指派并落库 pending speaker。"""

    member_id = str(member["id"])
    ctx.assigned_member_id = member_id
    set_group_studio_pending_speaker(ctx.group_id, member_id)
    emit_envelope(
        group_id=ctx.group_id,
        event_type="GROUP_FLOOR",
        run_id=ctx.run_id,
        payload={"memberId": member_id, "runId": ctx.run_id, "reason": "host_assign"},
    )


class GroupAssignFloor(HostTool):
    name = "group_assign_floor"
    description = "Give the next speaking/working turn to one worker member. Ends this host turn."
    input_schema = {
        "type": "object",
        "properties": {"memberId": {"type": "string"}},
        "required": ["memberId"],
    }

    async def __call__(self, memberId: str) -> ToolChunk:
        member, error = _resolve_assignee(self.ctx.group_id, memberId)
        if error or member is None:
            return self.error(error or "member_not_found")
        self.ctx.last_stop_tool = self.name
        _commit_floor_assignment(self.ctx, member)
        return self.success({"assigned": member})


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
        GroupAssignFloor(ctx),
        GroupListAvailableAgents(ctx),
        GroupInviteMember(ctx),
        GroupMuteMember(ctx),
        GroupRemoveMember(ctx),
        GroupStartExecution(ctx),
        GroupStartReview(ctx),
        GroupProposeClose(ctx),
    ]
