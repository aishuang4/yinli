"""Plan Mode 状态协调器。"""

import os
import uuid
from datetime import datetime, timezone

from src.server.routes.chat.plan_mode.models import (
    PLAN_STATUS_DRAFT,
    PLAN_STATUS_WAITING_APPROVAL,
    PlanModeContext,
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
        team_id: str | None = None,
        persist: bool = True,
    ) -> None:
        self.team_id = team_id
        self.persist = persist
        self.runtime_events: list[dict] = []
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

        events = list(self.runtime_events)
        self.runtime_events.clear()
        return events

    def enter(self, *, goal: str, title: str = "Runtime Plan") -> PlanModeContext:
        """进入 Plan Mode 并创建 plan 记录。"""

        already_created = bool(self.context.plan_id)
        plan_id = self.context.plan_id or f"plan-{uuid.uuid4().hex}"
        plan_file = self._make_plan_file_path(plan_id)
        markdown = self.context.markdown or f"# {title}\n\n## Goal\n{goal}\n"
        self.context.plan_id = plan_id
        self.context.plan_active = True
        self.context.waiting_approval = False
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

    def exit_for_approval(self) -> PlanModeContext:
        """退出只读计划阶段并等待用户审批。"""

        self.context.plan_active = False
        self.context.waiting_approval = True
        self._append_runtime_event(
            "PLAN_WAITING_APPROVAL",
            status="waiting",
            payload={"markdown": self.context.markdown},
        )
        if self.persist and self.context.plan_id:
            from src.storage.database import append_plan_event_record, update_plan_record

            update_plan_record(self.context.plan_id, status=PLAN_STATUS_WAITING_APPROVAL)
            append_plan_event_record(
                self.context.plan_id,
                step_id=None,
                event_type="PLAN_WAITING_APPROVAL",
                content="Plan mode exited and waits for approval.",
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
                "teamId": self.team_id,
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
        from src.server.routes.chat.plan_mode.events import build_plan_event

        self.runtime_events.append(build_plan_event(
            event_type,
            context=self.context,
            status=status,
            payload=payload,
        ))
