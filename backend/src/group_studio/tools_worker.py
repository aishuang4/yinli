"""Worker 群工具。V2 只注入 `group_post_message`，说完本轮结束。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk

WORKER_STOP_TOOLS = {"group_post_message"}


@dataclass
class WorkerToolContext:
    """按成员复用的 Worker 工具上下文。

    缓存 Agent 的工具闭包必须指向同一对象；每轮只改 run_id / posted_body。
    """

    group_id: str
    run_id: str
    member_id: str
    agent_id: str
    last_stop_tool: str = ""
    posted_body: str = ""
    posted_intent: str = ""

    def begin_run(self, run_id: str) -> None:
        """绑定本轮 Run，清空上一轮正文。"""

        self.run_id = run_id
        self.last_stop_tool = ""
        self.posted_body = ""
        self.posted_intent = ""


_WORKER_CONTEXTS: dict[tuple[str, str], WorkerToolContext] = {}


def acquire_worker_tool_context(
    group_id: str,
    member_id: str,
    agent_id: str,
) -> WorkerToolContext:
    """返回该成员的 Worker 工具上下文；缓存 Agent 与运行时共用。"""

    key = (group_id, member_id)
    existing = _WORKER_CONTEXTS.get(key)
    if existing is None:
        existing = WorkerToolContext(
            group_id=group_id,
            run_id="",
            member_id=member_id,
            agent_id=agent_id,
        )
        _WORKER_CONTEXTS[key] = existing
    else:
        existing.agent_id = agent_id
    return existing


def drop_worker_tool_contexts(group_id: str) -> None:
    """归档时丢掉该工作室全部 Worker 上下文。"""

    for key in [item for item in _WORKER_CONTEXTS if item[0] == group_id]:
        _WORKER_CONTEXTS.pop(key, None)


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
        """群协作工具一律放行，不走 Chat HITL。"""

        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Group Studio worker collaboration tool.",
            decision_reason="Group tools are not filesystem destructive",
        )

    def success(self, payload: dict[str, Any]) -> ToolChunk:
        """成功工具结果。"""

        return ToolChunk(
            content=[TextBlock(text=json.dumps(payload, ensure_ascii=False, indent=2))],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )

    def error(self, message: str) -> ToolChunk:
        """失败工具结果。"""

        return ToolChunk(
            content=[TextBlock(text=json.dumps({"error": message}, ensure_ascii=False))],
            state=ToolResultState.ERROR,
            is_last=True,
        )


class GroupPostMessage(WorkerTool):
    """把本轮可见正文交给运行时收口，并结束这一轮。"""

    name = "group_post_message"
    description = "Post the visible group message for this turn and end the turn."
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


def worker_tools(ctx: WorkerToolContext) -> list[WorkerTool]:
    """V2 工人只允许对群发言。黑板/工作项工具已删除。"""

    return [GroupPostMessage(ctx)]
