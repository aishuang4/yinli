"""Team Worker report 质量检查。

当前只做轻量质量闸门：判断 Worker report 是否足够给 Leader 汇总，并把缺口
写回 TeamActivity。暂不做二次 TeamSay 派发。
"""

import json
import re
from dataclasses import dataclass
from hashlib import sha256

from agentscope.message import UserMsg
from agentscope.permission import PermissionMode

from src.agents.factory import AgentGuardrails, AgentModelOptions, RuntimeToolScope, get_or_create_agent
from src.server.routes.chat.models import ChatTeamActivity
from src.server.routes.team_defaults import DEFAULT_TEAM_REPORT_REVIEWER_PROMPT


@dataclass(frozen=True)
class TeamReportReview:
    """单个 Worker report 的质量判断。"""

    agent_id: str
    status: str
    gaps: tuple[str, ...] = ()
    notes: str = ""


async def review_team_worker_reports(
    *,
    message: str,
    team_name: str | None,
    activities: list[ChatTeamActivity],
    model_name: str,
) -> dict[str, TeamReportReview]:
    """检查 Worker reports 是否足够支撑 Leader 汇总。"""

    workers = [
        activity
        for activity in activities
        if activity.role != "leader"
        and activity.status in {"completed", "failed", "waiting"}
    ]
    if not workers:
        return {}

    try:
        reviews = await _review_with_model(
            message=message,
            team_name=team_name,
            workers=workers,
            model_name=model_name,
        )
    except Exception:
        reviews = {}

    if set(reviews) >= {worker.agent_id for worker in workers}:
        return reviews
    return {
        **_fallback_reviews(workers),
        **reviews,
    }


async def _review_with_model(
    *,
    message: str,
    team_name: str | None,
    workers: list[ChatTeamActivity],
    model_name: str,
) -> dict[str, TeamReportReview]:
    """调用内置 reviewer Agent 输出质量检查 JSON。"""

    request_fingerprint = "\n".join([
        team_name or "",
        message,
        "\n".join(f"{item.agent_id}:{item.assignment}:{item.report}:{item.status}" for item in workers),
    ])
    prompt_hash = sha256(DEFAULT_TEAM_REPORT_REVIEWER_PROMPT.encode("utf-8")).hexdigest()[:12]
    request_hash = sha256(request_fingerprint.encode("utf-8")).hexdigest()[:12]
    agent = get_or_create_agent(
        agent_id=f"system:team-report-reviewer:{model_name}:prompt-{prompt_hash}:request-{request_hash}",
        name="TeamReportReviewer",
        system_prompt=DEFAULT_TEAM_REPORT_REVIEWER_PROMPT,
        model_name=model_name,
        thinking_enable=False,
        permission_mode=PermissionMode.BYPASS,
        model_options=AgentModelOptions(max_tokens=1600, max_react_iters=2),
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
        include_default_tools=False,
    )
    worker_blocks = []
    for item in workers:
        worker_blocks.append("\n".join([
            f"agentId={item.agent_id}",
            f"name={item.agent_name}",
            f"status={item.status}",
            f"assignment={item.assignment or item.task}",
            f"expectedOutput={item.expected_output}",
            f"constraints={item.constraints}",
            f"report={item.report or '(empty)'}",
            f"detail={item.detail}",
        ]))
    prompt = "\n\n".join([
        f"Team: {team_name or 'Unnamed Team'}",
        "Original user task:",
        message,
        "Worker reports:",
        "\n\n---\n\n".join(worker_blocks),
    ])

    text = ""
    async for event in agent.reply_stream(UserMsg(name="user", content=prompt)):
        event_data = event.model_dump(mode="json")
        event_type = event_data.pop("type", "UNKNOWN")
        if event_type == "TEXT_BLOCK_DELTA":
            text += str(event_data.get("delta", ""))

    return parse_team_report_review_json(text, {worker.agent_id for worker in workers})


def parse_team_report_review_json(
    text: str,
    allowed_agent_ids: set[str],
) -> dict[str, TeamReportReview]:
    """解析 report review JSON。"""

    raw = _extract_json_object(text)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}

    items = data.get("reviews") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}

    reviews: dict[str, TeamReportReview] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("agentId") or item.get("agent_id") or "").strip()
        if agent_id not in allowed_agent_ids or agent_id in reviews:
            continue
        raw_status = str(item.get("status") or "").strip()
        status = raw_status if raw_status in {"accepted", "needs_revision"} else "needs_revision"
        gaps = tuple(
            str(gap).strip()[:300]
            for gap in (item.get("gaps") or [])
            if str(gap).strip()
        )[:5]
        reviews[agent_id] = TeamReportReview(
            agent_id=agent_id,
            status=status,
            gaps=gaps,
            notes=str(item.get("notes") or "").strip()[:800],
        )
    return reviews


def _extract_json_object(text: str) -> str:
    """从模型文本中提取首个 JSON 对象。"""

    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.IGNORECASE).strip()
        raw = re.sub(r"```$", "", raw).strip()
    if "{" not in raw or "}" not in raw:
        return ""
    return raw[raw.find("{"): raw.rfind("}") + 1]


def _fallback_reviews(
    workers: list[ChatTeamActivity],
) -> dict[str, TeamReportReview]:
    """模型 reviewer 不可用时的基础质量规则。"""

    reviews: dict[str, TeamReportReview] = {}
    for worker in workers:
        report = " ".join((worker.report or "").split())
        gaps: list[str] = []
        if worker.status in {"failed", "waiting"}:
            gaps.append(f"Worker 状态为 {worker.status}，结果可能不完整。")
        if len(report) < 40:
            gaps.append("Worker report 过短或为空。")
        if worker.expected_output and worker.expected_output[:20] not in report and len(report) < 120:
            gaps.append("report 未充分回应期望输出。")
        reviews[worker.agent_id] = TeamReportReview(
            agent_id=worker.agent_id,
            status="needs_revision" if gaps else "accepted",
            gaps=tuple(gaps),
            notes="; ".join(gaps) if gaps else "report 可用于 Leader 汇总。",
        )
    return reviews
