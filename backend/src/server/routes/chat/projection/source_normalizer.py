"""把进程内 AgentScope/领域信号直接归一化为持久 Domain Event。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .completion_barrier import ActiveDomainBlock, DomainCompletionBarrier
from .domain_events import DomainEvent, DomainEventDraft, DomainEventRepository


class ChatProjectionNormalizer:
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
            "TOOL_CALL_START": self._tool_started,
            "TOOL_CALL_DELTA": self._tool_input_delta,
            "TOOL_RESULT_TEXT_DELTA": self._tool_output_delta,
            "TOOL_RESULT_END": self._tool_completed,
            "SKILL_CONTEXT": self._skill_started,
            "SKILL_END": self._skill_completed,
            "HITL_REQUIRED": self._interaction_started,
            "CLARIFICATION_REQUIRED": self._interaction_started,
            "TEAM_DOMAIN_EVENT": self._team_updated,
            "ERROR": self._error,
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
        produced = self._close_process_text()
        produced.extend(self._ensure_text())
        produced.append(self._delta_block("agent.output", self._text_id, "text", delta))
        return produced

    def _answer_replace(self, data: dict[str, Any]) -> list[DomainEvent]:
        produced = self._close_process_text()
        produced.extend(self._ensure_text())
        produced.append(self._delta_block(
            "agent.output",
            self._text_id,
            "text",
            {"replace": str(data.get("content") or "")},
        ))
        return produced

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
            data={"blockId": block_id, "error": str(data.get("error") or "")},
        )
        self._active.pop(block_id, None)
        return [event]

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
        elif runtime_type not in {"PLAN_FINALIZED"}:
            produced.append(self._delta_block("plan", plan_id, "plan", payload, block_id=block_id, event_suffix="updated"))
        if runtime_type == "PLAN_FINALIZED" and block_id in self._active:
            produced.append(self._close_block("plan", plan_id, block_id, "completed", payload))
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
        if block_id not in self._active and runtime_type not in {"SUBAGENT_COMPLETED", "SUBAGENT_FAILED"}:
            produced.extend(self._start_block("subagent", block_id, "subagent", payload, aggregate_id=invocation_id))
        elif block_id in self._active and runtime_type not in {"SUBAGENT_COMPLETED", "SUBAGENT_FAILED"}:
            produced.append(self._delta_block("subagent", invocation_id, "subagent", payload, block_id=block_id, event_suffix="updated"))
        if block_id in self._active and runtime_type in {"SUBAGENT_COMPLETED", "SUBAGENT_FAILED"}:
            produced.append(self._close_block(
                "subagent",
                invocation_id,
                block_id,
                "failed" if runtime_type.endswith("FAILED") else "completed",
                payload,
            ))
        return produced

    def _team_updated(self, data: dict[str, Any]) -> list[DomainEvent]:
        team_id = str(data.get("teamRunId") or self.run_id)
        block_id = f"team:{team_id}"
        snapshot = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else data
        if block_id not in self._active:
            return self._start_block("team", block_id, "team", snapshot, aggregate_id=team_id)
        return [self._delta_block("team", team_id, "team", snapshot, block_id=block_id, event_suffix="updated")]

    def _error(self, data: dict[str, Any]) -> list[DomainEvent]:
        error_id = f"error:{self.run_id}"
        if error_id in self._active:
            return [self._delta_block("error", error_id, "error", data, block_id=error_id, event_suffix="updated")]
        return self._start_block("error", error_id, "error", {
            "message": str(data.get("message") or data.get("error") or "运行失败"),
        }, aggregate_id=error_id)

    def _ensure_text(self) -> list[DomainEvent]:
        if self._text_id in self._active:
            return []
        return self._start_block("agent.output", self._text_id, "text", "", aggregate_id=self._text_id)

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
    ) -> list[DomainEvent]:
        aggregate = aggregate_id or block_id
        event = self._append(
            f"{prefix}.started",
            aggregate_type=prefix,
            aggregate_id=aggregate,
            data={"blockId": block_id, "parentId": self.message_id, "initial": initial},
        )
        self._active[block_id] = ActiveDomainBlock(block_id, kind, prefix, aggregate)
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
