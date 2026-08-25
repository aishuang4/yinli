"""定时任务可行性门禁。不写库、不调模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.scheduler.capability_catalog import (
    CapabilityInventoryItem,
    build_agent_capability_inventory,
)
from src.scheduler.capability_contract import (
    JobCapabilityClaim,
    JobCapabilityContract,
    JobCapabilityGap,
)
from src.scheduler.capability_match import (
    CHANNEL_INTENTS,
    SEND_USER_TOKENS,
    claim_capabilities,
    extract_required_intents,
)
from src.scheduler.job_toolkit import (
    JOB_ROLE,
    agent_role_of,
    intents_covered_by_claims,
    job_toolkit_of,
    optional_inventory,
    resolve_required_layer,
)
from src.scheduler.unattended_policy import agent_approval_policy, skill_package_has_scripts
from src.security.permissions import ApprovalPolicy, build_permission_profile
from src.storage.database import list_agents

_ALWAYS_PREVIEW = [
    "Read", "Grep", "Glob", "Write", "Edit",
    "FinalAnswerStart", "MemoryNodeTree", "MemoryNodeQuery",
]


class FeasibilityResult(BaseModel):
    """门禁结果；公开 JSON 一律 camelCase。"""

    verdict: Literal["ok", "partial", "impossible"]
    agent_id: str = Field(alias="agentId")
    workspace_id: str = Field(alias="workspaceId")
    project_id: str = Field(alias="projectId")
    task_intent: str = Field(alias="taskIntent")
    required_intents: list[str] = Field(alias="requiredIntents")
    claimed: list[JobCapabilityClaim]
    missing: list[JobCapabilityGap]
    blocked_by_permission: list[JobCapabilityGap] = Field(alias="blockedByPermission")
    downgrade_offer: dict[str, Any] | None = Field(default=None, alias="downgradeOffer")
    assembled_tool_scope: dict[str, bool] = Field(alias="assembledToolScope")
    allow_http_request: bool = Field(default=False, alias="allowHttpRequest")
    allow_web_fetch: bool = Field(default=False, alias="allowWebFetch")
    allow_bash: bool = Field(default=False, alias="allowBash")
    firing_tools_preview: list[str] = Field(alias="firingToolsPreview")
    unattended_ok: bool = Field(alias="unattendedOk")
    message: str
    suggested_agent_ids: list[str] = Field(default_factory=list, alias="suggestedAgentIds")

    model_config = {"populate_by_name": True}

    def to_public_json(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True)


def evaluate_job_feasibility(
    *,
    task_intent: str,
    agent_id: str,
    workspace_id: str,
    project_id: str,
    required_intents: tuple[str, ...] | list[str] = (),
    claimed_must_include: frozenset[str] | None = None,
    include_suggestions: bool = True,
) -> FeasibilityResult:
    """纯函数式门禁。不写库。不调模型。

    Job Agent：第一段检查必注入资格，第二段只在可选名单里认领。
    对话 Agent 不能作为到期执行者。
    """

    intent_text = str(task_intent or "")
    required = extract_required_intents(intent_text, required_intents)
    inventory = build_agent_capability_inventory(agent_id, project_id, workspace_id)
    from src.server.routes.agents import get_registered_agent

    agent = get_registered_agent(agent_id) if agent_id else None
    profile = build_permission_profile(getattr(agent, "guardrails", None) if agent else None)
    read_only = profile.is_read_only
    role = agent_role_of(agent)
    toolkit = job_toolkit_of(agent)

    if role != JOB_ROLE:
        return FeasibilityResult(
            verdict="impossible",
            agent_id=agent_id,
            workspace_id=workspace_id,
            project_id=project_id,
            task_intent=intent_text,
            required_intents=required,
            claimed=[],
            missing=[JobCapabilityGap(
                intent="job.agent",
                reason="not_job_agent",
                suggested_action="请选择一个 Job Agent 来执行定时任务",
            )],
            blocked_by_permission=[],
            assembled_tool_scope={
                "allowBash": False,
                "allowHttp": False,
                "allowWeb": False,
                "allowMcp": False,
                "allowKnowledge": False,
            },
            firing_tools_preview=list(_ALWAYS_PREVIEW),
            unattended_ok=False,
            message="定时任务必须由 Job Agent 执行。对话助手不能到点无人值守运行。",
            suggested_agent_ids=_suggest_agents(
                current_agent_id=agent_id,
                workspace_id=workspace_id,
                project_id=project_id,
                required=required,
            ) if include_suggestions else [],
        )

    required_claims, required_missing = resolve_required_layer(toolkit, inventory)
    if required_missing or not toolkit.required:
        message = "这个 Job Agent 的必注入工具当前不可无人值守使用，不能创建或启用任务。"
        if not toolkit.required:
            message = "Job Agent 必须配置必注入工具，不能把保底能力交给门禁裁剪。"
        return FeasibilityResult(
            verdict="impossible",
            agent_id=agent_id,
            workspace_id=workspace_id,
            project_id=project_id,
            task_intent=intent_text,
            required_intents=required,
            claimed=required_claims,
            missing=required_missing or [JobCapabilityGap(
                intent="job.required",
                reason="empty_required",
                suggested_action="在 Agent 配置里勾选至少一项必注入工具",
            )],
            blocked_by_permission=[],
            assembled_tool_scope={
                "allowBash": False,
                "allowHttp": False,
                "allowWeb": False,
                "allowMcp": False,
                "allowKnowledge": False,
            },
            firing_tools_preview=list(_ALWAYS_PREVIEW),
            unattended_ok=False,
            message=message,
            suggested_agent_ids=[],
        )

    covered = intents_covered_by_claims(required_claims)
    remaining_intents = [
        item for item in required
        if item not in covered and item not in {"file.read", "file.write", "office.generate"}
    ]
    optional_rows = optional_inventory(toolkit, inventory)
    claimed_optional, missing, blocked = claim_capabilities(
        remaining_intents, optional_rows, read_only=read_only,
    )
    claimed_optional = [
        item.model_copy(update={"layer": "optional"}) for item in claimed_optional
    ]
    claimed = [*required_claims, *claimed_optional]
    if any("file.write" in item.tags for item in claimed):
        missing = [item for item in missing if item.intent != "office.generate"]
    if claimed_must_include:
        live_ids = {item.capability_id for item in claimed if item.capability_id}
        inventory_ok = {
            item.capability_id for item in inventory if item.unattended_ok
        }
        for cap_id in claimed_must_include:
            if cap_id not in live_ids and cap_id not in inventory_ok:
                missing.append(JobCapabilityGap(
                    intent=cap_id,
                    reason="claimed_unbound",
                    suggested_action="合同认领的可选工具已不可无人值守使用",
                ))

    channel_required = [item for item in remaining_intents if item in CHANNEL_INTENTS]
    channel_satisfied = _channel_intents_satisfied(channel_required, claimed_optional)
    send_only = _is_send_only(intent_text)
    can_write = (not read_only) and (
        "file.write" in required or "office.generate" in required or not send_only
        or "file.write" in covered
    )

    if read_only and ("file.write" in required or "office.generate" in required):
        verdict: Literal["ok", "partial", "impossible"] = "impossible"
    elif channel_required and not channel_satisfied:
        if send_only:
            verdict = "impossible"
        else:
            verdict = "partial" if can_write else "impossible"
    elif missing or blocked:
        verdict = "partial" if can_write else "impossible"
    else:
        verdict = "ok"

    flags = _three_flags(required, intent_text, inventory, claimed)
    if any(item.resource_id == "WebFetch" for item in claimed):
        flags["allow_web_fetch"] = True
    if any(item.resource_id == "HttpRequest" for item in claimed):
        flags["allow_http_request"] = True
    if any(item.resource_id == "Bash" or item.kind == "skill" for item in claimed):
        flags["allow_bash"] = flags["allow_bash"] or any(
            item.resource_id == "Bash" or (item.kind == "skill" and "shell.execute" in item.tags)
            for item in claimed
        )
    scope = {
        "allowBash": flags["allow_bash"],
        "allowHttp": flags["allow_http_request"],
        "allowWeb": flags["allow_web_fetch"],
        "allowMcp": any(item.kind == "mcp" for item in claimed),
        "allowKnowledge": any(item.kind in {"http", "knowledge"} for item in claimed),
    }
    preview = list(_ALWAYS_PREVIEW)
    if flags["allow_bash"] and "Bash" not in preview:
        preview.append("Bash")
    if flags["allow_http_request"] and "HttpRequest" not in preview:
        preview.append("HttpRequest")
    if flags["allow_web_fetch"] and "WebFetch" not in preview:
        preview.append("WebFetch")
    for item in claimed:
        if item.kind != "builtin" and item.tool_name and item.tool_name not in preview:
            preview.append(item.tool_name)

    downgrade = None
    if verdict == "partial":
        dropped = [item.intent for item in missing + blocked]
        downgrade = {
            "summary": "可以到点生成文件并通知你，但不能完成全部外发",
            "droppedIntents": dropped,
            "claimedIfAccepted": [item.capability_id for item in claimed],
        }

    message = _message(verdict, required, missing, blocked)
    suggested: list[str] = []
    if verdict != "ok" and include_suggestions:
        suggested = _suggest_agents(
            current_agent_id=agent_id,
            workspace_id=workspace_id,
            project_id=project_id,
            required=required,
        )

    return FeasibilityResult(
        verdict=verdict,
        agent_id=agent_id,
        workspace_id=workspace_id,
        project_id=project_id,
        task_intent=intent_text,
        required_intents=required,
        claimed=claimed,
        missing=missing,
        blocked_by_permission=blocked,
        downgrade_offer=downgrade,
        assembled_tool_scope=scope,
        allow_http_request=flags["allow_http_request"],
        allow_web_fetch=flags["allow_web_fetch"],
        allow_bash=flags["allow_bash"],
        firing_tools_preview=preview,
        unattended_ok=verdict == "ok",
        message=message,
        suggested_agent_ids=suggested,
    )


def _channel_intents_satisfied(
    channel_required: list[str],
    claimed: list[JobCapabilityClaim],
) -> bool:
    claimed_tags = {tag for item in claimed for tag in item.tags}
    return all(intent in claimed_tags for intent in channel_required)


def _is_send_only(task_intent: str) -> bool:
    text = str(task_intent or "")
    has_send_only = any(token in text for token in ("只发送", "不要生成", "不要写文件"))
    has_generate_doc = any(token in text for token in ("周报", "日报", "纪要", "生成文档"))
    return has_send_only and not has_generate_doc


def _three_flags(
    required: list[str],
    task_intent: str,
    inventory: list[CapabilityInventoryItem],
    claimed: list[JobCapabilityClaim],
) -> dict[str, bool]:
    by_id = {item.capability_id: item for item in inventory}
    send_hit = any(token in task_intent for token in SEND_USER_TOKENS)
    http_ok = bool(
        by_id.get("builtin:HttpRequest") and by_id["builtin:HttpRequest"].unattended_ok
    )
    web_ok = bool(by_id.get("builtin:WebFetch") and by_id["builtin:WebFetch"].unattended_ok)
    bash_ok = bool(by_id.get("builtin:Bash") and by_id["builtin:Bash"].unattended_ok)
    skill_needs_bash = any(
        item.kind == "skill" and "shell.execute" in item.tags for item in claimed
    )
    return {
        "allow_http_request": (
            "http.call" in required and http_ok and not send_hit
        ),
        "allow_web_fetch": "web.fetch" in required and web_ok,
        "allow_bash": ("shell.execute" in required or skill_needs_bash) and bash_ok,
    }


def _message(
    verdict: str,
    required: list[str],
    missing: list[JobCapabilityGap],
    blocked: list[JobCapabilityGap],
) -> str:
    if verdict == "ok":
        return "当前 Job Agent 可以完成这个定时任务；必注入工具会保留。"
    if "email.send" in required:
        if verdict == "impossible":
            return "我不能无人值守发邮件，这个定时任务做不到。"
        return "我不能无人值守发邮件，只能降级为到点生成文件并通知你。"
    if "web.fetch" in required:
        if verdict == "impossible":
            return "我不能在定时任务里查天气或访问网页，当前 Agent 没有无人值守的网络能力。"
        return "我不能在定时任务里访问网页，只能降级为到点写本地文件。"
    if verdict == "impossible":
        return "当前 Agent 没有完成该定时任务的无人值守能力。"
    return "当前 Agent 不能完成全部外发，只能降级生成。"


def _suggest_agents(
    *,
    current_agent_id: str,
    workspace_id: str,
    project_id: str,
    required: list[str],
) -> list[str]:
    try:
        agents = list_agents()
    except Exception:
        return []
    hits: list[str] = []
    scanned = 0
    for record in agents:
        agent_id = str(record.get("id") or "")
        if not agent_id or agent_id == current_agent_id:
            continue
        if agent_role_of(record) != JOB_ROLE:
            continue
        scanned += 1
        if scanned > 20:
            break
        try:
            other = evaluate_job_feasibility(
                task_intent=" ".join(required),
                agent_id=agent_id,
                workspace_id=workspace_id,
                project_id=project_id,
                required_intents=tuple(required),
                include_suggestions=False,
            )
        except Exception:
            continue
        if other.verdict == "ok":
            hits.append(agent_id)
        if len(hits) >= 3:
            break
    return hits


def contract_from_feasibility(result: FeasibilityResult) -> JobCapabilityContract:
    """把门禁结果写成可持久化合同。impossible 不得调用。"""

    verdict: Literal["ok", "partial", "generate_only"]
    if result.verdict == "ok":
        verdict = "ok"
    elif result.verdict == "partial":
        verdict = "partial"
    else:
        verdict = "generate_only"
    return JobCapabilityContract(
        verdict=verdict,
        task_intent=result.task_intent,
        required_intents=result.required_intents,
        claimed=result.claimed,
        missing=result.missing,
        blocked_by_permission=result.blocked_by_permission,
        accepted_partial=result.verdict == "partial",
        allow_http_request=result.allow_http_request,
        allow_web_fetch=result.allow_web_fetch,
        allow_bash=result.allow_bash,
        assembled_tool_scope=result.assembled_tool_scope,
        skill_ids=[item.resource_id for item in result.claimed if item.kind == "skill"],
    )
