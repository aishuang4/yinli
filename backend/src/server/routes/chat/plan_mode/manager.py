"""Plan Mode 状态协调器。"""

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from src.server.routes.chat.plan_mode.models import (
    PLAN_STATUS_DRAFT,
    PLAN_STATUS_FINALIZED,
    PLAN_STEP_STATUS_COMPLETED,
    PLAN_STEP_STATUS_FAILED,
    PLAN_STEP_STATUS_PENDING,
    PLAN_STEP_STATUS_RUNNING,
    PLAN_STEP_STATUS_SKIPPED,
    PLAN_STEP_STATUSES,
    PlanModeContext,
)
from src.server.routes.chat.plan_mode.events import (
    PlanDomainEvent,
    build_plan_domain_event,
    plan_domain_event_to_runtime_event,
)


class PlanModeManager:
    """管理一次 Chat run 的 Plan Mode 状态和持久化。"""

    def __init__(
        self,
        *,
        session_id: str,
        run_id: str,
        agent_id: str,
        agent_name: str,
        persist: bool = True,
    ) -> None:
        self.persist = persist
        self.domain_events: list[PlanDomainEvent] = []
        self.context = PlanModeContext(
            session_id=session_id,
            run_id=run_id,
            agent_id=agent_id,
            agent_name=agent_name,
        )

    def is_plan_active(self) -> bool:
        """返回当前是否处于只读计划阶段。"""

        return self.context.plan_active

    def plan_file_path(self) -> str:
        """返回当前计划文件路径。"""

        if self.context.current_plan_file:
            return self.context.current_plan_file
        return self._make_plan_file_path(self.context.plan_id or uuid.uuid4().hex)

    def drain_runtime_events(self) -> list[dict]:
        """取出已生成的 Plan Mode Runtime Event。"""

        events = [
            plan_domain_event_to_runtime_event(event, context=self.context)
            for event in self.domain_events
        ]
        self.domain_events.clear()
        return events

    def enter(self, *, goal: str, title: str = "Runtime Plan") -> PlanModeContext:
        """进入 Plan Mode 并创建 plan 记录。"""

        already_created = bool(self.context.plan_id)
        plan_id = self.context.plan_id or f"plan-{uuid.uuid4().hex}"
        plan_file = self._make_plan_file_path(plan_id)
        markdown = self.context.markdown or f"# {title}\n\n## Goal\n{goal}\n"
        self.context.plan_id = plan_id
        self.context.plan_active = True
        self.context.current_plan_file = plan_file
        self.context.markdown = markdown
        self._append_runtime_event(
            "PLAN_MODE_ENTERED",
            status="running",
            payload={"mode": "plan"},
        )
        if self.persist and not already_created:
            self._create_plan_record(title=title, goal=goal, markdown=markdown)
        return self.context

    def write(self, *, markdown: str, source: str) -> PlanModeContext:
        """更新计划 markdown。"""

        self.context.markdown = markdown
        self._append_runtime_event(
            "PLAN_UPDATED",
            status="running",
            payload={"markdown": markdown, "source": source},
        )
        if self.persist and self.context.plan_id:
            from src.storage.database import append_plan_event_record, update_plan_record

            update_plan_record(self.context.plan_id, markdown=markdown)
            append_plan_event_record(
                self.context.plan_id,
                step_id=None,
                event_type="PLAN_UPDATED",
                content=f"source={source}",
            )
        return self.context

    def write_tasks(self, *, items: list[dict[str, Any]]) -> PlanModeContext:
        """写入结构化计划步骤。"""

        self._ensure_plan_started()
        steps = self._normalize_steps(items)
        self.context.steps = steps
        if self.persist and self.context.plan_id:
            from src.storage.database import append_plan_event_record, replace_plan_steps_record

            self.context.steps = replace_plan_steps_record(self.context.plan_id, steps)
            append_plan_event_record(
                self.context.plan_id,
                step_id=None,
                event_type="PLAN_TASKS_UPDATED",
                content=f"steps={len(self.context.steps)}",
            )
        self._append_runtime_event(
            "PLAN_TASKS_UPDATED",
            status="running",
            payload={"steps": self.context.steps},
        )
        return self.context

    def list_tasks(self) -> list[dict]:
        """返回当前结构化计划步骤。"""

        if self.persist and self.context.plan_id:
            from src.storage.database import get_plan_record

            plan = get_plan_record(self.context.plan_id)
            if plan:
                self.context.steps = list(plan.get("steps") or [])
        return list(self.context.steps or [])

    def record_task_output(
        self,
        *,
        step_id: str | None = None,
        step_index: int | None = None,
        status: str = PLAN_STEP_STATUS_RUNNING,
        evidence: str = "",
        result: str = "",
    ) -> PlanModeContext:
        """记录某个结构化步骤的执行输出。"""

        self._ensure_plan_started()
        normalized_status = self._normalize_step_status(status)
        steps = self.list_tasks()
        step = self._find_step(steps, step_id=step_id, step_index=step_index)
        now = datetime.now(timezone.utc).isoformat()
        next_step = {
            **step,
            "status": normalized_status,
            "evidence": evidence if evidence else str(step.get("evidence") or ""),
            "result": result if result else str(step.get("result") or ""),
            "startedAt": step.get("startedAt") or (now if normalized_status == PLAN_STEP_STATUS_RUNNING else None),
            "completedAt": now if normalized_status in {
                PLAN_STEP_STATUS_COMPLETED,
                PLAN_STEP_STATUS_FAILED,
                PLAN_STEP_STATUS_SKIPPED,
            } else step.get("completedAt"),
            "updatedAt": now,
        }
        self.context.steps = [
            next_step if str(item.get("id") or "") == str(next_step.get("id") or "") else item
            for item in steps
        ]
        if self.persist and self.context.plan_id:
            from src.storage.database import append_plan_event_record, update_plan_record, update_plan_step_record

            updated_step = update_plan_step_record(
                str(next_step["id"]),
                status=normalized_status,
                evidence=str(next_step.get("evidence") or ""),
                result=str(next_step.get("result") or ""),
                started_at=next_step.get("startedAt"),
                completed_at=next_step.get("completedAt"),
            )
            if updated_step:
                next_step = updated_step
                self.context.steps = [
                    next_step if str(item.get("id") or "") == str(next_step.get("id") or "") else item
                    for item in self.context.steps
                ]
            update_plan_record(self.context.plan_id, current_step_id=str(next_step["id"]))
            append_plan_event_record(
                self.context.plan_id,
                step_id=str(next_step["id"]),
                event_type="PLAN_TASK_OUTPUT",
                content=f"status={normalized_status}",
            )
        self._append_runtime_event(
            "PLAN_TASK_OUTPUT",
            status=self._runtime_status_for_step(normalized_status),
            payload={"step": next_step, "steps": self.context.steps},
        )
        return self.context

    def finalize(self) -> PlanModeContext:
        """退出只读计划阶段并将计划作为本轮最终答复。"""

        self.context.plan_active = False
        self._append_runtime_event(
            "PLAN_FINALIZED",
            status="completed",
            payload={"markdown": self.context.markdown},
        )
        if self.persist and self.context.plan_id:
            from src.storage.database import append_plan_event_record, update_plan_record

            update_plan_record(self.context.plan_id, status=PLAN_STATUS_FINALIZED)
            append_plan_event_record(
                self.context.plan_id,
                step_id=None,
                event_type="PLAN_FINALIZED",
                content="Plan mode exited and returned the plan as final answer.",
            )
        return self.context

    def record_tool_denied(self, *, tool_call_id: str, tool_name: str, message: str) -> None:
        """记录 Plan Mode 拒绝变更型工具调用。"""

        self._append_runtime_event(
            "PLAN_TOOL_DENIED",
            status="completed",
            payload={
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "message": message,
            },
        )

    def _create_plan_record(self, *, title: str, goal: str, markdown: str) -> None:
        from src.storage.database import create_plan_record

        now = datetime.now(timezone.utc).isoformat()
        create_plan_record(
            {
                "id": self.context.plan_id,
                "runId": self.context.run_id,
                "sessionId": self.context.session_id,
                "agentId": self.context.agent_id,
                "title": title,
                "goal": goal,
                "status": PLAN_STATUS_DRAFT,
                "markdown": markdown,
                "currentStepId": None,
                "createdAt": now,
                "updatedAt": now,
                "completedAt": None,
            },
            [],
        )

    def _ensure_plan_started(self) -> None:
        if not self.context.plan_id:
            raise ValueError("Plan Mode task tools require plan_enter before use.")

    def _normalize_steps(self, items: list[dict[str, Any]]) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        steps: list[dict] = []
        for index, item in enumerate(items):
            title = str(item.get("title") or "").strip()
            if not title:
                raise ValueError("Plan step title is required.")
            status = self._normalize_step_status(str(item.get("status") or PLAN_STEP_STATUS_PENDING))
            steps.append({
                "id": str(item.get("id") or f"{self.context.plan_id}-step-{index + 1}"),
                "planId": self.context.plan_id,
                "stepIndex": index,
                "title": title,
                "description": str(item.get("description") or ""),
                "status": status,
                "assignedAgentId": item.get("assignedAgentId") or item.get("assigned_agent_id"),
                "evidence": str(item.get("evidence") or ""),
                "result": str(item.get("result") or ""),
                "startedAt": item.get("startedAt"),
                "completedAt": item.get("completedAt"),
                "updatedAt": str(item.get("updatedAt") or now),
            })
        return steps

    def _normalize_step_status(self, status: str) -> str:
        value = str(status or PLAN_STEP_STATUS_PENDING).strip().lower()
        if value not in PLAN_STEP_STATUSES:
            allowed = ", ".join(sorted(PLAN_STEP_STATUSES))
            raise ValueError(f"Invalid plan step status: {status}. Allowed: {allowed}.")
        return value

    def _find_step(
        self,
        steps: list[dict],
        *,
        step_id: str | None,
        step_index: int | None,
    ) -> dict:
        if step_id:
            match = next((item for item in steps if str(item.get("id") or "") == step_id), None)
            if match:
                return match
        if step_index is not None:
            match = next((item for item in steps if int(item.get("stepIndex") or 0) == step_index), None)
            if match:
                return match
        raise ValueError("Plan step was not found.")

    def _runtime_status_for_step(self, status: str) -> str:
        if status == PLAN_STEP_STATUS_FAILED:
            return "failed"
        if status in {PLAN_STEP_STATUS_COMPLETED, PLAN_STEP_STATUS_SKIPPED}:
            return "completed"
        return "running"

    def _make_plan_file_path(self, plan_id: str) -> str:
        safe_session = self.context.session_id.replace("/", "-")
        return os.path.join(".agent-workstation", "plans", safe_session, f"{plan_id}.md")

    def _append_runtime_event(
        self,
        event_type: str,
        *,
        status: str,
        payload: dict,
    ) -> None:
        self.domain_events.append(build_plan_domain_event(
            event_type,
            status=status,
            payload=payload,
        ))
