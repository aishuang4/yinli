"""可跨 Chat Run 延续的 Plan 生命周期协调器。"""

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from src.server.routes.chat.plan_mode.events import PlanDomainEvent, build_plan_domain_event, plan_domain_event_to_runtime_event
from src.server.routes.chat.plan_mode.models import PLAN_ACTIVE_STATUSES, PLAN_STATUS_APPROVED, PLAN_STATUS_CANCELLED, PLAN_STATUS_COMPLETED, PLAN_STATUS_DRAFTING, PLAN_STATUS_EXECUTING, PLAN_STATUS_FAILED, PLAN_STATUS_REPLANNING, PLAN_STATUS_REVIEW_WAITING, PLAN_STATUS_REVISING, PLAN_STEP_STATUS_COMPLETED, PLAN_STEP_STATUS_FAILED, PLAN_STEP_STATUS_PENDING, PLAN_STEP_STATUS_RUNNING, PLAN_STEP_STATUS_SKIPPED, PLAN_STEP_STATUSES, PlanModeContext


class PlanModeManager:
    """管理持久化 Plan 会话、revision、审核与执行进度。"""

    def __init__(
        self,
        *,
        session_id: str,
        run_id: str,
        agent_id: str,
        agent_name: str,
        persist: bool = True,
        plan_id: str = "",
        action: str = "",
        feedback: str = "",
        requested: bool = False,
    ) -> None:
        self.persist = persist
        self.action = action
        self.domain_events: list[PlanDomainEvent] = []
        self.context = PlanModeContext(
            session_id=session_id,
            run_id=run_id,
            agent_id=agent_id,
            agent_name=agent_name,
            feedback=feedback.strip(),
        )
        if requested and not action:
            self.context.plan_active = True
        if plan_id:
            self._load(plan_id)
            self.context.run_id = run_id
            self.context.feedback = feedback.strip()
        if action:
            self._prepare_action(action)

    def is_plan_active(self) -> bool:
        """返回当前是否处于只读规划阶段。"""

        return self.context.plan_active

    def is_cancelled_action(self) -> bool:
        """返回本轮是否只执行取消命令。"""

        return self.action == "cancel"

    def is_execution_active(self) -> bool:
        """返回是否正在执行已审核计划。"""

        return self.context.status == PLAN_STATUS_EXECUTING

    def run_completion_error(self) -> str:
        """校验当前 ReAct Run 是否通过 Plan 生命周期收口。"""

        if self.context.plan_active or self.context.status in {
            PLAN_STATUS_DRAFTING,
            PLAN_STATUS_REVISING,
            PLAN_STATUS_REPLANNING,
        }:
            return "Plan Mode 未调用 plan_request_review，不能结束当前规划 Run。"
        if self.context.status == PLAN_STATUS_EXECUTING:
            return "计划执行尚未调用 plan_complete，不能结束当前执行 Run。"
        return ""

    def fail_run(self, *, reason: str) -> PlanModeContext:
        """在 Run 异常或未按协议收口时恢复为可操作状态。"""

        if not self.context.plan_id:
            return self.context
        failure_reason = reason.strip() or "Plan Run failed."
        if self.context.status in {PLAN_STATUS_REVISING, PLAN_STATUS_REPLANNING}:
            previous_status = self.context.status
            if not self._transition_status(
                expected_status=previous_status,
                status=PLAN_STATUS_REVIEW_WAITING,
            ):
                self._load(self.context.plan_id)
                return self.context
            self.context.status = PLAN_STATUS_REVIEW_WAITING
            self.context.plan_active = False
            self._record("PLAN_REVIEW_REQUESTED", f"attempt_failed={failure_reason}")
            self._append_runtime_event(
                "PLAN_REVIEW_REQUESTED",
                status="waiting",
                extra={"attemptFailed": True, "failureReason": failure_reason},
            )
            return self.context
        if self.context.status in {PLAN_STATUS_DRAFTING, PLAN_STATUS_APPROVED, PLAN_STATUS_EXECUTING}:
            previous_status = self.context.status
            now = datetime.now(timezone.utc).isoformat()
            if not self._transition_status(
                expected_status=previous_status,
                status=PLAN_STATUS_FAILED,
                completed_at=now,
            ):
                self._load(self.context.plan_id)
                return self.context
            self.context.status = PLAN_STATUS_FAILED
            self.context.plan_active = False
            self._record("PLAN_FAILED", failure_reason)
            self._append_runtime_event(
                "PLAN_FAILED",
                status="failed",
                reason=failure_reason,
                extra={"failureReason": failure_reason},
            )
        return self.context

    def plan_file_path(self) -> str:
        """返回当前计划的逻辑文件路径。"""

        if self.context.current_plan_file:
            return self.context.current_plan_file
        return self._make_plan_file_path(self.context.plan_id or f"draft-{self.context.run_id}")

    def prompt_context(self) -> str:
        """构造 revision 或执行 continuation 所需的完整 Plan 上下文。"""

        if not self.context.plan_id:
            return ""
        steps = "\n".join(
            f"{index + 1}. [{step.get('status', 'pending')}] {step.get('title', '')}: {step.get('description', '')}"
            for index, step in enumerate(self.context.steps)
        ) or "(no structured steps yet)"
        feedback = self.context.feedback or "(none)"
        return (
            "\n\n# Active Plan Session\n"
            f"Plan ID: {self.context.plan_id}\n"
            f"Status: {self.context.status}\n"
            f"Current revision: {self.context.revision}\n"
            f"User feedback: {feedback}\n\n"
            "## Current plan markdown\n"
            f"{self.context.markdown}\n\n"
            "## Current structured steps\n"
            f"{steps}\n"
        )

    def drain_runtime_events(self) -> list[dict]:
        """取出已生成的 Plan Runtime Event。"""

        events = [
            plan_domain_event_to_runtime_event(event, context=self.context)
            for event in self.domain_events
        ]
        self.domain_events.clear()
        return events

    def enter(self, *, goal: str, title: str = "Runtime Plan") -> PlanModeContext:
        """创建新的持久化 Plan 会话并进入只读起草阶段。"""

        if self.context.plan_id:
            raise ValueError("A plan session is already active for this run.")
        plan_id = f"plan-{uuid.uuid4().hex}"
        self.context.plan_id = plan_id
        self.context.plan_active = True
        self.context.current_plan_file = self._make_plan_file_path(plan_id)
        self.context.title = title.strip() or "Runtime Plan"
        self.context.goal = goal.strip()
        self.context.status = PLAN_STATUS_DRAFTING
        self.context.markdown = f"# {self.context.title}\n\n## Goal\n{self.context.goal}\n"
        if self.persist:
            self._create_plan_record()
        self._append_runtime_event("PLAN_CREATED", status="running")
        return self.context

    def write(self, *, markdown: str, source: str) -> PlanModeContext:
        """替换当前未提交 revision 的完整 Markdown。"""

        self._require_planning()
        value = markdown.strip()
        if not value:
            raise ValueError("Plan markdown cannot be empty.")
        self.context.markdown = value
        self._persist_plan(markdown=value)
        self._record("PLAN_CONTENT_REPLACED", f"source={source}")
        self._append_runtime_event("PLAN_CONTENT_REPLACED", status="running")
        return self.context

    def write_tasks(self, *, items: list[dict[str, Any]]) -> PlanModeContext:
        """替换当前未提交 revision 的全部结构化步骤。"""

        self._require_planning()
        steps = self._normalize_steps(items)
        if not steps:
            raise ValueError("A reviewable plan requires at least one structured step.")
        self.context.steps = steps
        if self.persist:
            from src.storage.records.plan_records import replace_plan_steps_record

            self.context.steps = replace_plan_steps_record(self.context.plan_id, steps)
        self._record("PLAN_STEPS_REPLACED", f"steps={len(self.context.steps)}")
        self._append_runtime_event("PLAN_STEPS_REPLACED", status="running")
        return self.context

    def request_review(self) -> PlanModeContext:
        """保存完整 revision 并进入等待用户审核状态。"""

        self._require_planning()
        if not self.context.markdown.strip() or not self.context.steps:
            raise ValueError("Call plan_write and plan_set_steps before requesting review.")
        previous_status = self.context.status
        next_revision = self.context.revision + 1
        if self.persist:
            from src.storage.records.plan_records import publish_plan_revision_record

            published = publish_plan_revision_record(
                self.context.plan_id,
                expected_status=previous_status,
                revision=next_revision,
                markdown=self.context.markdown,
                steps=self.context.steps,
                feedback=self.context.feedback,
            )
            if published is None:
                raise ValueError("Plan state changed before the revision could be published.")
        self.context.revision = next_revision
        self.context.status = PLAN_STATUS_REVIEW_WAITING
        self.context.plan_active = False
        self._record("PLAN_REVIEW_REQUESTED", f"revision={self.context.revision}")
        self._append_runtime_event("PLAN_REVIEW_REQUESTED", status="waiting")
        return self.context

    def request_replan(self, *, reason: str) -> PlanModeContext:
        """执行中因新事实进入只读重新规划阶段。"""

        if self.context.status != PLAN_STATUS_EXECUTING:
            raise ValueError("Replanning is only available while executing an approved plan.")
        feedback = reason.strip()
        if not self._transition_status(
            expected_status=PLAN_STATUS_EXECUTING,
            status=PLAN_STATUS_REPLANNING,
            feedback=feedback,
        ):
            raise ValueError("Plan state changed before replanning could start.")
        self.context.status = PLAN_STATUS_REPLANNING
        self.context.plan_active = True
        self.context.feedback = feedback
        self._record("PLAN_REPLAN_REQUESTED", self.context.feedback)
        self._append_runtime_event("PLAN_REPLAN_REQUESTED", status="running")
        return self.context

    def list_tasks(self) -> list[dict]:
        """返回当前持久化步骤。"""

        if self.persist and self.context.plan_id:
            from src.storage.records.plan_records import get_plan_record

            plan = get_plan_record(self.context.plan_id)
            if plan:
                self.context.steps = list(plan.get("steps") or [])
        return list(self.context.steps)

    def record_task_output(
        self,
        *,
        step_id: str | None = None,
        step_index: int | None = None,
        status: str = PLAN_STEP_STATUS_RUNNING,
        evidence: str = "",
        result: str = "",
    ) -> PlanModeContext:
        """实时更新已审核计划中的一个执行步骤。"""

        if self.context.status != PLAN_STATUS_EXECUTING:
            raise ValueError("task_output is only available while executing an approved plan.")
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
        if self.persist:
            from src.storage.records.plan_records import update_plan_record, update_plan_step_record

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
        self._record("PLAN_STEP_UPDATED", f"step={next_step['id']};status={normalized_status}", step_id=str(next_step["id"]))
        self._append_runtime_event(
            "PLAN_STEP_UPDATED",
            status=self._runtime_status_for_step(normalized_status),
            extra={"step": next_step},
        )
        return self.context

    def complete_execution(self, *, summary: str = "") -> PlanModeContext:
        """在所有步骤终止后收口 Plan 执行。"""

        if self.context.status != PLAN_STATUS_EXECUTING:
            raise ValueError("plan_complete is only available while executing an approved plan.")
        steps = self.list_tasks()
        unfinished = [
            step for step in steps
            if step.get("status") not in {PLAN_STEP_STATUS_COMPLETED, PLAN_STEP_STATUS_FAILED, PLAN_STEP_STATUS_SKIPPED}
        ]
        if unfinished:
            raise ValueError("All plan steps must be completed, failed, or skipped before plan_complete.")
        failed = any(step.get("status") == PLAN_STEP_STATUS_FAILED for step in steps)
        next_status = PLAN_STATUS_FAILED if failed else PLAN_STATUS_COMPLETED
        now = datetime.now(timezone.utc).isoformat()
        if not self._transition_status(
            expected_status=PLAN_STATUS_EXECUTING,
            status=next_status,
            completed_at=now,
        ):
            raise ValueError("Plan state changed before execution could be completed.")
        self.context.status = next_status
        self.context.plan_active = False
        event_type = "PLAN_FAILED" if failed else "PLAN_COMPLETED"
        self._record(event_type, summary.strip())
        self._append_runtime_event(
            event_type,
            status="failed" if failed else "completed",
            extra={"summary": summary.strip()},
        )
        return self.context

    def record_tool_denied(self, *, tool_call_id: str, tool_name: str, message: str) -> None:
        """记录只读规划阶段拒绝的变更型工具调用。"""

        self._append_runtime_event(
            "PLAN_TOOL_DENIED",
            status="completed",
            extra={"toolCallId": tool_call_id, "toolName": tool_name, "message": message},
        )

    def _prepare_action(self, action: str) -> None:
        if not self.context.plan_id:
            raise ValueError("Plan review action requires an existing plan.")
        if self.context.status not in PLAN_ACTIVE_STATUSES:
            raise ValueError(f"Plan {self.context.plan_id} is already {self.context.status}.")
        if action == "revise":
            if self.context.status != PLAN_STATUS_REVIEW_WAITING:
                raise ValueError("Only a plan waiting for review can be revised.")
            if not self.context.feedback:
                raise ValueError("Plan revision feedback cannot be empty.")
            if not self._transition_status(
                expected_status=PLAN_STATUS_REVIEW_WAITING,
                status=PLAN_STATUS_REVISING,
                feedback=self.context.feedback,
            ):
                raise ValueError("Plan review was already handled by another request.")
            self.context.status = PLAN_STATUS_REVISING
            self.context.plan_active = True
            self._record("PLAN_REVISION_REQUESTED", self.context.feedback)
            self._append_runtime_event("PLAN_REVISION_REQUESTED", status="running")
            return
        if action == "approve":
            if self.context.status != PLAN_STATUS_REVIEW_WAITING:
                raise ValueError("Only a plan waiting for review can be approved.")
            if not self._transition_status(
                expected_status=PLAN_STATUS_REVIEW_WAITING,
                status=PLAN_STATUS_EXECUTING,
                approved_revision=self.context.revision,
                execution_run_id=self.context.run_id,
            ):
                raise ValueError("Plan review was already handled by another request.")
            self.context.status = PLAN_STATUS_APPROVED
            self.context.approved_revision = self.context.revision
            self.context.plan_active = False
            self._record("PLAN_APPROVED", f"revision={self.context.revision}")
            self._append_runtime_event("PLAN_APPROVED", status="completed")
            self.context.status = PLAN_STATUS_EXECUTING
            self.context.execution_run_id = self.context.run_id
            self._record("PLAN_EXECUTION_STARTED", f"run={self.context.run_id}")
            self._append_runtime_event("PLAN_EXECUTION_STARTED", status="running")
            return
        if action == "cancel":
            if self.context.status != PLAN_STATUS_REVIEW_WAITING:
                raise ValueError("Only a plan waiting for review can be cancelled.")
            now = datetime.now(timezone.utc).isoformat()
            if not self._transition_status(
                expected_status=PLAN_STATUS_REVIEW_WAITING,
                status=PLAN_STATUS_CANCELLED,
                completed_at=now,
            ):
                raise ValueError("Plan review was already handled by another request.")
            self.context.status = PLAN_STATUS_CANCELLED
            self.context.plan_active = False
            self._record("PLAN_CANCELLED", "Cancelled by user during review.")
            self._append_runtime_event("PLAN_CANCELLED", status="completed")
            return
        raise ValueError(f"Unsupported plan action: {action}")

    def _load(self, plan_id: str) -> None:
        if not self.persist:
            raise ValueError("Plan review actions require persistent storage.")
        from src.storage.records.plan_records import get_plan_record

        plan = get_plan_record(plan_id)
        if not plan or str(plan.get("sessionId") or "") != self.context.session_id:
            raise ValueError("Plan does not exist in the current session.")
        self.context.plan_id = plan_id
        self.context.current_plan_file = self._make_plan_file_path(plan_id)
        self.context.title = str(plan.get("title") or "Runtime Plan")
        self.context.goal = str(plan.get("goal") or "")
        self.context.status = str(plan.get("status") or "")
        self.context.markdown = str(plan.get("markdown") or "")
        self.context.steps = list(plan.get("steps") or [])
        self.context.revision = int(plan.get("revision") or 0)
        self.context.approved_revision = plan.get("approvedRevision")
        self.context.execution_run_id = str(plan.get("executionRunId") or "")

    def _create_plan_record(self) -> None:
        from src.storage.records.plan_records import create_plan_record

        now = datetime.now(timezone.utc).isoformat()
        create_plan_record({
            "id": self.context.plan_id,
            "runId": self.context.run_id,
            "sessionId": self.context.session_id,
            "agentId": self.context.agent_id,
            "title": self.context.title,
            "goal": self.context.goal,
            "status": self.context.status,
            "markdown": self.context.markdown,
            "currentStepId": None,
            "revision": 0,
            "approvedRevision": None,
            "feedback": "",
            "executionRunId": "",
            "createdAt": now,
            "updatedAt": now,
            "completedAt": None,
        }, [])

    def _persist_plan(self, **updates: Any) -> None:
        if not self.persist or not self.context.plan_id:
            return
        from src.storage.records.plan_records import update_plan_record

        update_plan_record(self.context.plan_id, **updates)

    def _transition_status(
        self,
        *,
        expected_status: str,
        status: str,
        approved_revision: int | None = None,
        feedback: str | None = None,
        execution_run_id: str | None = None,
        completed_at: str | None = None,
    ) -> bool:
        """执行带期望状态的持久化迁移；非持久化测试直接通过。"""

        if not self.persist:
            return True
        from src.storage.records.plan_records import transition_plan_status_record

        updated = transition_plan_status_record(
            self.context.plan_id,
            expected_status=expected_status,
            status=status,
            approved_revision=approved_revision,
            feedback=feedback,
            execution_run_id=execution_run_id,
            completed_at=completed_at,
        )
        return updated is not None

    def _record(self, event_type: str, content: str, *, step_id: str | None = None) -> None:
        if not self.persist or not self.context.plan_id:
            return
        from src.storage.records.plan_records import append_plan_event_record

        append_plan_event_record(
            self.context.plan_id,
            step_id=step_id,
            event_type=event_type,
            content=content,
        )

    def _require_planning(self) -> None:
        if not self.context.plan_id or self.context.status not in {
            PLAN_STATUS_DRAFTING,
            PLAN_STATUS_REVISING,
            PLAN_STATUS_REPLANNING,
        }:
            raise ValueError("This Plan tool is only available while drafting or revising a plan.")

    def _normalize_steps(self, items: list[dict[str, Any]]) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        steps: list[dict] = []
        for index, item in enumerate(items):
            title = str(item.get("title") or "").strip()
            if not title:
                raise ValueError("Plan step title is required.")
            steps.append({
                "id": str(item.get("id") or f"{self.context.plan_id}-step-{index + 1}"),
                "planId": self.context.plan_id,
                "stepIndex": index,
                "title": title,
                "description": str(item.get("description") or ""),
                "status": PLAN_STEP_STATUS_PENDING,
                "assignedAgentId": item.get("assignedAgentId") or item.get("assigned_agent_id"),
                "evidence": "",
                "result": "",
                "startedAt": None,
                "completedAt": None,
                "updatedAt": now,
            })
        return steps

    def _normalize_step_status(self, status: str) -> str:
        value = str(status or PLAN_STEP_STATUS_PENDING).strip().lower()
        if value not in PLAN_STEP_STATUSES:
            allowed = ", ".join(sorted(PLAN_STEP_STATUSES))
            raise ValueError(f"Invalid plan step status: {status}. Allowed: {allowed}.")
        return value

    @staticmethod
    def _find_step(steps: list[dict], *, step_id: str | None, step_index: int | None) -> dict:
        if step_id:
            match = next((item for item in steps if str(item.get("id") or "") == step_id), None)
            if match:
                return match
        if step_index is not None:
            match = next((item for item in steps if int(item.get("stepIndex") or 0) == step_index), None)
            if match:
                return match
        raise ValueError("Plan step was not found.")

    @staticmethod
    def _runtime_status_for_step(status: str) -> str:
        if status == PLAN_STEP_STATUS_FAILED:
            return "failed"
        if status in {PLAN_STEP_STATUS_COMPLETED, PLAN_STEP_STATUS_SKIPPED}:
            return "completed"
        return "running"

    def _make_plan_file_path(self, plan_id: str) -> str:
        safe_session = self.context.session_id.replace("/", "-")
        return os.path.join(".agent-workstation", "plans", safe_session, f"{plan_id}.md")

    def _snapshot_payload(self) -> dict[str, Any]:
        return {
            "planId": self.context.plan_id,
            "planFile": self.context.current_plan_file,
            "title": self.context.title or "执行计划",
            "goal": self.context.goal,
            "status": self.context.status,
            "revision": self.context.revision,
            "approvedRevision": self.context.approved_revision,
            "markdown": self.context.markdown,
            "steps": self.context.steps,
            "feedback": self.context.feedback,
            "executionRunId": self.context.execution_run_id,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        }

    def _append_runtime_event(
        self,
        event_type: str,
        *,
        status: str,
        reason: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.domain_events.append(build_plan_domain_event(
            event_type,
            status=status,
            reason=reason,
            payload={**self._snapshot_payload(), **(extra or {})},
        ))
