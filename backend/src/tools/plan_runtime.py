"""Plan Runtime 内部工具。"""

import json
from datetime import datetime, timezone
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk

from src.storage.database import (
    append_plan_event_record,
    get_plan_record,
    update_plan_record,
    update_plan_step_record,
)


PLAN_TOOL_NAMES = {"UpdatePlanStep", "CompletePlanStep", "BlockPlanStep", "Replan"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _render_plan_markdown(plan: dict[str, Any]) -> str:
    """渲染结构化计划，避免工具模块反向导入 chat 路由包。"""

    lines = [
        f"# Plan: {plan.get('title') or '任务计划'}",
        "",
        "## Goal",
        str(plan.get("goal") or ""),
        "",
        "## Status",
        f"- Overall: {plan.get('status') or 'draft'}",
        f"- Current Step: {plan.get('currentStepId') or 'none'}",
        "",
        "## Steps",
    ]
    for step in plan.get("steps") or []:
        checked = "x" if step.get("status") == "completed" else " "
        lines.append(
            f"- [{checked}] {int(step.get('stepIndex', 0)) + 1}. "
            f"{step.get('title') or 'Step'} - {step.get('status') or 'pending'}"
        )
        if step.get("description"):
            lines.append(f"  - {step['description']}")
    lines.extend(["", "## Execution Log"])
    for step in plan.get("steps") or []:
        lines.extend([
            "",
            f"### Step {int(step.get('stepIndex', 0)) + 1}: {step.get('title') or 'Step'}",
            f"Status: {step.get('status') or 'pending'}",
        ])
        if step.get("assignedAgentId"):
            lines.append(f"Assigned Agent: {step['assignedAgentId']}")
        if step.get("evidence"):
            lines.extend(["", "Evidence:", str(step["evidence"])])
        if step.get("result"):
            lines.extend(["", "Result:", str(step["result"])])
    return "\n".join(lines).strip() + "\n"


class _PlanToolBase(ToolBase):
    """Plan 工具基类，所有操作都限定在当前运行的 plan_id。"""

    is_mcp: bool = False
    is_read_only: bool = False
    is_concurrency_safe: bool = False
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(self, plan_id: str) -> None:
        self.plan_id = plan_id

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="Plan Runtime tools only update the current structured plan.",
            decision_reason="Internal plan state update",
        )

    def _plan(self) -> dict[str, Any] | None:
        return get_plan_record(self.plan_id)

    def _find_step(self, plan: dict[str, Any], step_id: str = "", step_index: int | None = None) -> dict[str, Any] | None:
        steps = plan.get("steps") or []
        if step_id:
            return next((step for step in steps if step.get("id") == step_id), None)
        if step_index is not None:
            return next((step for step in steps if int(step.get("stepIndex", -1)) == int(step_index)), None)
        current_id = plan.get("currentStepId")
        return next((step for step in steps if step.get("id") == current_id), None) or (steps[0] if steps else None)

    def _sync_markdown(self, plan_id: str) -> dict[str, Any] | None:
        plan = get_plan_record(plan_id)
        if not plan:
            return None
        markdown = _render_plan_markdown(plan)
        return update_plan_record(plan_id, markdown=markdown) or plan

    def _chunk(self, payload: dict[str, Any], ok: bool = True) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=json.dumps(payload, ensure_ascii=False, indent=2))],
            state=ToolResultState.SUCCESS if ok else ToolResultState.ERROR,
            is_last=True,
        )

    def _missing_plan(self) -> ToolChunk:
        return self._chunk({"ok": False, "error": "No active Plan Runtime found."}, ok=False)

    def _missing_step(self) -> ToolChunk:
        return self._chunk({"ok": False, "error": "Plan step not found."}, ok=False)


class UpdatePlanStep(_PlanToolBase):
    """更新当前计划步骤。"""

    name: str = "UpdatePlanStep"
    description: str = (
        "Update one Plan Runtime step with evidence, result, or status. "
        "Use this after meaningful progress, tool results, or changed understanding."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "step_id": {"type": "string", "description": "Optional step id. Defaults to current step."},
            "step_index": {"type": "integer", "description": "Optional zero-based step index."},
            "status": {
                "type": "string",
                "enum": ["pending", "active", "completed", "blocked", "skipped"],
                "description": "Optional new step status.",
            },
            "evidence": {"type": "string", "description": "Compact evidence or observation for this step."},
            "result": {"type": "string", "description": "Compact result for this step."},
        },
    }

    async def __call__(
        self,
        step_id: str = "",
        step_index: int | None = None,
        status: str | None = None,
        evidence: str = "",
        result: str = "",
    ) -> ToolChunk:
        plan = self._plan()
        if not plan:
            return self._missing_plan()
        step = self._find_step(plan, step_id=step_id, step_index=step_index)
        if not step:
            return self._missing_step()

        completed_at = _utc_now() if status == "completed" and not step.get("completedAt") else None
        update_plan_step_record(
            str(step["id"]),
            status=status,
            evidence=evidence[:2000] if evidence else None,
            result=result[:2000] if result else None,
            completed_at=completed_at,
        )
        if status == "active":
            update_plan_record(str(plan["id"]), status="running", current_step_id=str(step["id"]))
        append_plan_event_record(str(plan["id"]), step_id=str(step["id"]), event_type="PLAN_STEP_UPDATED", content=result or evidence or status or "")
        updated = self._sync_markdown(str(plan["id"]))
        return self._chunk({"ok": True, "plan": updated})


class CompletePlanStep(_PlanToolBase):
    """完成当前计划步骤，并可激活下一步。"""

    name: str = "CompletePlanStep"
    description: str = "Mark one Plan Runtime step completed, record its result, and optionally activate the next pending step."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "step_id": {"type": "string", "description": "Optional step id. Defaults to current step."},
            "step_index": {"type": "integer", "description": "Optional zero-based step index."},
            "result": {"type": "string", "description": "Short result proving the step is complete."},
            "activate_next": {"type": "boolean", "description": "Whether to activate the next pending step.", "default": True},
        },
    }

    async def __call__(
        self,
        step_id: str = "",
        step_index: int | None = None,
        result: str = "",
        activate_next: bool = True,
    ) -> ToolChunk:
        plan = self._plan()
        if not plan:
            return self._missing_plan()
        step = self._find_step(plan, step_id=step_id, step_index=step_index)
        if not step:
            return self._missing_step()

        now = _utc_now()
        update_plan_step_record(str(step["id"]), status="completed", result=result[:2000], completed_at=now)
        current_step_id = str(plan.get("currentStepId") or "")
        if activate_next:
            steps = get_plan_record(str(plan["id"])).get("steps", []) if get_plan_record(str(plan["id"])) else []
            next_step = next((item for item in steps if item.get("status") == "pending"), None)
            if next_step:
                update_plan_step_record(str(next_step["id"]), status="active", started_at=now)
                current_step_id = str(next_step["id"])
            else:
                current_step_id = ""
        update_plan_record(str(plan["id"]), status="running", current_step_id=current_step_id)
        append_plan_event_record(str(plan["id"]), step_id=str(step["id"]), event_type="PLAN_STEP_COMPLETED", content=result[:2000])
        updated = self._sync_markdown(str(plan["id"]))
        return self._chunk({"ok": True, "plan": updated})


class BlockPlanStep(_PlanToolBase):
    """阻塞当前计划步骤。"""

    name: str = "BlockPlanStep"
    description: str = "Mark one Plan Runtime step blocked when execution cannot continue without missing data, permission, or user input."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "step_id": {"type": "string", "description": "Optional step id. Defaults to current step."},
            "step_index": {"type": "integer", "description": "Optional zero-based step index."},
            "reason": {"type": "string", "description": "Short blocking reason."},
        },
        "required": ["reason"],
    }

    async def __call__(self, reason: str, step_id: str = "", step_index: int | None = None) -> ToolChunk:
        plan = self._plan()
        if not plan:
            return self._missing_plan()
        step = self._find_step(plan, step_id=step_id, step_index=step_index)
        if not step:
            return self._missing_step()
        update_plan_step_record(str(step["id"]), status="blocked", result=reason[:2000])
        update_plan_record(str(plan["id"]), status="blocked", current_step_id=str(step["id"]))
        append_plan_event_record(str(plan["id"]), step_id=str(step["id"]), event_type="PLAN_STEP_BLOCKED", content=reason[:2000])
        updated = self._sync_markdown(str(plan["id"]))
        return self._chunk({"ok": True, "plan": updated})


class Replan(_PlanToolBase):
    """重规划现有步骤的标题和说明。"""

    name: str = "Replan"
    description: str = (
        "Revise the existing Plan Runtime steps when new information changes the approach. "
        "This updates existing steps by index; keep the plan concise."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "Why replanning is needed."},
            "steps": {
                "type": "array",
                "description": "Replacement text for existing steps by order.",
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                    },
                    "required": ["title"],
                },
            },
        },
        "required": ["reason", "steps"],
    }

    async def __call__(self, reason: str, steps: list[dict[str, Any]]) -> ToolChunk:
        plan = self._plan()
        if not plan:
            return self._missing_plan()
        current_steps = plan.get("steps") or []
        for index, spec in enumerate(steps[: len(current_steps)]):
            step = current_steps[index]
            title = str(spec.get("title") or step.get("title") or "Step")[:200]
            description = str(spec.get("description") or "")[:1000]
            update_plan_step_record(
                str(step["id"]),
                title=title,
                description=description,
                evidence=f"重规划：{reason[:500]}",
            )
        update_plan_record(str(plan["id"]), status="running")
        append_plan_event_record(str(plan["id"]), step_id=None, event_type="PLAN_REPLANNED", content=reason[:2000])
        updated = self._sync_markdown(str(plan["id"]))
        return self._chunk({"ok": True, "plan": updated})


def create_plan_runtime_tools(plan_id: str | None) -> list[ToolBase]:
    """按当前 plan_id 创建可注入 Agent 的 Plan Runtime 工具。"""

    if not plan_id:
        return []
    return [
        UpdatePlanStep(plan_id),
        CompletePlanStep(plan_id),
        BlockPlanStep(plan_id),
        Replan(plan_id),
    ]
