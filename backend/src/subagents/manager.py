"""Subagent 委托管理器。"""

from __future__ import annotations

import json
from typing import Any

from agentscope.message import TextBlock, ToolResultBlock, ToolResultState

from src.agents.runtime_types import (
    AgentGuardrails,
    AgentModelOptions,
    KnowledgeToolRuntimeSpec,
    McpRuntimeSpec,
    RuntimeToolScope,
)
from src.storage.database import (
    create_subagent_invocation,
    update_subagent_invocation,
)
from src.subagents.models import SubagentCatalog, SubagentDefinition
from src.subagents.runner import SubagentHitlRequired, SubagentRunError, SubagentRunner


class SubagentDelegationWaiting(RuntimeError):
    """父 DelegateTask 已暂停，等待子 Agent HITL 恢复。"""

    def __init__(self, interaction_id: str) -> None:
        super().__init__("Subagent delegation is waiting for human approval.")
        self.interaction_id = interaction_id


class SubagentDelegationManager:
    """管理本轮可用 Subagent 定义和运行事件缓冲。"""

    def __init__(
        self,
        *,
        catalog: SubagentCatalog,
        session_id: str,
        run_id: str,
        parent_scope: str,
        parent_agent_id: str,
        parent_agent_name: str,
        model_name: str,
        fallback_names: list[str],
        model_options: AgentModelOptions,
        guardrails: AgentGuardrails,
        mcp_servers: list[McpRuntimeSpec],
        knowledge_tools: list[KnowledgeToolRuntimeSpec],
        parent_tool_scope: RuntimeToolScope,
        persist: bool,
    ) -> None:
        self.catalog = catalog
        self.session_id = session_id
        self.run_id = run_id
        self.parent_scope = parent_scope
        self.parent_agent_id = parent_agent_id
        self.parent_agent_name = parent_agent_name
        self.model_name = model_name
        self.guardrails = guardrails
        self.persist = persist
        self._events: list[dict[str, Any]] = []
        self._runner = SubagentRunner(
            session_id=session_id,
            run_id=run_id,
            parent_scope=parent_scope,
            parent_agent_id=parent_agent_id,
            parent_agent_name=parent_agent_name,
            model_name=model_name,
            fallback_names=fallback_names,
            model_options=model_options,
            guardrails=guardrails,
            mcp_servers=mcp_servers,
            knowledge_tools=knowledge_tools,
            parent_tool_scope=parent_tool_scope,
            persist=persist,
        )

    def get_definition(self, name: str) -> SubagentDefinition | None:
        """按名称读取 Subagent 定义。"""

        return self.catalog.definitions.get(name)

    def drain_runtime_events(self) -> list[dict[str, Any]]:
        """取出待发送给主 Run 的 Subagent 委托事件。"""

        events = list(self._events)
        self._events.clear()
        return events

    async def delegate(
        self,
        *,
        subagent_name: str,
        task: str,
        expected_output: str = "",
        context: str = "",
        constraints: list[str] | None = None,
        tool_call_id: str = "",
        raise_on_waiting: bool = False,
    ) -> str:
        """执行 blocking Subagent 委托并返回给主 Agent 的 observation。"""

        definition = self.get_definition(subagent_name)
        if not definition:
            available = ", ".join(sorted(self.catalog.definitions)) or "none"
            raise ValueError(f"Unknown subagent '{subagent_name}'. Available subagents: {available}")

        invocation = create_subagent_invocation(
            {
                "run_id": self.run_id,
                "session_id": self.session_id,
                "tool_call_id": tool_call_id,
                "parent_scope": self.parent_scope,
                "parent_agent_id": self.parent_agent_id,
                "parent_agent_name": self.parent_agent_name,
                "definition_scope": definition.scope,
                "definition_path": definition.path,
                "subagent_name": definition.name,
                "status": "running",
                "task": task,
                "expected_output": expected_output,
            }
        )
        invocation_id = str(invocation["id"])
        self._emit(
            "SUBAGENT_STARTED",
            status="running",
            payload={
                "invocationId": invocation_id,
                "toolCallId": tool_call_id,
                "subagentName": definition.name,
                "definitionScope": definition.scope,
                "definitionPath": definition.path,
                "taskPreview": _trim_text(task, 500),
                "expectedOutput": expected_output,
            },
        )
        prepared = self._runner.prepare(definition)
        self._emit(
            "SUBAGENT_CONTEXT",
            status="running",
            payload={
                "invocationId": invocation_id,
                "toolCallId": tool_call_id,
                **prepared.audit_payload,
            },
        )
        try:
            result = await self._runner.run(
                invocation_id=invocation_id,
                definition=definition,
                task=task,
                expected_output=expected_output,
                context=context,
                constraints=constraints or [],
                runtime_context=prepared.runtime_context,
                permission=prepared.permission,
                runtime_event_sink=lambda event_type, event_data: self._emit_child_agent_event(
                    definition=definition,
                    invocation_id=invocation_id,
                    tool_call_id=tool_call_id,
                    event_type=event_type,
                    event_data=event_data,
                ),
            )
            result_preview = _trim_text(result.text, 1200)
            update_subagent_invocation(
                invocation_id,
                status="completed",
                result_preview=result_preview,
            )
            self._emit(
                "SUBAGENT_COMPLETED",
                status="completed",
                duration_ms=result.duration_ms,
                payload={
                    "invocationId": invocation_id,
                    "toolCallId": tool_call_id,
                    "subagentName": definition.name,
                    "resultPreview": result_preview,
                    "durationMs": result.duration_ms,
                },
            )
            return (
                f"Subagent {definition.name} completed.\n\n"
                f"Summary:\n{result.text}"
            )
        except SubagentHitlRequired as exc:
            partial = _trim_text(exc.partial_result, 1200)
            message = (
                "Subagent paused for human approval. "
                "The child agent can be resumed, but the parent DelegateTask is not automatically continued yet."
            )
            interaction_id = ""
            if exc.agent is not None:
                from src.server.routes.chat.hitl import _create_pending_interaction

                interaction = _create_pending_interaction(
                    interaction_type=exc.interaction_type,
                    agent=exc.agent,
                    session_id=self.session_id,
                    agent_id=f"subagent:{definition.name}",
                    agent_name=f"Subagent {definition.name}",
                    model_name=self.model_name,
                    reply_id=exc.reply_id,
                    tool_calls=exc.tool_calls,
                    resume_tool_calls=exc.tool_calls,
                    message=task,
                    assistant_text=partial,
                    skill_runs=[],
                    team_activities=[],
                    scope="subagent",
                    chat_data={
                        "invocationId": invocation_id,
                        "toolCallId": tool_call_id,
                        "subagentName": definition.name,
                        "definitionScope": definition.scope,
                        "definitionPath": definition.path,
                        "expectedOutput": expected_output,
                        "parentScope": self.parent_scope,
                        "parentAgentId": self.parent_agent_id,
                        "parentAgentName": self.parent_agent_name,
                    },
                    run_id=self.run_id,
                    persist=self.persist,
                )
                interaction_id = interaction.id
            update_subagent_invocation(
                invocation_id,
                status="waiting",
                result_preview=partial,
                error=message,
            )
            self._emit(
                "SUBAGENT_WAITING_HUMAN",
                status="waiting",
                duration_ms=exc.duration_ms,
                payload={
                    "invocationId": invocation_id,
                    "toolCallId": tool_call_id,
                    "subagentName": definition.name,
                    "interactionId": interaction_id,
                    "interactionType": exc.interaction_type,
                    "replyId": exc.reply_id,
                    "toolCalls": exc.tool_calls,
                    "partialResultPreview": partial,
                    "durationMs": exc.duration_ms,
                },
            )
            if raise_on_waiting:
                raise SubagentDelegationWaiting(interaction_id)
            return (
                f"Subagent {definition.name} paused for human approval.\n\n"
                "The delegated task reached a tool permission or external execution checkpoint. "
                "Resume the child interaction or rerun with narrower instructions before continuing.\n\n"
                f"Pending interaction: {exc.interaction_type}\n"
                f"Partial result:\n{partial or '(none)'}"
            )
        except SubagentRunError as exc:
            partial = _trim_text(exc.partial_result, 1200)
            update_subagent_invocation(
                invocation_id,
                status="failed",
                result_preview=partial,
                error=str(exc),
            )
            self._emit(
                "SUBAGENT_FAILED",
                status="failed",
                duration_ms=exc.duration_ms,
                payload={
                    "invocationId": invocation_id,
                    "toolCallId": tool_call_id,
                    "subagentName": definition.name,
                    "error": str(exc),
                    "partialResultPreview": partial,
                },
            )
            raise

    async def execute_external_tool_call(self, tool_call: dict[str, Any]) -> ToolResultBlock:
        """执行父 AgentScope 外部 DelegateTask 工具调用。"""

        tool_call_id = str(tool_call.get("id") or "")
        raw_input = str(tool_call.get("input") or "{}")
        try:
            data = json.loads(raw_input)
        except json.JSONDecodeError as exc:
            return ToolResultBlock(
                id=tool_call_id,
                name="DelegateTask",
                output=[TextBlock(text=f"Subagent delegation failed: invalid DelegateTask input JSON: {exc}")],
                state=ToolResultState.ERROR,
            )
        result = await self.delegate(
            subagent_name=str(data.get("subagentName") or ""),
            task=str(data.get("task") or ""),
            expected_output=str(data.get("expectedOutput") or ""),
            context=str(data.get("context") or ""),
            constraints=list(data.get("constraints") or []),
            tool_call_id=tool_call_id,
            raise_on_waiting=True,
        )
        return ToolResultBlock(
            id=tool_call_id,
            name="DelegateTask",
            output=[TextBlock(text=result)],
            state=ToolResultState.SUCCESS,
        )

    def _emit(
        self,
        event_type: str,
        *,
        status: str,
        payload: dict[str, Any],
        duration_ms: int | None = None,
    ) -> None:
        from src.server.routes.chat.runtime_events import build_runtime_event
        from src.server.routes.chat.trace_recorder import _record_run_event

        event = build_runtime_event(
            event_type,
            session_id=self.session_id,
            run_id=self.run_id,
            scope="subagent",
            source="runtime",
            agent_id=self.parent_agent_id,
            agent_name=self.parent_agent_name,
            status=status,
            duration_ms=duration_ms,
            payload=payload,
        )
        _record_run_event(
            session_id=self.session_id,
            event_type="RUNTIME_EVENT",
            persist=self.persist,
            scope="subagent",
            agent_id=self.parent_agent_id,
            agent_name=self.parent_agent_name,
            status=status,
            title=event_type,
            payload=event,
        )
        self._events.append(event)

    def _emit_child_agent_event(
        self,
        *,
        definition: SubagentDefinition,
        invocation_id: str,
        tool_call_id: str,
        event_type: str,
        event_data: dict[str, Any],
    ) -> None:
        """把子 Agent 关键执行事件转成父 Run 可展示的 Runtime Event。"""

        from src.server.routes.chat.event_types import AGENTSCOPE_TO_RUNTIME_EVENT
        from src.server.routes.chat.runtime_events import build_runtime_event

        runtime_type = AGENTSCOPE_TO_RUNTIME_EVENT.get(event_type)
        if not runtime_type:
            return
        if runtime_type in {
            "AGENT_OUTPUT_DELTA",
            "AGENT_THINKING",
            "TOOL_CALL_DELTA",
            "TOOL_RESULT_DELTA",
        } and not (runtime_type == "AGENT_OUTPUT_DELTA" and event_data.get("subagentFinalAnswerDelta")):
            return
        payload = _compact_child_runtime_payload(event_type, event_data)
        payload.update({
            "invocationId": invocation_id,
            "toolCallId": tool_call_id,
            "subagentName": definition.name,
        })
        self._events.append(build_runtime_event(
            runtime_type,
            session_id=self.session_id,
            run_id=self.run_id,
            scope="subagent",
            source="agentscope",
            source_event_type=event_type,
            agent_id=f"subagent:{definition.name}",
            agent_name=f"Subagent {definition.name}",
            status=_status_for_child_runtime_event(runtime_type, event_data),
            payload=payload,
        ))


def _trim_text(text: str, limit: int) -> str:
    """裁剪事件和审计摘要文本，避免 Subagent observation 撑爆事件 payload。"""

    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def _compact_child_runtime_payload(event_type: str, event_data: dict[str, Any]) -> dict[str, Any]:
    """保留 Subagent 过程面板需要的轻量字段。"""

    keys = [
        "model_name",
        "tool_call_id",
        "tool_call_name",
        "reply_id",
        "state",
        "delta",
        "subagentFinalAnswerDelta",
        "input_tokens",
        "output_tokens",
    ]
    payload = {key: event_data[key] for key in keys if key in event_data}
    payload["sourceEventType"] = event_type
    return payload


def _status_for_child_runtime_event(runtime_type: str, event_data: dict[str, Any]) -> str:
    """按 AgentScope 事件状态归一化子 Agent RuntimeEvent 状态。"""

    if runtime_type == "TOOL_RESULT_COMPLETED":
        state = str(event_data.get("state") or "").lower()
        if state in {"error", "failed", "denied"}:
            return "failed"
    if runtime_type.endswith("_COMPLETED") or runtime_type == "AGENT_COMPLETED":
        return "completed"
    if runtime_type.endswith("_FAILED") or runtime_type == "AGENT_FAILED":
        return "failed"
    return "running"
