"""把进程内 AgentScope/领域信号直接归一化为持久 Domain Event。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .completion_barrier import ActiveDomainBlock, DomainCompletionBarrier
from .domain_events import DomainEvent, DomainEventDraft, DomainEventRepository
from .team_event_normalizer import TeamEventNormalizerMixin


_SESSION_RESOURCE_FIELDS = (
    "sessionId",
    "snapshotId",
    "agentId",
    "directoryGrantVersion",
    "resourceGrantVersion",
    "workingDirectory",
    "skills",
    "tools",
)


def _session_resource_payload(value: Any) -> dict[str, Any]:
    """只保留契約允許的 Session 資源欄位，丟掉正文與 secret。"""

    if not isinstance(value, dict):
        return {}
    payload: dict[str, Any] = {}
    for key in _SESSION_RESOURCE_FIELDS:
        if key not in value:
            continue
        item = value[key]
        if key in {"skills", "tools"}:
            if not isinstance(item, list):
                continue
            payload[key] = [str(entry) for entry in item if isinstance(entry, str)]
        elif key in {"directoryGrantVersion", "resourceGrantVersion"}:
            if isinstance(item, int) and not isinstance(item, bool):
                payload[key] = item
        elif isinstance(item, str):
            payload[key] = item
    return payload


class ChatProjectionNormalizer(TeamEventNormalizerMixin):
    """单 Run normalizer；源信号不持久化、不作为 SSE 或 replay 契约。"""

    def __init__(
        self,
        *,
        stream_key: str,
        run_id: str,
        session_id: str,
        events: DomainEventRepository,
    ) -> None:
        self.stream_key = stream_key
        self.run_id = run_id
        self.session_id = session_id
        self.events = events
        self.message_id = f"message:{run_id}"
        self._revisions: dict[tuple[str, str], int] = defaultdict(int)
        self._active: dict[str, ActiveDomainBlock] = {}
        self._block_parents: dict[str, str] = {}
        self._pending_team_task_terminals: dict[str, tuple[str, str, dict[str, Any]]] = {}
        self._terminal_team_tasks: set[str] = set()
        self._terminal_team_tools: set[str] = set()
        self._reasoning_index = 0
        self._thinking_index = 0
        self._reasoning_id = ""
        self._thinking_id = ""
        self._text_id = f"text:{run_id}"
        self._started = False
        self._terminal = False
        self._barrier = DomainCompletionBarrier()

    def start(self, *, agent_name: str = "") -> list[DomainEvent]:
        if self._started:
            return []
        self._started = True
        return [self._append(
            "message.started",
            aggregate_type="message",
            aggregate_id=self.message_id,
            data={
                "blockId": self.message_id,
                "role": "assistant",
                "agentName": agent_name,
            },
        )]

    def ingest(self, event_type: str, data: dict[str, Any]) -> list[DomainEvent]:
        """消费一个实时源信号；未知信号只留在 Trace，不污染 UI Domain Stream。"""

        if self._terminal:
            return []
        handler = {
            "REASONING_DELTA": self._reasoning_delta,
            "THINKING_BLOCK_DELTA": self._thinking_delta,
            "FINAL_ANSWER_DELTA": self._answer_delta,
            "FINAL_ANSWER_REPLACE": self._answer_replace,
            "ANSWER_READY": self._answer_ready,
            "TOOL_CALL_START": self._tool_started,
            "TOOL_CALL_DELTA": self._tool_input_delta,
            "TOOL_CALL_END": self._tool_input_completed,
            "TOOL_RESULT_TEXT_DELTA": self._tool_output_delta,
            "TOOL_RESULT_END": self._tool_completed,
            "SKILL_CONTEXT": self._skill_started,
            "SKILL_END": self._skill_completed,
            "HITL_REQUIRED": self._interaction_started,
            "CLARIFICATION_REQUIRED": self._interaction_started,
            "TEAM_DOMAIN_EVENT": self._team_event,
            "ERROR": self._error,
            "CONTEXT_USAGE": self._context_usage,
            "RUN_CANCELLED": lambda value: self.finish("cancelled", value),
        }.get(event_type)
        if handler:
            return handler(data)
        if event_type in {"RUN_COMPLETED", "RUN_FAILED"}:
            terminal_data = data.get("payload") if isinstance(data.get("payload"), dict) else data
            return self.finish("completed" if event_type == "RUN_COMPLETED" else "failed", terminal_data)
        if event_type == "HITL_RESOLVED":
            return self._interaction_completed(data.get("payload") if isinstance(data.get("payload"), dict) else data)
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
        if event_type.startswith("PLAN_"):
            return self._plan_event(event_type, payload)
        if event_type.startswith("ARTIFACT_"):
            return self._artifact_event(event_type, payload)
        if event_type.startswith("SUBAGENT_"):
            return self._subagent_event(event_type, payload)
        return []

    def finish(self, outcome: str, data: dict[str, Any] | None = None) -> list[DomainEvent]:
        """先经过 completion barrier，再 append 不可逆 Run 终态事实。"""

        if self._terminal:
            return []
        normalized = outcome if outcome in {"completed", "failed", "cancelled"} else "failed"
        produced: list[DomainEvent] = []
        for block, event_type, event_data in self._barrier.terminal_events(
            list(self._active.values()),
            run_outcome=normalized,
        ):
            produced.append(self._append(
                event_type,
                aggregate_type=block.aggregate_type,
                aggregate_id=block.aggregate_id,
                data=event_data,
            ))
            self._active.pop(block.block_id, None)
        terminal_data = dict(data or {})
        terminal_data["messageId"] = self.message_id
        produced.append(self._append(
            f"run.{normalized}",
            aggregate_type="run",
            aggregate_id=self.run_id,
            data=terminal_data,
        ))
        self._terminal = True
        return produced

    def _reasoning_delta(self, data: dict[str, Any]) -> list[DomainEvent]:
        delta = str(data.get("delta") or "")
        if not delta:
            return []
        produced: list[DomainEvent] = []
        if not self._reasoning_id:
            self._reasoning_index += 1
            self._reasoning_id = f"reasoning:{self.run_id}:{self._reasoning_index}"
            produced.extend(self._start_block("reasoning", self._reasoning_id, "reasoning", ""))
        produced.append(self._delta_block("reasoning", self._reasoning_id, "reasoning", delta))
        return produced

    def _thinking_delta(self, data: dict[str, Any]) -> list[DomainEvent]:
        delta = str(data.get("delta") or "")
        if not delta:
            return []
        produced: list[DomainEvent] = []
        if not self._thinking_id:
            self._thinking_index += 1
            self._thinking_id = f"thinking:{self.run_id}:{self._thinking_index}"
            produced.extend(self._start_block("thinking", self._thinking_id, "thinking", ""))
        produced.append(self._delta_block("thinking", self._thinking_id, "thinking", delta))
        return produced

    def _answer_delta(self, data: dict[str, Any]) -> list[DomainEvent]:
        delta = str(data.get("delta") or "")
        if not delta:
            return []
        block_id = self._text_block_id(data.get("generation"))
        produced = self._close_process_text()
        produced.extend(self._ensure_text(block_id))
        produced.append(self._delta_block("agent.output", block_id, "text", delta))
        return produced

    def _answer_replace(self, data: dict[str, Any]) -> list[DomainEvent]:
        content = str(data.get("content") or "")
        block_id = self._text_block_id(data.get("generation"))
        produced = self._close_process_text()
        produced.extend(self._ensure_text(block_id))
        produced.append(self._delta_block(
            "agent.output",
            block_id,
            "text",
            {"replace": content},
        ))
        if content.strip():
            produced.extend(self._mark_answer_ready(block_id))
        return produced

    def _answer_ready(self, data: dict[str, Any]) -> list[DomainEvent]:
        if not self._started:
            return []
        return self._mark_answer_ready(self._text_block_id(data.get("generation")))

    def _tool_started(self, data: dict[str, Any]) -> list[DomainEvent]:
        produced = self._close_process_text()
        call_id = str(data.get("tool_call_id") or data.get("id") or "")
        if not call_id:
            return produced
        block_id = f"tool:{call_id}"
        initial = {
            "name": str(data.get("tool_call_name") or data.get("name") or "Tool"),
            "inputPreview": "",
            "outputPreview": "",
        }
        event = self._append(
            "tool.call.started",
            aggregate_type="tool",
            aggregate_id=call_id,
            data={"blockId": block_id, "parentId": self.message_id, **initial},
        )
        self._active[block_id] = ActiveDomainBlock(block_id, "tool", "tool", call_id)
        produced.append(event)
        return produced

    def _tool_input_delta(self, data: dict[str, Any]) -> list[DomainEvent]:
        call_id = str(data.get("tool_call_id") or "")
        if not call_id or f"tool:{call_id}" not in self._active:
            return []
        return [self._append(
            "tool.call.delta",
            aggregate_type="tool",
            aggregate_id=call_id,
            data={"blockId": f"tool:{call_id}", "delta": str(data.get("delta") or "")},
        )]

    def _tool_output_delta(self, data: dict[str, Any]) -> list[DomainEvent]:
        call_id = str(data.get("tool_call_id") or "")
        if not call_id or f"tool:{call_id}" not in self._active:
            return []
        return [self._append(
            "tool.result.delta",
            aggregate_type="tool",
            aggregate_id=call_id,
            data={"blockId": f"tool:{call_id}", "delta": str(data.get("delta") or "")},
        )]

    def _tool_input_completed(self, data: dict[str, Any]) -> list[DomainEvent]:
        """完整脱敏参数替换流式预览，恢复后和 replay 都不重复追加。"""
        call_id = str(data.get("tool_call_id") or "")
        if not call_id or f"tool:{call_id}" not in self._active:
            return []
        value = data.get("input") if data.get("input") is not None else data.get("arguments")
        if value is None:
            return []
        import json

        preview = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return [self._append(
            "tool.call.updated", aggregate_type="tool", aggregate_id=call_id,
            data={"blockId": f"tool:{call_id}", "inputPreview": preview[:16_000]},
        )]

    def _tool_completed(self, data: dict[str, Any]) -> list[DomainEvent]:
        call_id = str(data.get("tool_call_id") or "")
        block_id = f"tool:{call_id}"
        if not call_id or block_id not in self._active:
            return []
        state = str(data.get("state") or "completed").lower()
        outcome = "failed" if state in {"error", "failed", "denied"} else "completed"
        event = self._append(
            f"tool.result.{outcome}",
            aggregate_type="tool",
            aggregate_id=call_id,
            data={"blockId": block_id, "error": str(data.get("error") or ""),
                  **({"authorization": data["authorization"]} if isinstance(data.get("authorization"), dict) else {})},
        )
        self._active.pop(block_id, None)
        return [event]

    def _context_usage(self, data: dict[str, Any]) -> list[DomainEvent]:
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else data
        if not isinstance(payload, dict) or not payload:
            return []
        block_id = f"context:{self.run_id}"
        stats = {
            key: payload.get(key)
            for key in (
                "systemPromptChars",
                "summaryChars",
                "contextChars",
                "contextMessages",
                "toolResultBlocks",
                "toolCount",
                "contextWindow",
                "triggerRatio",
                "triggerTokens",
                "estimatedTokens",
                "compressed",
                "overTrigger",
                "inputTokens",
                "outputTokens",
                "estimateMethod",
                "estimateErrorPct",
                "promptSections",
                "toolSchemaChars",
                "dataBlocks",
                "compressionCount",
                "stubbedToolResults",
                "droppedImages",
                "hygieneSavedChars",
            )
            if key in payload
        }
        session = _session_resource_payload(payload.get("session"))
        if session:
            stats["session"] = session
        if not stats:
            return []
        if block_id not in self._active:
            return self._start_block("context", block_id, "context", stats)
        return [self._delta_block("context", block_id, "context", stats, event_suffix="updated")]

    def _skill_started(self, data: dict[str, Any]) -> list[DomainEvent]:
        produced: list[DomainEvent] = []
        for raw in data.get("skills") or []:
            item = raw if isinstance(raw, dict) else {"name": str(raw)}
            skill_id = str(item.get("id") or item.get("name") or "skill")
            block_id = f"skill:{skill_id}"
            if block_id not in self._active:
                produced.extend(self._start_block("skill", block_id, "skill", item, aggregate_id=skill_id))
        return produced

    def _skill_completed(self, data: dict[str, Any]) -> list[DomainEvent]:
        produced: list[DomainEvent] = []
        failed = str(data.get("status") or "completed") == "failed"
        for block in list(self._active.values()):
            if block.kind == "skill":
                produced.append(self._close_block("skill", block.aggregate_id, block.block_id, "failed" if failed else "completed", {}))
        return produced

    def _interaction_started(self, data: dict[str, Any]) -> list[DomainEvent]:
        interaction_id = str(
            data.get("interactionId")
            or data.get("interaction_id")
            or data.get("id")
            or ""
        )
        if not interaction_id:
            return []
        block_id = f"approval:{interaction_id}"
        initial = {
            key: data.get(key)
            for key in (
                "interactionId",
                "kind",
                "type",
                "title",
                "description",
                "questions",
                "toolCalls",
                "sessionId",
                "agentId",
                "agentName",
                "runId",
                "replyId",
                "scope",
                "createdAt",
                "version",
                "approvalMode",
                "actorId",
                "decisions",
            )
            if data.get(key) is not None
        }
        initial.setdefault("interactionId", interaction_id)
        initial["status"] = "waiting"
        return self._start_block("interaction", block_id, "approval", initial, aggregate_id=interaction_id)

    def _interaction_completed(self, data: dict[str, Any]) -> list[DomainEvent]:
        interaction_id = str(data.get("interactionId") or "")
        block_id = f"approval:{interaction_id}"
        if not interaction_id or block_id not in self._active:
            return []
        return [self._close_block("interaction", interaction_id, block_id, "completed", {
            "status": "resolved",
            "action": data.get("action"),
            "externalResultsPreview": data.get("externalResultsPreview") or {},
        })]

    def _plan_event(self, runtime_type: str, payload: dict[str, Any]) -> list[DomainEvent]:
        plan_id = str(payload.get("planId") or payload.get("plan_id") or "current")
        block_id = f"plan:{plan_id}"
        produced: list[DomainEvent] = []
        if block_id not in self._active:
            produced.extend(self._start_block("plan", block_id, "plan", payload, aggregate_id=plan_id))
        elif runtime_type not in {"PLAN_COMPLETED", "PLAN_FAILED", "PLAN_CANCELLED"}:
            produced.append(self._delta_block("plan", plan_id, "plan", payload, block_id=block_id, event_suffix="updated"))
        if runtime_type in {"PLAN_COMPLETED", "PLAN_FAILED", "PLAN_CANCELLED"} and block_id in self._active:
            outcome = "failed" if runtime_type == "PLAN_FAILED" else (
                "cancelled" if runtime_type == "PLAN_CANCELLED" else "completed"
            )
            produced.append(self._close_block("plan", plan_id, block_id, outcome, payload))
        return produced

    def _artifact_event(self, runtime_type: str, payload: dict[str, Any]) -> list[DomainEvent]:
        artifact_id = str(payload.get("artifactId") or payload.get("path") or "artifact")
        block_id = f"artifact:{artifact_id}"
        if block_id not in self._active:
            return self._start_block("artifact", block_id, "artifact", payload, aggregate_id=artifact_id)
        return [self._delta_block("artifact", artifact_id, "artifact", payload, block_id=block_id, event_suffix="updated")]

    def _subagent_event(self, runtime_type: str, payload: dict[str, Any]) -> list[DomainEvent]:
        invocation_id = str(payload.get("invocationId") or payload.get("toolCallId") or "subagent")
        block_id = f"subagent:{invocation_id}"
        produced: list[DomainEvent] = []
        if block_id not in self._active and runtime_type != "SUBAGENT_CONSUMED":
            produced.extend(self._start_block("subagent", block_id, "subagent", payload, aggregate_id=invocation_id))
        elif block_id in self._active and runtime_type != "SUBAGENT_CONSUMED":
            produced.append(self._delta_block("subagent", invocation_id, "subagent", payload, block_id=block_id, event_suffix="updated"))
        if block_id in self._active and runtime_type == "SUBAGENT_CONSUMED":
            task_status = str(payload.get("taskStatus") or payload.get("status") or "completed")
            outcome = "failed" if task_status in {"failed", "interrupted"} else (
                "cancelled" if task_status == "cancelled" else "completed"
            )
            produced.append(self._close_block(
                "subagent",
                invocation_id,
                block_id,
                outcome,
                payload,
            ))
        return produced

    def _team_event(self, data: dict[str, Any]) -> list[DomainEvent]:
        return super()._team_event(data)

    def _revive_team_task(
        self,
        team_id: str,
        task_id: str,
        task: dict[str, Any],
        team_block_id: str,
    ) -> list[DomainEvent]:
        """重派后重新打开已关闭的 Task Block，并允许后续 output / tool 再投影。"""

        if not task_id:
            return []
        task_block_id = self._team_task_block_id(team_id, task_id)
        self._terminal_team_tasks.discard(task_block_id)
        self._pending_team_task_terminals.pop(task_block_id, None)
        if task_block_id in self._active:
            return [self._delta_block(
                "team.task", task_id, "team-task", task,
                block_id=task_block_id, event_suffix="updated",
            )]
        return self._start_block(
            "team.task",
            task_block_id,
            "team-task",
            {"teamRunId": team_id, **task},
            aggregate_id=task_id,
            parent_id=team_block_id,
        )

    def _start_team_member(
        self,
        team_id: str,
        member_id: str,
        member: dict[str, Any],
        team_block_id: str,
    ) -> list[DomainEvent]:
        if not member_id:
            return []
        block_id = self._team_member_block_id(team_id, member_id)
        if block_id in self._active:
            return []
        return self._start_block(
            "team.member",
            block_id,
            "team-member",
            {"teamRunId": team_id, "memberId": member_id, **member},
            aggregate_id=member_id,
            parent_id=team_block_id,
        )

    def _team_tool_event(
        self,
        *,
        source_type: str,
        team_id: str,
        team_block_id: str,
        actor_id: str,
        task_id: str,
        payload: dict[str, Any],
    ) -> list[DomainEvent]:
        tool_call_id = str(payload.get("toolCallId") or "")
        if not tool_call_id:
            return []
        block_id = f"tool:team:{team_id}:{tool_call_id}"
        if block_id in self._terminal_team_tools:
            return []
        parent_id = self._team_task_block_id(team_id, task_id) if task_id else team_block_id
        if parent_id not in self._active:
            parent_id = team_block_id
        produced: list[DomainEvent] = []
        if block_id not in self._active:
            event = self._append(
                "tool.call.started",
                aggregate_type="tool",
                aggregate_id=f"team:{team_id}:{tool_call_id}",
                data={
                    "blockId": block_id,
                    "parentId": parent_id,
                    "name": str(payload.get("name") or "Tool"),
                    "inputPreview": "",
                    "outputPreview": "",
                    "scope": "team",
                    "memberId": actor_id,
                    "taskId": task_id,
                },
            )
            self._active[block_id] = ActiveDomainBlock(
                block_id, "tool", "tool", f"team:{team_id}:{tool_call_id}",
            )
            self._block_parents[block_id] = parent_id
            produced.append(event)
        if source_type == "member.tool.input" and block_id in self._active:
            produced.append(self._append(
                "tool.call.updated", aggregate_type="tool", aggregate_id=f"team:{team_id}:{tool_call_id}",
                data={"blockId": block_id, "inputPreview": str(payload.get("input") or "")[:16_000]},
            ))
            return produced
        if source_type != "member.tool.started" and block_id in self._active:
            failed = source_type in {"member.tool.failed", "member.tool.denied"}
            event = self._append(
                "tool.result.failed" if failed else "tool.result.completed",
                aggregate_type="tool",
                aggregate_id=f"team:{team_id}:{tool_call_id}",
                data={
                    "blockId": block_id,
                    "status": "failed" if failed else "completed",
                    "error": str(payload.get("reason") or payload.get("state") or "") if failed else "",
                },
            )
            self._active.pop(block_id, None)
            self._block_parents.pop(block_id, None)
            self._terminal_team_tools.add(block_id)
            produced.append(event)
        return produced

    def _close_team_tree(self, team_block_id: str, outcome: str) -> list[DomainEvent]:
        produced = self._close_team_descendants(team_block_id, outcome)
        self._pending_team_task_terminals.clear()
        self._terminal_team_tasks.clear()
        self._terminal_team_tools.clear()
        block = self._active.get(team_block_id)
        if block is not None:
            produced.append(self._close_block(
                "team", block.aggregate_id, team_block_id, outcome, {},
            ))
        return produced

    def _close_team_descendants(
        self,
        parent_id: str,
        outcome: str,
        *,
        exclude_kinds: set[str] | None = None,
    ) -> list[DomainEvent]:
        excluded = exclude_kinds or set()
        descendants = [
            block
            for block in self._active.values()
            if (
                block.block_id != parent_id
                and block.kind not in excluded
                and self._is_descendant(block.block_id, parent_id)
            )
        ]
        descendants.sort(key=lambda block: self._block_depth(block.block_id), reverse=True)
        produced: list[DomainEvent] = []
        for block in descendants:
            if block.block_id not in self._active:
                continue
            prefix = "tool.result" if block.kind == "tool" else block.aggregate_type
            produced.append(self._close_block(
                prefix,
                block.aggregate_id,
                block.block_id,
                outcome,
                {"status": "completed" if outcome == "completed" else outcome},
            ))
        return produced

    def _close_pending_team_task(self, task_id: str, team_id: str) -> list[DomainEvent]:
        """最后一个工具关闭后，提交此前已到达的 Task 终态。"""

        if not task_id:
            return []
        task_block_id = self._team_task_block_id(team_id, task_id)
        pending = self._pending_team_task_terminals.get(task_block_id)
        if pending is None or task_block_id not in self._active:
            return []
        if self._open_team_descendants(task_block_id):
            return []
        aggregate_id, outcome, patch = self._pending_team_task_terminals.pop(task_block_id)
        return [self._close_block(
            "team.task",
            aggregate_id,
            task_block_id,
            outcome,
            patch,
        )]

    def _open_team_descendants(
        self,
        parent_id: str,
        *,
        kind: str | None = None,
    ) -> list[ActiveDomainBlock]:
        return [
            block
            for block in self._active.values()
            if (
                block.block_id != parent_id
                and (kind is None or block.kind == kind)
                and self._is_descendant(block.block_id, parent_id)
            )
        ]

    def _is_descendant(self, block_id: str, parent_id: str) -> bool:
        current = self._block_parents.get(block_id, "")
        while current:
            if current == parent_id:
                return True
            current = self._block_parents.get(current, "")
        return False

    def _block_depth(self, block_id: str) -> int:
        depth = 0
        current = self._block_parents.get(block_id, "")
        while current:
            depth += 1
            current = self._block_parents.get(current, "")
        return depth

    @staticmethod
    def _team_member_block_id(team_id: str, member_id: str) -> str:
        return f"team-member:{team_id}:{member_id}"

    @staticmethod
    def _team_task_block_id(team_id: str, task_id: str) -> str:
        return f"team-task:{team_id}:{task_id}"

    def _error(self, data: dict[str, Any]) -> list[DomainEvent]:
        error_id = f"error:{self.run_id}"
        if error_id in self._active:
            return [self._delta_block("error", error_id, "error", data, block_id=error_id, event_suffix="updated")]
        return self._start_block("error", error_id, "error", {
            "message": str(data.get("message") or data.get("error") or "运行失败"),
        }, aggregate_id=error_id)

    def _text_block_id(self, generation: Any) -> str:
        """正整数 generation 单独成块；缺省时保持历史的单块 id。"""

        if isinstance(generation, bool):
            return f"text:{self.run_id}"
        if isinstance(generation, int) and generation > 0:
            return f"text:{self.run_id}:{generation}"
        if isinstance(generation, float) and generation.is_integer() and generation > 0:
            return f"text:{self.run_id}:{int(generation)}"
        if isinstance(generation, str) and generation.isdigit() and int(generation) > 0:
            return f"text:{self.run_id}:{int(generation)}"
        return f"text:{self.run_id}"

    def _ensure_text(self, block_id: str | None = None) -> list[DomainEvent]:
        target = block_id or f"text:{self.run_id}"
        self._text_id = target
        if target in self._active:
            return []
        # 上一句已经说完。只让当前这块继续闪烁，避免每一句末尾都留着光标。
        produced = self._close_other_text_blocks(target)
        produced.extend(self._start_block("agent.output", target, "text", "", aggregate_id=target))
        return produced

    def _close_other_text_blocks(self, keep: str) -> list[DomainEvent]:
        produced: list[DomainEvent] = []
        for block in list(self._active.values()):
            if block.kind == "text" and block.block_id != keep:
                produced.append(self._close_block(
                    "agent.output",
                    block.aggregate_id,
                    block.block_id,
                    "completed",
                    None,
                ))
        return produced

    def _mark_answer_ready(self, block_id: str) -> list[DomainEvent]:
        """把终态正文的 Block id 写到仍打开的消息上。消息还没开始时不写。"""

        if not self._started:
            return []
        return [self._append(
            "message.answer.ready",
            aggregate_type="message",
            aggregate_id=self.message_id,
            data={"blockId": self.message_id, "answerGeneration": block_id},
        )]

    def _close_process_text(self) -> list[DomainEvent]:
        produced: list[DomainEvent] = []
        for kind, block_id, prefix in (
            ("reasoning", self._reasoning_id, "reasoning"),
            ("thinking", self._thinking_id, "thinking"),
        ):
            if block_id and block_id in self._active:
                produced.append(self._close_block(prefix, block_id, block_id, "completed", None))
            if kind == "reasoning":
                self._reasoning_id = ""
            else:
                self._thinking_id = ""
        return produced

    def _start_block(
        self,
        prefix: str,
        block_id: str,
        kind: str,
        initial: Any,
        *,
        aggregate_id: str | None = None,
        parent_id: str | None = None,
    ) -> list[DomainEvent]:
        aggregate = aggregate_id or block_id
        event = self._append(
            f"{prefix}.started",
            aggregate_type=prefix,
            aggregate_id=aggregate,
            data={"blockId": block_id, "parentId": parent_id or self.message_id, "initial": initial},
        )
        self._active[block_id] = ActiveDomainBlock(block_id, kind, prefix, aggregate)
        self._block_parents[block_id] = parent_id or self.message_id
        return [event]

    def _delta_block(
        self,
        prefix: str,
        aggregate_id: str,
        kind: str,
        patch: Any,
        *,
        block_id: str | None = None,
        event_suffix: str = "delta",
    ) -> DomainEvent:
        return self._append(
            f"{prefix}.{event_suffix}",
            aggregate_type=prefix,
            aggregate_id=aggregate_id,
            data={"blockId": block_id or aggregate_id, "patch": patch},
        )

    def _close_block(
        self,
        prefix: str,
        aggregate_id: str,
        block_id: str,
        outcome: str,
        patch: Any,
    ) -> DomainEvent:
        suffix = outcome if outcome in {"failed", "cancelled"} else "completed"
        event = self._append(
            f"{prefix}.{suffix}",
            aggregate_type=prefix,
            aggregate_id=aggregate_id,
            data={"blockId": block_id, "patch": patch},
        )
        self._active.pop(block_id, None)
        self._block_parents.pop(block_id, None)
        return event

    def _append(
        self,
        event_type: str,
        *,
        aggregate_type: str,
        aggregate_id: str,
        data: dict[str, Any],
    ) -> DomainEvent:
        persisted_aggregate_id = f"{self.run_id}:{aggregate_id}"
        key = (aggregate_type, persisted_aggregate_id)
        self._revisions[key] += 1
        return self.events.append(
            self.stream_key,
            self.run_id,
            DomainEventDraft(
                type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=persisted_aggregate_id,
                revision=self._revisions[key],
                data=data,
            ),
        )
