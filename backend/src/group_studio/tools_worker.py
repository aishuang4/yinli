"""Worker 群工具。`group_post_message` 结束本轮。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk

from src.group_studio.errors import GroupStudioError
from src.storage.database import get_group_studio_snapshot

WORKER_STOP_TOOLS = {"group_post_message"}


@dataclass
class WorkerToolContext:
    """一次 Worker Run 内的可变上下文。"""

    group_id: str
    run_id: str
    member_id: str
    agent_id: str
    last_stop_tool: str = ""
    posted_body: str = ""
    posted_intent: str = ""


class WorkerTool(ToolBase):
    """Worker 群工具基类。"""

    is_mcp = False
    is_read_only = False
    is_concurrency_safe = False
    is_external_tool = False
    is_state_injected = False

    def __init__(self, ctx: WorkerToolContext) -> None:
        self.ctx = ctx

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Group Studio worker collaboration tool.",
            decision_reason="Group tools are not filesystem destructive",
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


class GroupReadBlackboardWorker(WorkerTool):
    name = "group_read_blackboard"
    description = "Read the consensus blackboard. Prefer this over long chat history."
    input_schema = {"type": "object", "properties": {}, "required": []}
    is_read_only = True

    async def __call__(self) -> ToolChunk:
        snapshot = get_group_studio_snapshot(self.ctx.group_id) or {}
        return self.success({"blackboard": snapshot.get("blackboard")})


class GroupPostMessage(WorkerTool):
    name = "group_post_message"
    description = "Post the visible group message for this turn and end the turn. Required after execution tools."
    input_schema = {
        "type": "object",
        "properties": {
            "body": {"type": "string"},
            "intent": {
                "type": "string",
                "enum": [
                    "discuss",
                    "propose_decision",
                    "ask_question",
                    "report_progress",
                    "request_help",
                ],
            },
        },
        "required": ["body"],
    }

    async def __call__(self, body: str, intent: str = "discuss") -> ToolChunk:
        text = str(body or "").strip()
        if not text:
            return self.error("empty_body")
        self.ctx.last_stop_tool = self.name
        self.ctx.posted_body = text
        self.ctx.posted_intent = intent or "discuss"
        return self.success({"posted": True, "intent": self.ctx.posted_intent})


class GroupProposeDecision(WorkerTool):
    name = "group_propose_decision"
    description = "Record a confirmed decision on the blackboard."
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def __call__(self, text: str) -> ToolChunk:
        from src.group_studio.service import patch_blackboard

        value = str(text or "").strip()
        if not value:
            return self.error("empty_decision")
        try:
            board = patch_blackboard(
                self.ctx.group_id,
                {"addDecisions": [{"text": value}]},
                asked_by=self.ctx.member_id,
                source_run_id=self.ctx.run_id or None,
            )
        except GroupStudioError as exc:
            return self.error(exc.code)
        return self.success({"blackboard": board})


class GroupRequestHelp(WorkerTool):
    name = "group_request_help"
    description = "Ask an unresolved question on the blackboard."
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def __call__(self, text: str) -> ToolChunk:
        from src.group_studio.service import patch_blackboard

        value = str(text or "").strip()
        if not value:
            return self.error("empty_question")
        try:
            board = patch_blackboard(
                self.ctx.group_id,
                {"addQuestions": [{"text": value}]},
                asked_by=self.ctx.member_id,
                source_run_id=self.ctx.run_id or None,
            )
        except GroupStudioError as exc:
            return self.error(exc.code)
        return self.success({"blackboard": board})


def worker_tools(ctx: WorkerToolContext) -> list[WorkerTool]:
    """Worker 群协作工具。"""

    return [
        GroupReadBlackboardWorker(ctx),
        GroupPostMessage(ctx),
        GroupProposeDecision(ctx),
        GroupRequestHelp(ctx),
    ]
