"""Subagent attached fork/join 编排器。

YAML/Markdown 继续定义 Subagent 能力；本模块只把父 Agent 工具命令转换为文件任务、
Supervisor 执行和结果消费状态。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentscope.message import TextBlock, ToolResultBlock, ToolResultState

from src.agents.runtime_types import (
    AgentGuardrails,
    AgentModelOptions,
    KnowledgeToolRuntimeSpec,
    McpRuntimeSpec,
    RuntimeToolScope,
)
from src.runtime_context import RuntimeContext
from src.storage.database import (
    append_subagent_transcript_item,
    update_subagent_invocation_delivery,
)
from src.subagents.models import SubagentCatalog, SubagentDefinition
from src.subagents.runner import SubagentHitlRequired, SubagentRunError, SubagentRunner
from src.subagents.task_service import SubagentTaskService
from src.subagents.task_store import (
    TERMINAL_SUBAGENT_STATUSES,
    SubagentTaskStore,
    SubagentTaskStoreError,
)
from src.subagents.supervisor import get_subagent_supervisor


def subagent_error_observation(
    exc: Exception,
    *,
    invocation_id: str = "",
    subagent_name: str = "",
) -> str:
    """构造给父 Agent 消费的稳定失败 observation。"""

    partial_result = exc.partial_result if isinstance(exc, SubagentRunError) else ""
    failure_code = str(getattr(exc, "failure_code", "subagent_delegation_failed"))
    return json.dumps({
        "ok": False,
        "invocationId": invocation_id or str(getattr(exc, "invocation_id", "")),
        "subagentName": subagent_name or str(getattr(exc, "subagent_name", "")),
        "failureCode": failure_code,
        "error": str(exc),
        "partialResult": partial_result,
    }, ensure_ascii=False)


class SubagentDelegationManager:
    """管理当前父 Run 的 attached Subagent tasks。"""

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
        runtime_context: RuntimeContext | None = None,
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
        self.runtime_context = runtime_context
        self._events: list[dict[str, Any]] = []
        self._task_service = SubagentTaskService()
        self._supervisor = get_subagent_supervisor()
        project_root = (
            runtime_context.project_root
            if runtime_context is not None
            else Path(guardrails.filesystem_scope)
        )
        self._task_store = SubagentTaskStore(project_root=project_root, session_id=session_id)
        self._delivered_by_tool_call: dict[str, list[str]] = {}
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
            platform_context=runtime_context,
        )

    def get_definition(self, name: str) -> SubagentDefinition | None:
        """按名称读取 Subagent 定义。"""

        return self.catalog.definitions.get(name)

    def drain_runtime_events(self) -> list[dict[str, Any]]:
        """取出待进入父 Run projection 的轻量任务事件。"""

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
        **_: Any,
    ) -> str:
        """Fork attached task 并立即返回稳定任务引用。"""

        definition = self.get_definition(subagent_name)
        if not definition:
            available = ", ".join(sorted(self.catalog.definitions)) or "none"
            raise ValueError(f"Unknown subagent '{subagent_name}'. Available subagents: {available}")
        normalized_task = task.strip()
        if not normalized_task:
            raise ValueError("Subagent task 不能为空")

        invocation_id = f"subagent-{uuid4().hex}"
        normalized_constraints = [str(item).strip() for item in constraints or [] if str(item).strip()]
        file_status = self._task_store.create(
            invocation_id=invocation_id,
            run_id=self.run_id,
            tool_call_id=tool_call_id,
            parent_scope=self.parent_scope,
            parent_agent_id=self.parent_agent_id,
            parent_agent_name=self.parent_agent_name,
            definition=definition,
            task=normalized_task,
            expected_output=expected_output,
            context=context,
            constraints=normalized_constraints,
        )
        self._task_service.create({
            "id": invocation_id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "workspace_id": self.runtime_context.workspace_id if self.runtime_context else "default",
            "project_id": self.runtime_context.project_id if self.runtime_context else "default",
            "tool_call_id": tool_call_id,
            "parent_scope": self.parent_scope,
            "parent_agent_id": self.parent_agent_id,
            "parent_agent_name": self.parent_agent_name,
            "definition_scope": definition.scope,
            "definition_path": definition.path,
            "subagent_name": definition.name,
            "execution_mode": "attached",
            "task": normalized_task,
            "expected_output": expected_output,
            "result_ref": file_status["resultRef"],
            "delivery_status": "none",
        })
        self._emit("SUBAGENT_STARTED", status="queued", payload={
            **self._projection_payload(file_status),
            "toolCallId": tool_call_id,
            "definitionScope": definition.scope,
            "definitionPath": definition.path,
            "expectedOutput": expected_output,
        })
        await self._supervisor.start(
            invocation_id,
            lambda: self._execute_task(
                invocation_id=invocation_id,
                definition=definition,
                task=normalized_task,
                expected_output=expected_output,
                context=context,
                constraints=normalized_constraints,
                tool_call_id=tool_call_id,
            ),
            on_cancel=lambda: self._cancel_task_state(invocation_id),
        )
        return json.dumps({
            "ok": True,
            "mode": "attached",
            "invocationId": invocation_id,
            "subagentName": definition.name,
            "status": "queued",
            "resultRef": file_status["resultRef"],
            "next": "Continue other work, then call SubagentTaskWait and SubagentResultRead before finalizing.",
        }, ensure_ascii=False)

    async def _execute_task(
        self,
        *,
        invocation_id: str,
        definition: SubagentDefinition,
        task: str,
        expected_output: str,
        context: str,
        constraints: list[str],
        tool_call_id: str,
    ) -> None:
        """在 Supervisor 取得并发槽位后执行 child，并原子提交结果。"""

        try:
            self._task_service.transition(
                invocation_id,
                target_status="running",
                reason="supervisor_slot_acquired",
            )
            running = self._task_store.set_status(invocation_id, "running")
            self._emit("SUBAGENT_UPDATED", status="running", payload=self._projection_payload(running))
            prepared = self._runner.prepare(definition)
            self._emit("SUBAGENT_CONTEXT", status="running", payload={
                "invocationId": invocation_id,
                "toolCallId": tool_call_id,
                **prepared.audit_payload,
            })
            result = await self._runner.run(
                invocation_id=invocation_id,
                definition=definition,
                task=task,
                expected_output=expected_output,
                context=context,
                constraints=constraints,
                runtime_context=prepared.runtime_context,
                permission=prepared.permission,
                tool_policy=getattr(prepared, "tool_policy", None),
                runtime_event_sink=lambda event_type, event_data: self._record_child_agent_event(
                    invocation_id=invocation_id,
                    event_type=event_type,
                    event_data=event_data,
                ),
            )
            self._task_store.write_result(invocation_id, result.text)
            completed = self._task_store.set_status(
                invocation_id,
                "completed",
                duration_ms=result.duration_ms,
            )
            self._task_service.transition(
                invocation_id,
                target_status="completed",
                result_preview=str(completed.get("resultPreview") or ""),
            )
            self._mirror_delivery(completed)
            self._emit(
                "SUBAGENT_COMPLETED",
                status="completed",
                duration_ms=result.duration_ms,
                payload=self._projection_payload(completed),
            )
        except SubagentHitlRequired as exc:
            await self._handle_waiting(
                invocation_id=invocation_id,
                definition=definition,
                task=task,
                expected_output=expected_output,
                tool_call_id=tool_call_id,
                exc=exc,
            )
        except asyncio.CancelledError:
            await self._cancel_task_state(invocation_id)
            raise
        except Exception as exc:
            run_error = exc if isinstance(exc, SubagentRunError) else SubagentRunError(
                f"Subagent 委托执行失败: {exc}",
            )
            partial = str(getattr(run_error, "partial_result", "") or "")
            content = "\n\n".join(part for part in [
                f"Subagent {definition.name} failed.",
                str(run_error),
                f"Partial result:\n{partial}" if partial else "",
            ] if part)
            self._task_store.write_result(invocation_id, content, error=str(run_error))
            failed = self._task_store.set_status(
                invocation_id,
                "failed",
                error=str(run_error),
                duration_ms=int(getattr(run_error, "duration_ms", 0) or 0),
            )
            self._safe_index_transition(
                invocation_id,
                "failed",
                result_preview=str(failed.get("resultPreview") or ""),
                error=str(run_error),
            )
            self._mirror_delivery(failed)
            self._emit("SUBAGENT_FAILED", status="failed", payload=self._projection_payload(failed))

    async def _handle_waiting(
        self,
        *,
        invocation_id: str,
        definition: SubagentDefinition,
        task: str,
        expected_output: str,
        tool_call_id: str,
        exc: SubagentHitlRequired,
    ) -> None:
        """持久化 child HITL；waiting 不是终态，不生成 result.md。"""

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
                assistant_text=exc.partial_result,
                skill_runs=[],
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
                    "taskStoreRoot": str(self._task_store.root),
                    "projectRoot": str(self._task_store.project_root),
                },
                run_id=self.run_id,
                persist=self.persist,
            )
            interaction_id = interaction.id
        message = "Subagent paused for human approval."
        waiting = self._task_store.set_status(
            invocation_id,
            "waiting",
            error=message,
            duration_ms=exc.duration_ms,
        )
        self._task_service.transition(
            invocation_id,
            target_status="waiting",
            result_preview=_trim_text(exc.partial_result, 1200),
            error=message,
            reason="hitl_required",
        )
        self._emit("SUBAGENT_WAITING_HUMAN", status="waiting", payload={
            **self._projection_payload(waiting),
            "interactionId": interaction_id,
            "interactionType": exc.interaction_type,
            "replyId": exc.reply_id,
            "toolCalls": exc.tool_calls,
        })

    def list_tasks(self) -> str:
        """返回父 Run attached children 的轻量状态，不读取结果内容。"""

        items = [self._public_status(item) for item in self._task_store.list_for_run(self.run_id)]
        return json.dumps({"ok": True, "tasks": items}, ensure_ascii=False)

    async def wait_tasks(
        self,
        invocation_ids: list[str],
        *,
        wait_mode: str = "all",
        timeout_seconds: int = 300,
    ) -> str:
        """等待 any/all task 离开 active 状态，只返回状态和 resultRef。"""

        ids = list(dict.fromkeys(str(item).strip() for item in invocation_ids if str(item).strip()))
        if not ids:
            raise ValueError("invocationIds 不能为空")
        mode = wait_mode if wait_mode in {"any", "all"} else "all"
        deadline = asyncio.get_running_loop().time() + max(1, min(int(timeout_seconds), 1800))
        while True:
            statuses = [self._task_store.status(invocation_id) for invocation_id in ids]
            ready = [item for item in statuses if str(item.get("status") or "") not in {"queued", "running"}]
            if (mode == "any" and ready) or (mode == "all" and len(ready) == len(statuses)):
                return json.dumps({
                    "ok": True,
                    "waitMode": mode,
                    "tasks": [self._public_status(item) for item in statuses],
                    "readyInvocationIds": [str(item["invocationId"]) for item in ready],
                    "next": "Call SubagentResultRead for every terminal task before finalizing.",
                }, ensure_ascii=False)
            if asyncio.get_running_loop().time() >= deadline:
                return json.dumps({
                    "ok": False,
                    "failureCode": "subagent_wait_timeout",
                    "waitMode": mode,
                    "tasks": [self._public_status(item) for item in statuses],
                }, ensure_ascii=False)
            await asyncio.sleep(0.1)

    def read_result(self, invocation_id: str, *, tool_call_id: str) -> str:
        """读取 result.md 并登记本次父工具调用需要在后续 acting 后确认消费。"""

        content, delivered = self._task_store.read_result(invocation_id)
        self._mirror_delivery(delivered)
        self._delivered_by_tool_call.setdefault(tool_call_id, []).append(invocation_id)
        self._emit("SUBAGENT_UPDATED", status=str(delivered.get("status") or "completed"), payload={
            **self._projection_payload(delivered),
            "deliveryStatus": "delivered",
        })
        return "\n".join([
            f"Subagent result: {invocation_id}",
            f"Source: {delivered.get('resultRef')}",
            "",
            content.rstrip(),
        ])

    async def cancel_task(self, invocation_id: str) -> str:
        """取消 attached task；终态取消结果仍需父 Agent读取。"""

        status = self._task_store.status(invocation_id)
        if str(status.get("runId") or "") != self.run_id:
            raise ValueError("Subagent task 不属于当前父 Run")
        if str(status.get("status") or "") in TERMINAL_SUBAGENT_STATUSES:
            return json.dumps({"ok": True, "task": self._public_status(status)}, ensure_ascii=False)
        accepted = await self._supervisor.cancel(invocation_id)
        return json.dumps({
            "ok": accepted,
            "invocationId": invocation_id,
            "status": "cancellation_requested" if accepted else str(status.get("status") or ""),
        }, ensure_ascii=False)

    async def _cancel_task_state(self, invocation_id: str) -> None:
        """幂等收口 queued/running task 的取消状态和可消费结果。"""

        current = self._task_store.status(invocation_id)
        if str(current.get("status") or "") in TERMINAL_SUBAGENT_STATUSES:
            return
        message = "Subagent 执行已取消。"
        self._task_store.write_result(invocation_id, "", error=message)
        cancelled = self._task_store.set_status(invocation_id, "cancelled", error=message)
        self._safe_index_transition(invocation_id, "cancelled", error=message)
        self._mirror_delivery(cancelled)
        self._emit("SUBAGENT_CANCELLED", status="cancelled", payload=self._projection_payload(cancelled))

    def mark_results_consumed_for_tool_calls(self, tool_calls: list[dict[str, Any]]) -> None:
        """父 Agent收到读取结果后完成一次 continuation，推进 consumed。"""

        invocation_ids: list[str] = []
        for tool_call in tool_calls:
            invocation_ids.extend(self._delivered_by_tool_call.pop(str(tool_call.get("id") or ""), []))
        for consumed in self._task_store.mark_consumed(invocation_ids):
            self._mirror_delivery(consumed)
            self._emit("SUBAGENT_CONSUMED", status="completed", payload={
                **self._projection_payload(consumed),
                "taskStatus": consumed.get("status"),
            })

    def completion_error(self) -> str:
        """父 Run completion barrier：所有 attached child 必须终态且 consumed。"""

        tasks = self._task_store.list_for_run(self.run_id)
        active = [item for item in tasks if item.get("status") not in TERMINAL_SUBAGENT_STATUSES]
        unread = [item for item in tasks if item.get("deliveryStatus") != "consumed"]
        if not active and not unread:
            return ""
        details = ", ".join(
            f"{item.get('invocationId')}={item.get('status')}/{item.get('deliveryStatus')}"
            for item in tasks
            if item in active or item in unread
        )
        return (
            "父任务仍有未完成或未消费的 attached Subagent，不能结束。"
            "请调用 SubagentTaskWait，再用 SubagentResultRead 读取每个终态结果后继续回答。"
            f" 当前状态: {details}"
        )

    async def execute_external_tool_call(self, tool_call: dict[str, Any]) -> ToolResultBlock:
        """执行父 AgentScope 的 Subagent 外部工具调用。"""

        tool_call_id = str(tool_call.get("id") or "")
        tool_name = str(tool_call.get("name") or "")
        try:
            data = json.loads(str(tool_call.get("input") or "{}"))
            if not isinstance(data, dict):
                raise ValueError("tool input 必须是 JSON object")
            if tool_name == "DelegateTask":
                text = await self.delegate(
                    subagent_name=str(data.get("subagentName") or ""),
                    task=str(data.get("task") or ""),
                    expected_output=str(data.get("expectedOutput") or ""),
                    context=str(data.get("context") or ""),
                    constraints=list(data.get("constraints") or []),
                    tool_call_id=tool_call_id,
                )
            elif tool_name == "SubagentTaskList":
                text = self.list_tasks()
            elif tool_name == "SubagentTaskWait":
                text = await self.wait_tasks(
                    list(data.get("invocationIds") or []),
                    wait_mode=str(data.get("waitMode") or "all"),
                    timeout_seconds=int(data.get("timeoutSeconds") or 300),
                )
            elif tool_name == "SubagentResultRead":
                text = self.read_result(
                    str(data.get("invocationId") or ""),
                    tool_call_id=tool_call_id,
                )
            elif tool_name == "SubagentTaskCancel":
                text = await self.cancel_task(str(data.get("invocationId") or ""))
            else:
                raise ValueError(f"Unknown Subagent tool: {tool_name}")
            state = ToolResultState.SUCCESS
        except Exception as exc:
            text = subagent_error_observation(exc)
            state = ToolResultState.ERROR
        return ToolResultBlock(
            id=tool_call_id,
            name=tool_name or "SubagentTool",
            output=[TextBlock(text=text)],
            state=state,
        )

    def _safe_index_transition(
        self,
        invocation_id: str,
        target_status: str,
        *,
        result_preview: str = "",
        error: str = "",
    ) -> None:
        try:
            self._task_service.transition(
                invocation_id,
                target_status=target_status,
                result_preview=result_preview,
                error=error,
            )
        except Exception:
            pass

    @staticmethod
    def _mirror_delivery(status: dict[str, Any]) -> None:
        update_subagent_invocation_delivery(
            str(status.get("invocationId") or ""),
            delivery_status=str(status.get("deliveryStatus") or "none"),
            result_ref=str(status.get("resultRef") or ""),
        )

    def _record_child_agent_event(
        self,
        *,
        invocation_id: str,
        event_type: str,
        event_data: dict[str, Any],
    ) -> None:
        """完整 child 过程只写 transcript，不注入父 Agent context。"""

        self._append_transcript(invocation_id, event_type, _compact_child_payload(event_data))

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
        invocation_id = str(payload.get("invocationId") or "")
        if invocation_id:
            self._append_transcript(invocation_id, event_type, payload)
        self._events.append(event)

    def _append_transcript(self, invocation_id: str, kind: str, payload: dict[str, Any]) -> None:
        self._task_store.append_transcript(invocation_id, kind=kind, payload=payload)
        append_subagent_transcript_item(invocation_id=invocation_id, kind=kind, payload=payload)

    @staticmethod
    def _projection_payload(status: dict[str, Any]) -> dict[str, Any]:
        return {
            key: status.get(key)
            for key in (
                "invocationId",
                "subagentName",
                "taskPreview",
                "status",
                "deliveryStatus",
                "resultRef",
                "resultPreview",
                "error",
                "durationMs",
                "updatedAt",
            )
        }

    @staticmethod
    def _public_status(status: dict[str, Any]) -> dict[str, Any]:
        return SubagentDelegationManager._projection_payload(status)


def _trim_text(text: str, limit: int) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[: max(0, limit - 1)] + "…"


def _compact_child_payload(event_data: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "model_name",
        "tool_call_id",
        "tool_call_name",
        "reply_id",
        "state",
        "input_tokens",
        "output_tokens",
    )
    return {key: event_data[key] for key in keys if key in event_data}
