"""Plan Runtime v2 — structure first, Markdown as projection."""

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from agentscope.message import UserMsg
from agentscope.permission._types import PermissionMode

from src.agents.factory import AgentGuardrails, AgentModelOptions, RuntimeToolScope, get_or_create_agent
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


def _trim_plan_text(value: Any, *, limit: int = 600) -> str:
    """清理模型计划字段，避免过长内容污染结构化计划。"""

    text = " ".join(str(value or "").strip().split())
    return text[:limit]


def _extract_json_object(text: str) -> dict[str, Any]:
    """从模型输出中提取第一个 JSON 对象。"""

    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


_EXPLICIT_PLAN_RE = re.compile(
    r"(计划|规划|方案|步骤|拆解|待办|里程碑|路线图|执行计划|实施方案|plan|roadmap|todo|milestone)",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(
    r"(创建|新建|修改|实现|生成|调研|分析|检查|测试|修复|优化|重构|部署|运行|读取|查找|搜索|调用|整理|总结|写)",
    re.IGNORECASE,
)
_COMPLEX_TARGET_RE = re.compile(
    r"(项目|代码|仓库|文件|目录|接口|API|页面|组件|后端|前端|数据库|SQLite|MCP|Skill|Agent|Team|"
    r"多步|多个|批量|全部|端到端|回归|上线|发布)",
    re.IGNORECASE,
)
_CONVERSATIONAL_RE = re.compile(
    r"^(你好|您好|hi|hello|谢谢|你是谁|介绍一下你自己|解释一下|是什么|为什么|怎么理解|聊聊)",
    re.IGNORECASE,
)


def detect_plan_intent(
    *,
    message: str,
    has_team: bool,
    has_tools: bool,
    has_skills: bool,
    has_images: bool,
) -> bool:
    """识别本轮是否真的需要进入 Plan Runtime。

    计划运行时是执行型能力，不应因为 Agent 可用工具或用户消息较长就默认触发。
    只有用户明确要求规划，或请求呈现出需要多步执行/协作/工具调用的意图时才创建计划。
    """

    text = " ".join(message.strip().split())
    if not text:
        return False

    if _EXPLICIT_PLAN_RE.search(text):
        return True

    has_action = bool(_ACTION_RE.search(text))
    has_complex_target = bool(_COMPLEX_TARGET_RE.search(text))

    if _CONVERSATIONAL_RE.search(text) and not (has_action and has_complex_target):
        return False

    if has_team:
        return has_action or has_complex_target or len(text) >= 80

    if has_images:
        return bool(re.search(r"(分析|识别|检查|看看|总结|提取|对比)", text))

    if has_tools or has_skills:
        return has_action and (has_complex_target or len(text) >= 60)

    return has_action and has_complex_target and len(text) >= 60


def _single_agent_step_specs(
    *,
    goal: str,
    agent_id: str,
    mode: str,
    has_tools: bool,
    has_skills: bool,
) -> list[dict[str, Any]]:
    """按任务意图生成单 Agent 的初始计划骨架。"""

    text = " ".join(goal.strip().split())
    mentioned_dimensions = [
        ("前端入口与交互", "检查页面入口、表单结构、选择/注入流程、状态反馈和历史展示是否完整。")
        if "前端" in text else None,
        ("后端接口与存储", "检查 HTTP 工具注册、校验、落库、运行时注入和错误处理链路。")
        if "后端" in text else None,
        ("测试与回归验证", "确认接口测试、运行时调用测试、权限边界和前端构建验证是否覆盖。")
        if "测试" in text else None,
        ("用户体验与可用性", "评估配置成本、表单理解成本、失败反馈、调试体验和复用方式。")
        if "用户体验" in text or "体验" in text else None,
    ]
    dimension_specs = [item for item in mentioned_dimensions if item is not None]
    if len(dimension_specs) >= 2:
        specs = [
            {
                "title": "界定调研目标和现状范围",
                "description": "确认本轮要分析的功能边界、已有实现和完成标准。",
            },
            *[
                {"title": title, "description": description}
                for title, description in dimension_specs
            ],
            {
                "title": "收敛改进建议和优先级",
                "description": "按价值、风险和实现成本整理可执行的优化清单。",
            },
        ]
    elif re.search(r"(HTTP\s*工具|http\s*工具|工具管理|MCP|Skill|Agent|Team)", text, re.IGNORECASE) and re.search(r"(完善|缺|优化|分析|调研|评估)", text):
        specs = [
            {"title": "梳理功能边界", "description": "确认要评估的工具管理能力、使用场景和当前入口。"},
            {"title": "检查配置与注入链路", "description": "查看注册、保存、选择注入和运行时调用是否连贯。"},
            {"title": "评估权限和调试体验", "description": "检查只读默认、请求头配置、失败反馈和调用日志是否足够清晰。"},
            {"title": "形成优化清单", "description": "按优先级输出可落地的前后端和测试改进建议。"},
        ]
    elif re.search(r"(修复|修改|实现|重构|优化|代码|接口|组件|后端|前端|数据库|测试)", text):
        specs = [
            {"title": "定位相关代码和约束", "description": "读取相关文件，确认问题边界、现有模式和风险点。"},
            {"title": "实施必要改动", "description": "按现有架构和规范修改代码，避免扩大无关范围。"},
            {"title": "运行验证", "description": "执行针对性测试或构建检查，确认行为符合预期。"},
            {"title": "汇总变更与结果", "description": "说明修改内容、验证结果和剩余风险。"},
        ]
    elif re.search(r"(调研|分析|搜索|查找|对比|资料|竞品|市场)", text):
        specs = [
            {"title": "明确调研问题", "description": "提炼目标、范围、判断标准和需要验证的假设。"},
            {"title": "收集并筛选信息", "description": "按需使用可用工具或知识源获取相关资料。"},
            {"title": "交叉验证结论", "description": "对关键事实、差异和不确定性做一致性检查。"},
            {"title": "输出结构化结论", "description": "给出结论、依据、风险和下一步建议。"},
        ]
    elif re.search(r"(图片|截图|图像|识别|看看|提取)", text):
        specs = [
            {"title": "识别输入内容", "description": "检查用户提供的视觉输入和问题目标。"},
            {"title": "提取关键发现", "description": "围绕目标整理可见信息、异常和证据。"},
            {"title": "形成答复", "description": "用清晰结论回应用户，并标注不确定处。"},
        ]
    elif re.search(r"(写|生成|整理|总结|文档|方案|说明|报告)", text):
        specs = [
            {"title": "梳理输出目标", "description": "确认受众、格式、重点和上下文约束。"},
            {"title": "生成内容草稿", "description": "组织结构并完成主要内容。"},
            {"title": "校对和收敛", "description": "检查准确性、表达和遗漏点后输出最终版本。"},
        ]
    else:
        specs = [
            {"title": "确认任务目标", "description": "理解用户诉求、上下文和完成标准。"},
            {"title": "执行核心任务", "description": "按需使用模型能力、工具或 Skills 推进任务。"},
            {"title": "检查并交付结果", "description": "验证输出质量，给出最终答复。"},
        ]

    if (has_tools or has_skills) and len(specs) == 3:
        specs.insert(
            1,
            {"title": "准备可用资源", "description": "确认需要使用的工具、Skills 或上下文资源。"},
        )
    if not has_tools and not has_skills and mode == "lightweight":
        specs = specs[:2]
    return [{**spec, "assignedAgentId": agent_id} for spec in specs]


def _normalize_model_step_specs(
    raw_plan: dict[str, Any],
    *,
    agent_id: str,
    team_members: list[Any],
    max_steps: int,
) -> list[dict[str, Any]]:
    """校验模型生成的 Plan steps，返回可落库的步骤规格。"""

    raw_steps = raw_plan.get("steps")
    if not isinstance(raw_steps, list):
        return []

    allowed_agent_ids = {agent_id}
    for member in team_members:
        member_agent_id = getattr(member, "agent_id", None)
        if member_agent_id:
            allowed_agent_ids.add(str(member_agent_id))

    normalized: list[dict[str, Any]] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        title = _trim_plan_text(item.get("title"), limit=120)
        if not title:
            continue
        assigned_agent_id = item.get("assignedAgentId") or item.get("assigned_agent_id") or item.get("agentId")
        assigned_agent_id = str(assigned_agent_id).strip() if assigned_agent_id else agent_id
        if assigned_agent_id not in allowed_agent_ids:
            assigned_agent_id = agent_id
        normalized.append({
            "title": title,
            "description": _trim_plan_text(item.get("description"), limit=800),
            "assignedAgentId": assigned_agent_id,
        })
        if len(normalized) >= max(1, min(max_steps, 20)):
            break
    return normalized


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
    return detect_plan_intent(
        message=message,
        has_team=has_team,
        has_tools=has_tools,
        has_skills=has_skills,
        has_images=has_images,
    )


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
        step_specs = _single_agent_step_specs(
            goal=goal,
            agent_id=agent_id,
            mode=mode,
            has_tools=has_tools,
            has_skills=has_skills,
        )
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


async def create_model_first_runtime_plan(
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
    model_name: str,
    fallback_names: list[str],
    model_options: AgentModelOptions | None = None,
) -> dict[str, Any]:
    """优先由模型生成结构化 Plan JSON，失败时回退到规则模板。"""

    max_steps = max(1, min(int(max_steps or 6), 20))
    raw_plan = await _generate_plan_json_with_model(
        goal=goal,
        mode=mode,
        max_steps=max_steps,
        team_members=team_members,
        has_tools=has_tools,
        has_skills=has_skills,
        model_name=model_name,
        fallback_names=fallback_names,
        model_options=model_options,
    )
    step_specs = _normalize_model_step_specs(
        raw_plan,
        agent_id=agent_id,
        team_members=team_members,
        max_steps=max_steps,
    )
    if not step_specs:
        fallback_plan = create_runtime_plan(
            session_id=session_id,
            agent_id=agent_id,
            team_id=team_id,
            title=title,
            goal=goal,
            mode=mode,
            max_steps=max_steps,
            team_members=team_members,
            has_tools=has_tools,
            has_skills=has_skills,
        )
        append_plan_event_record(
            str(fallback_plan["id"]),
            step_id=None,
            event_type="PLAN_MODEL_FALLBACK",
            content="模型未返回有效结构化 Plan JSON，已使用任务相关兜底计划。",
        )
        return fallback_plan

    model_title = _trim_plan_text(raw_plan.get("title"), limit=80)
    return _create_plan_from_step_specs(
        session_id=session_id,
        agent_id=agent_id,
        team_id=team_id,
        title=model_title or title or _plain_title(goal),
        goal=goal,
        step_specs=step_specs,
    )


async def _generate_plan_json_with_model(
    *,
    goal: str,
    mode: str,
    max_steps: int,
    team_members: list[Any],
    has_tools: bool,
    has_skills: bool,
    model_name: str,
    fallback_names: list[str],
    model_options: AgentModelOptions | None,
) -> dict[str, Any]:
    """调用轻量 Planner Agent，要求只返回结构化 JSON。"""

    worker_lines = []
    for member in team_members:
        worker_lines.append(
            f"- agentId={getattr(member, 'agent_id', '')}; name={getattr(member, 'name', '')}; "
            f"role={getattr(member, 'role', '')}; description={getattr(member, 'description', '') or 'none'}; "
            f"executionMode={getattr(member, 'execution_mode', '') or 'parallel'}; "
            f"dependsOn={', '.join(getattr(member, 'depends_on', []) or []) or 'none'}"
        )
    system_prompt = "\n".join([
        "You are a runtime execution planner.",
        "Return only valid compact JSON. Do not call tools. Do not write markdown.",
        "Create a practical plan for this single chat run.",
        "The plan must be executable, concise, and task-specific.",
        "Use only provided assignedAgentId values. For single-agent tasks use the main agent id.",
        f"Maximum steps: {max_steps}.",
        'Schema: {"title":"short title","steps":[{"title":"step title","description":"short detail","assignedAgentId":"agent-id"}]}',
    ])
    user_prompt = "\n\n".join([
        f"Planning mode: {mode}",
        f"Has tools: {has_tools}",
        f"Has skills: {has_skills}",
        "User goal:",
        goal,
        "Team members:" if worker_lines else "Team members: none",
        "\n".join(worker_lines) if worker_lines else "",
        "Prefer 2-6 steps unless the task genuinely needs more.",
        "Do not include generic meta steps unless they are necessary for execution.",
    ]).strip()

    try:
        agent = get_or_create_agent(
            agent_id=f"runtime-plan-planner:{model_name}:{uuid.uuid4().hex}",
            name="RuntimePlanPlanner",
            system_prompt=system_prompt,
            model_name=model_name,
            fallback_names=fallback_names,
            thinking_enable=False,
            permission_mode=PermissionMode.BYPASS,
            model_options=AgentModelOptions(
                temperature=model_options.temperature if model_options else None,
                top_p=model_options.top_p if model_options else None,
                max_tokens=min(model_options.max_tokens or 1200, 1200) if model_options else 1200,
                max_react_iters=2,
            ),
            guardrails=AgentGuardrails(
                network_enabled=False,
                tool_permission_profile="read_only",
            ),
            tool_scope=RuntimeToolScope(
                allow_mcp=False,
                allow_knowledge=False,
                allow_bash=False,
                allow_http=False,
            ),
        )
        text = ""
        async for event in agent.reply_stream(UserMsg(name="user", content=user_prompt)):
            event_data = event.model_dump(mode="json")
            if event_data.get("type") == "TEXT_BLOCK_DELTA":
                text += str(event_data.get("delta") or "")
        return _extract_json_object(text)
    except Exception:
        return {}


def _create_plan_from_step_specs(
    *,
    session_id: str,
    agent_id: str,
    team_id: str | None,
    title: str,
    goal: str,
    step_specs: list[dict[str, Any]],
) -> dict[str, Any]:
    """根据已校验步骤规格落库 Plan。"""

    now = _utc_now()
    plan_id = f"plan-{uuid.uuid4().hex}"
    steps = [
        {
            "id": f"step-{uuid.uuid4().hex}",
            "planId": plan_id,
            "stepIndex": index,
            "title": spec["title"],
            "description": spec.get("description", ""),
            "status": "active" if index == 0 else "pending",
            "assignedAgentId": spec.get("assignedAgentId") or agent_id,
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
    append_plan_event_record(plan_id, step_id=None, event_type="PLAN_CREATED_BY_MODEL", content=plan["markdown"][:2000])
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


def mark_plan_step_for_agent(
    plan: dict[str, Any] | None,
    *,
    assigned_agent_id: str,
    status: str,
    result: str,
) -> dict[str, Any] | None:
    """按 assignedAgentId 更新 Team worker 对应的 Plan step。"""

    if not plan or not assigned_agent_id:
        return plan
    current = get_plan_record(str(plan["id"])) or plan
    steps = current.get("steps") or []
    target = next(
        (
            step
            for step in steps
            if str(step.get("assignedAgentId") or "") == assigned_agent_id
            and step.get("status") not in {"completed", "blocked", "skipped"}
        ),
        None,
    )
    if not target:
        return current

    now = _utc_now()
    update_plan_step_record(
        str(target["id"]),
        status=status,
        result=result[:2000],
        completed_at=now if status == "completed" else None,
    )
    updated = get_plan_record(str(plan["id"])) or current
    updated_steps = updated.get("steps") or []
    active = next((step for step in updated_steps if step.get("status") == "active"), None)
    if active and active.get("status") in {"completed", "blocked", "skipped"}:
        active = None
    if not active:
        next_step = next((step for step in updated_steps if step.get("status") == "pending"), None)
        if next_step:
            update_plan_step_record(str(next_step["id"]), status="active", started_at=now)
            updated = update_plan_record(str(plan["id"]), current_step_id=str(next_step["id"])) or updated
        else:
            updated = update_plan_record(str(plan["id"]), current_step_id="") or updated
    updated = get_plan_record(str(plan["id"])) or updated
    updated["markdown"] = render_plan_markdown(updated)
    updated = update_plan_record(str(plan["id"]), markdown=updated["markdown"]) or updated
    append_plan_event_record(
        str(plan["id"]),
        step_id=str(target["id"]),
        event_type="PLAN_STEP_UPDATED_BY_TEAM_WORKER",
        content=result[:2000],
    )
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
