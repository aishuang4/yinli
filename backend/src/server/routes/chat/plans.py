"""Plan Runtime v2 — structure first, Markdown as projection."""

import asyncio
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from src.storage.database import (
    append_plan_event_record,
    create_plan_record,
    get_plan_record,
    update_plan_record,
    update_plan_step_record,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plain_title(text: str, fallback: str = "任务计划") -> str:
    value = " ".join(text.strip().split())
    return (value[:36] + ("..." if len(value) > 36 else "")) or fallback


def should_create_plan(
    *,
    agent_config: object | None,
    message: str,
    has_team: bool,
    has_tools: bool,
    has_skills: bool,
    has_images: bool,
) -> bool:
    """Return whether this run should create a structured plan."""

    plan_config = getattr(agent_config, "plan_config", None)
    if not plan_config or not getattr(plan_config, "enabled", True):
        return False
    if has_team or has_tools or has_skills or has_images:
        return True
    if len(message.strip()) >= 40:
        return True
    return bool(re.search(r"(创建|修改|实现|生成|分析|调研|检查|测试|总结|写|规划|协作)", message))


def render_plan_markdown(plan: dict[str, Any]) -> str:
    """Render deterministic Markdown from structured plan state."""

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
            f"{step.get('title') or 'Step'} — {step.get('status') or 'pending'}"
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


def create_runtime_plan(
    *,
    session_id: str,
    agent_id: str,
    team_id: str | None,
    title: str,
    goal: str,
    mode: str,
    max_steps: int,
    team_members: list[Any],
    has_tools: bool,
    has_skills: bool,
) -> dict[str, Any]:
    """Create a deterministic first-pass plan for one chat run."""

    now = _utc_now()
    plan_id = f"plan-{uuid.uuid4().hex}"
    step_specs: list[dict[str, Any]] = []
    if team_members:
        worker_members = [member for member in team_members if getattr(member, "role", "") != "leader"]
        for member in worker_members[: max(1, max_steps - 1)]:
            step_specs.append({
                "title": f"{getattr(member, 'name', 'Worker')} 执行分工任务",
                "description": getattr(member, "handoff_prompt", "") or getattr(member, "description", "") or "根据 Leader 任务分工执行并返回结果。",
                "assignedAgentId": getattr(member, "agent_id", None),
            })
        step_specs.append({
            "title": "Leader 汇总最终结果",
            "description": "读取所有 Worker 输出，形成最终回复。",
            "assignedAgentId": agent_id,
        })
    else:
        step_specs = [
            {"title": "理解任务和约束", "description": "确认目标、上下文和可用资源。", "assignedAgentId": agent_id},
            {"title": "执行必要操作", "description": "按需调用工具、Skills 或模型能力完成任务。", "assignedAgentId": agent_id},
            {"title": "验证并汇总结果", "description": "检查输出质量，给出最终答复。", "assignedAgentId": agent_id},
        ]
        if not has_tools and not has_skills and mode == "lightweight":
            step_specs = step_specs[:2]
    step_specs = step_specs[: max(1, min(max_steps, 20))]
    steps = [
        {
            "id": f"step-{uuid.uuid4().hex}",
            "planId": plan_id,
            "stepIndex": index,
            "title": spec["title"],
            "description": spec.get("description", ""),
            "status": "active" if index == 0 else "pending",
            "assignedAgentId": spec.get("assignedAgentId"),
            "evidence": "",
            "result": "",
            "startedAt": now if index == 0 else None,
            "completedAt": None,
            "updatedAt": now,
        }
        for index, spec in enumerate(step_specs)
    ]
    plan = {
        "id": plan_id,
        "runId": "",
        "sessionId": session_id,
        "agentId": agent_id,
        "teamId": team_id,
        "title": title or _plain_title(goal),
        "goal": goal,
        "status": "running",
        "markdown": "",
        "currentStepId": steps[0]["id"] if steps else None,
        "steps": steps,
        "createdAt": now,
        "updatedAt": now,
        "completedAt": None,
    }
    plan["markdown"] = render_plan_markdown(plan)
    created = create_plan_record(plan, steps)
    append_plan_event_record(plan_id, step_id=plan.get("currentStepId"), event_type="PLAN_CREATED", content=goal)
    return created


def build_plan_context_prompt(plan: dict[str, Any] | None) -> str:
    """Build model-facing Plan Runtime instructions."""

    if not plan:
        return ""
    return "\n".join([
        "## Plan Runtime",
        "A structured runtime plan has been created for this run.",
        "Follow the plan step by step. Treat the Markdown below as the readable projection of backend plan state.",
        "When Plan Runtime tools are available, update the structured plan with them instead of only writing plan text in your reply.",
        "Use CompletePlanStep after a step is genuinely done, UpdatePlanStep after meaningful evidence or partial progress, BlockPlanStep when blocked, and Replan when new information changes the approach.",
        "Do not create a separate competing plan in the final answer. Mention plan updates only when they affect execution.",
        "",
        plan.get("markdown", ""),
    ]).strip()


def serialize_plan_for_event(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not plan:
        return None
    return plan


_PLAN_APPROVALS: dict[str, asyncio.Future[str]] = {}


def create_plan_approval(plan: dict[str, Any]) -> tuple[str, asyncio.Future[str]]:
    """创建一次计划审批等待项。"""

    approval_id = f"plan-approval-{uuid.uuid4().hex}"
    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    _PLAN_APPROVALS[approval_id] = future
    append_plan_event_record(str(plan["id"]), step_id=None, event_type="PLAN_APPROVAL_REQUIRED", content="")
    return approval_id, future


def resolve_plan_approval(approval_id: str, action: str) -> bool:
    """审批或拒绝一次计划。"""

    future = _PLAN_APPROVALS.pop(approval_id, None)
    if future is None or future.done():
        return False
    future.set_result("approve" if action == "approve" else "reject")
    return True


def mark_plan_tool_event(plan: dict[str, Any] | None, *, event_type: str, tool_name: str) -> dict[str, Any] | None:
    """Record a compact tool event against the current step."""

    if not plan or not plan.get("currentStepId"):
        return plan
    if event_type not in {"TOOL_CALL_START", "TOOL_CALL_END", "TOOL_RESULT_END"}:
        return plan
    step_id = str(plan["currentStepId"])
    current = get_plan_record(str(plan["id"])) or plan
    step = next((item for item in current.get("steps", []) if item.get("id") == step_id), None)
    if not step:
        return current
    if event_type == "TOOL_CALL_START" and int(step.get("stepIndex", 0)) == 0:
        steps = current.get("steps") or []
        next_step = next((item for item in steps if int(item.get("stepIndex", 0)) == 1), None)
        if next_step:
            now = _utc_now()
            update_plan_step_record(
                step_id,
                status="completed",
                result="已确认任务目标、上下文和可用资源。",
                completed_at=now,
            )
            update_plan_step_record(
                str(next_step["id"]),
                status="active",
                started_at=now,
            )
            current = update_plan_record(str(plan["id"]), current_step_id=str(next_step["id"])) or current
            step_id = str(next_step["id"])
            step = next_step
    evidence_lines = [line for line in str(step.get("evidence") or "").splitlines() if line.strip()]
    label = {
        "TOOL_CALL_START": "开始调用工具",
        "TOOL_CALL_END": "完成工具调用",
        "TOOL_RESULT_END": "收到工具结果",
    }.get(event_type, event_type)
    evidence_lines.append(f"- {label}: {tool_name}")
    update_plan_step_record(step_id, evidence="\n".join(evidence_lines[-12:]))
    append_plan_event_record(str(plan["id"]), step_id=step_id, event_type=event_type, content=tool_name)
    updated = get_plan_record(str(plan["id"]))
    if updated:
        updated["markdown"] = render_plan_markdown(updated)
        updated = update_plan_record(str(plan["id"]), markdown=updated["markdown"]) or updated
    return updated


def complete_runtime_plan(plan: dict[str, Any] | None, *, result: str) -> dict[str, Any] | None:
    """Mark all unfinished steps and the plan as completed."""

    if not plan:
        return None
    current = get_plan_record(str(plan["id"])) or plan
    now = _utc_now()
    steps = current.get("steps") or []
    for index, step in enumerate(steps):
        if step.get("status") != "completed":
            update_plan_step_record(
                str(step["id"]),
                status="completed",
                result=result[:1200] if index == len(steps) - 1 else step.get("result") or "已处理。",
                completed_at=now,
            )
    updated = get_plan_record(str(plan["id"])) or current
    updated["status"] = "completed"
    updated["currentStepId"] = None
    updated["completedAt"] = now
    updated["markdown"] = render_plan_markdown(updated)
    final_plan = update_plan_record(
        str(plan["id"]),
        status="completed",
        current_step_id="",
        completed_at=now,
        markdown=updated["markdown"],
    ) or updated
    append_plan_event_record(str(plan["id"]), step_id=None, event_type="PLAN_COMPLETED", content=result[:2000])
    return final_plan


def fail_runtime_plan(plan: dict[str, Any] | None, *, error: str) -> dict[str, Any] | None:
    if not plan:
        return None
    current = get_plan_record(str(plan["id"])) or plan
    current["status"] = "failed"
    current["markdown"] = render_plan_markdown(current)
    append_plan_event_record(str(plan["id"]), step_id=current.get("currentStepId"), event_type="PLAN_FAILED", content=error)
    return update_plan_record(str(plan["id"]), status="failed", markdown=current["markdown"]) or current
