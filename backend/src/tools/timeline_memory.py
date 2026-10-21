"""时间线检索与摄取工具。

检索把原始对话交给当前这一步；摄取只留下模型要的内容，原文随后从上下文抽掉。
"""

from __future__ import annotations

import json
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk

from src.memory.timeline import search_timeline
from src.memory.trace import record_memory_audit_event


class TimelineRetrieve(ToolBase):
    """按标签和时间检索时间线上的原始对话。"""

    name: str = "timeline_retrieve"
    description: str = (
        "从时间线检索原始对话。可按项目、智能体、场合（chat/group/job）、"
        "时间起迄、说话方和关键词过滤。全空表示在整条时间线上查找，仍会限制返回量。"
        "查到后请立刻调用 timeline_ingest，只留下这次任务需要的内容。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "projectSlug": {"type": "string", "description": "可选项目 slug。空表示不限。"},
            "agentId": {"type": "string", "description": "可选智能体。空表示不限。"},
            "surface": {"type": "string", "description": "可选场合：chat、group、job。空表示全部。"},
            "timeFrom": {"type": "string", "description": "可选开始时间，ISO 格式。"},
            "timeTo": {"type": "string", "description": "可选结束时间，ISO 格式。"},
            "speaker": {"type": "string", "description": "可选说话方：user 或 assistant。"},
            "queryText": {"type": "string", "description": "可选关键词，对正文做包含匹配。"},
        },
        "required": [],
    }
    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False
    audit_session_id: str = ""
    audit_agent_id: str = ""
    audit_agent_name: str = ""

    def __init__(
        self,
        audit_session_id: str = "",
        audit_agent_id: str = "",
        audit_agent_name: str = "",
        *,
        project_slug: str = "",
    ) -> None:
        super().__init__()
        self.audit_session_id = audit_session_id
        self.audit_agent_id = audit_agent_id
        self.audit_agent_name = audit_agent_name
        self.project_slug = project_slug

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="timeline_retrieve 只读取已有聊天记录。",
            decision_reason="Read-only timeline query",
        )

    async def __call__(
        self,
        projectSlug: str = "",
        agentId: str = "",
        surface: str = "",
        timeFrom: str = "",
        timeTo: str = "",
        speaker: str = "",
        queryText: str = "",
        **kwargs: Any,
    ) -> ToolChunk:
        payload = search_timeline(
            project_slug=projectSlug or self.project_slug,
            agent_id=agentId,
            surface=surface,
            time_from=timeFrom,
            time_to=timeTo,
            speaker=speaker,
            query_text=queryText,
        )
        record_memory_audit_event(
            session_id=self.audit_session_id,
            persist=bool(self.audit_session_id),
            agent_id=self.audit_agent_id,
            agent_name=self.audit_agent_name,
            action="timeline_retrieve",
            status="completed",
            title="Timeline retrieved",
            content=queryText,
            payload={
                "hitCount": payload.get("hitCount"),
                "messageCount": len(payload.get("messages") or []),
                "truncated": bool(payload.get("truncated")),
                "filters": {
                    "projectSlug": projectSlug or self.project_slug,
                    "agentId": agentId,
                    "surface": surface,
                    "timeFrom": timeFrom,
                    "timeTo": timeTo,
                    "speaker": speaker,
                },
            },
        )
        return ToolChunk(
            content=[TextBlock(text=json.dumps(payload, ensure_ascii=False))],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )


class TimelineIngest(ToolBase):
    """把这次任务需要的时间线内容留下，不写回长期记忆。"""

    name: str = "timeline_ingest"
    description: str = (
        "在 timeline_retrieve 之后调用。把这次任务真正要用的内容写成 note。"
        "note 会留在当前会话里；检索到的原文不会写入长期记忆，也会从后续上下文移除。"
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "note": {"type": "string", "description": "这次任务需要留下的内容。"},
            "sourceIds": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选，本轮检索到的消息 id。",
            },
        },
        "required": ["note"],
    }
    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False
    audit_session_id: str = ""
    audit_agent_id: str = ""
    audit_agent_name: str = ""

    def __init__(
        self,
        audit_session_id: str = "",
        audit_agent_id: str = "",
        audit_agent_name: str = "",
    ) -> None:
        super().__init__()
        self.audit_session_id = audit_session_id
        self.audit_agent_id = audit_agent_id
        self.audit_agent_name = audit_agent_name

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="timeline_ingest 只把摄取结果留在当前会话。",
            decision_reason="Session-scoped timeline ingest",
        )

    async def __call__(self, note: str = "", sourceIds: list[str] | None = None, **kwargs: Any) -> ToolChunk:
        text = " ".join(str(note or "").split())
        if not text:
            return ToolChunk(
                content=[TextBlock(text="note 不能为空")],
                state=ToolResultState.ERROR,
                is_last=True,
            )
        record_memory_audit_event(
            session_id=self.audit_session_id,
            persist=bool(self.audit_session_id),
            agent_id=self.audit_agent_id,
            agent_name=self.audit_agent_name,
            action="timeline_ingest",
            status="completed",
            title="Timeline ingested",
            content=text[:240],
            payload={"sourceIds": list(sourceIds or []), "noteChars": len(text)},
        )
        return ToolChunk(
            content=[TextBlock(text=text)],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )
