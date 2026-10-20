"""scheduled firing 的 preloaded-only Skill 首轮准备与运行时审计。

固定首轮顺序，任一步失败都不得调用模型：

1. preflight：`build_scheduled_run_context` 已按冻结 manifest 校验 live catalog 与 SKILL.md hash；
2. toolkit：本次 Agent 必须是 `scheduled_preloaded` Toolkit，模型可见 schema 中没有 `Skill` / `SkillViewer`；
3. run_snapshot：绑定本 Run 的冻结资源 snapshot，fresh Run Context 不得带入旧消息、摘要或旧激活；
4. skill_activation：`activate_forced_skills` 把每个 required Skill 正文写成 `<activated-skill>` HintBlock；
5. context_verification：逐个核对 `<activated-skill>` 块的 ID、hash 与正文；
6. job_prompt：随后追加冻结 Job prompt，并发起首次模型调用。

运行时审计只记录 ID、hash、注入顺序、token 估算、预算、恢复结果和被拒 viewer 调用，
不保存 Skill 正文。审计挂在 `agent._aw_skill_context` 上随每次模型调用写入 trace，
并按 firing ID 暂存，供调度器在 Run 结束后写入 firing 记录。
"""

from __future__ import annotations

import copy
import hashlib
import html
import json
import re
import threading
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any

from src.agents.runtime_types import SKILL_LOADING_SCHEDULED
from src.agents.skill_toolkit import (
    SKILL_VIEWER_TOOL_NAMES,
    requested_skill_from_input,
    toolkit_skill_loading_mode,
)
from src.server.routes.chat.skill_context_middleware import (
    ScheduledSkillContextError,
    activate_forced_skills,
    activated_skills,
)

SCHEDULED_FIRST_TURN_STEPS = (
    "preflight",
    "toolkit",
    "run_snapshot",
    "skill_activation",
    "context_verification",
    "job_prompt",
)

_BLOCK_RE = re.compile(r"<activated-skill\b([^>]*)>(.*?)</activated-skill>", re.DOTALL)
_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
# 按 firing 暂存的审计数量上限；正常路径由调度器在 Run 结束后取走。
_MAX_PENDING_AUDITS = 256
_PENDING_AUDITS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_PENDING_LOCK = threading.Lock()


class ScheduledSkillPreloadError(ScheduledSkillContextError):
    """首轮预加载任一步失败；`code` 写入 firing error_code 与 Skill Context 审计。"""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _required_skills(forced_skills: Iterable[Any]) -> list[Any]:
    return [skill for skill in forced_skills or [] if bool(getattr(skill, "required_skill", False))]


def _budget(preflight: dict[str, Any]) -> dict[str, Any]:
    from src.scheduler.skill_manifest import (
        SINGLE_SKILL_MAX_CHARS,
        TOTAL_SKILL_MAX_CHARS,
    )

    items = [item for item in preflight.get("items") or [] if isinstance(item, dict)]
    return {
        "singleSkillMaxChars": SINGLE_SKILL_MAX_CHARS,
        "totalMaxChars": TOTAL_SKILL_MAX_CHARS,
        "totalChars": int(preflight.get("totalChars") or sum(int(item.get("contentChars") or 0) for item in items)),
        "estimatedTokens": sum(int(item.get("estimatedTokens") or 0) for item in items),
    }


def build_scheduled_skill_audit(ctx: Any) -> dict[str, Any]:
    """在 preflight 摘要上补齐 firing 身份、预算与运行时字段；不含正文。"""

    preflight = copy.deepcopy(dict(getattr(ctx, "skill_context_audit", None) or {}))
    runtime_context = getattr(ctx, "runtime_context", None)
    return {
        **preflight,
        "mode": SKILL_LOADING_SCHEDULED,
        "jobId": str(getattr(ctx, "scheduled_job_id", "") or ""),
        "firingId": str(getattr(ctx, "scheduled_firing_id", "") or ""),
        "ownerSessionId": str(getattr(ctx, "session_id", "") or ""),
        "firingRunId": str(getattr(runtime_context, "run_id", "") or ""),
        "preflightValidation": str(preflight.get("validation") or ""),
        "budget": _budget(preflight),
        "firstTurnSteps": [],
        "injectionOrder": [],
        "reinjections": [],
        "rejectedViewerCalls": [],
    }


def _assert_preflight(preflight: dict[str, Any], required: list[Any]) -> None:
    """每个待预加载的 required Skill 都必须有一条通过 preflight 的冻结 hash 记录。"""

    if not required:
        return
    if str(preflight.get("validation") or "") != "passed":
        raise ScheduledSkillPreloadError(
            "skill_metadata_missing",
            "required Skill 未通过 preflight/hash 校验，不能预加载",
        )
    validated = {
        str(item.get("contentSha256") or "")
        for item in preflight.get("items") or []
        if isinstance(item, dict) and item.get("validated")
    }
    for skill in required:
        if _frozen_hash(skill) not in validated:
            raise ScheduledSkillPreloadError(
                "skill_metadata_missing",
                f"required Skill {getattr(skill, 'id', '')} 缺少 preflight 校验记录",
            )


def _frozen_hash(skill: Any) -> str:
    return str(getattr(skill, "frozen_sha256", "") or getattr(skill, "content_hash", "") or "")


async def assert_scheduled_toolkit(agent: Any) -> dict[str, Any]:
    """scheduled Toolkit 断言：模式正确、viewer 未注册、模型可见 schema 中没有任何 viewer。"""

    toolkit = getattr(agent, "toolkit", None)
    if toolkit is None:
        raise ScheduledSkillPreloadError("skill_toolkit_invalid", "scheduled firing 缺少 Toolkit")
    mode = toolkit_skill_loading_mode(toolkit)
    if mode != SKILL_LOADING_SCHEDULED or getattr(agent, "_aw_skill_loading_mode", "") != SKILL_LOADING_SCHEDULED:
        raise ScheduledSkillPreloadError("skill_toolkit_invalid", f"scheduled firing 拿到了 {mode} Toolkit")
    if getattr(toolkit, "builtin_skill_viewer", None) is not None:
        raise ScheduledSkillPreloadError("skill_toolkit_invalid", "scheduled Toolkit 仍注册了内置 Skill viewer")
    all_groups = [group.name for group in getattr(toolkit, "tool_groups", []) or []]
    if await toolkit._get_available_skills(all_groups):
        raise ScheduledSkillPreloadError("skill_toolkit_invalid", "scheduled Toolkit 仍向 viewer 暴露可加载 Skill")
    preloaded = await toolkit.list_preloaded_skills(all_groups) if hasattr(toolkit, "list_preloaded_skills") else []
    summary: dict[str, Any] = {
        "mode": mode,
        "viewerRegistered": False,
        "mountedSkillIds": [str(getattr(skill, "id", "") or getattr(skill, "name", "")) for skill in preloaded],
    }
    state = getattr(agent, "state", None)
    activated_groups = list(getattr(getattr(state, "tool_context", None), "activated_groups", None) or [])
    schemas = await toolkit.get_tool_schemas(activated_groups)
    names = sorted(str((schema.get("function") or {}).get("name") or "") for schema in schemas)
    exposed = sorted(set(names) & SKILL_VIEWER_TOOL_NAMES)
    if exposed:
        raise ScheduledSkillPreloadError(
            "skill_toolkit_invalid",
            f"scheduled 模型可见工具中出现 Skill viewer: {', '.join(exposed)}",
        )
    summary["toolNames"] = names
    summary["toolSchemaSha256"] = _sha256_text(json.dumps(schemas, ensure_ascii=False, sort_keys=True, default=str))
    return summary


def restore_scheduled_run_snapshot(
    agent: Any,
    *,
    session_history: Iterable[Any],
    run_snapshot: dict[str, Any] | None,
    policy: str,
) -> dict[str, Any]:
    """fresh firing：绑定本 Run 冻结 snapshot，并确认 Run Context 在预加载前是全新的。"""

    if policy != "fresh":
        raise ScheduledSkillPreloadError("skill_context_policy_unsupported", f"不支持的 Job context_policy: {policy}")
    state = getattr(agent, "state", None)
    context = list(getattr(state, "context", None) or [])
    if context or str(getattr(state, "summary", "") or ""):
        raise ScheduledSkillPreloadError(
            "skill_context_not_fresh",
            f"fresh firing 的 Run Context 在预加载前已有 {len(context)} 条消息或压缩摘要",
        )
    if activated_skills(agent):
        raise ScheduledSkillPreloadError("skill_context_not_fresh", "fresh firing 在预加载前已存在 Skill 激活记录")
    if list(session_history or []):
        raise ScheduledSkillPreloadError("skill_context_not_fresh", "fresh firing 不得继承 Session 历史消息")
    summary: dict[str, Any] = {}
    if run_snapshot:
        from src.sessions.run_binding import snapshot_audit_summary

        summary = snapshot_audit_summary(run_snapshot)
    return {
        "policy": policy,
        "freshContext": True,
        "seededMessages": 0,
        "snapshotId": str(summary.get("snapshotId") or ""),
        "resourceGrantVersion": summary.get("resourceGrantVersion"),
        "directoryGrantVersion": summary.get("directoryGrantVersion"),
    }


def activated_skill_blocks(agent: Any) -> list[dict[str, str]]:
    """按出现顺序解析 context 中系统生成的 `<activated-skill>` HintBlock。"""

    blocks: list[dict[str, str]] = []
    state = getattr(agent, "state", None)
    for message in list(getattr(state, "context", None) or []):
        if getattr(message, "role", "") != "assistant":
            continue
        for block in getattr(message, "content", None) or []:
            if getattr(block, "type", "") != "hint":
                continue
            hint = getattr(block, "hint", "")
            texts = [hint] if isinstance(hint, str) else [
                str(getattr(item, "text", "") or "") for item in hint or [] if getattr(item, "type", "") == "text"
            ]
            for text in texts:
                for match in _BLOCK_RE.finditer(text):
                    attrs = {key: html.unescape(value) for key, value in _ATTR_RE.findall(match.group(1))}
                    blocks.append({
                        "id": attrs.get("id", ""),
                        "name": attrs.get("name", ""),
                        "sha256": attrs.get("sha256", ""),
                        "body": match.group(2).strip(),
                    })
    return blocks


def verify_required_skill_context(agent: Any, forced_skills: Iterable[Any]) -> dict[str, Any]:
    """每个 required Skill 必须恰好一个 `<activated-skill>` 块，ID、冻结 hash 与正文完全一致。"""

    required = _required_skills(forced_skills)
    blocks = activated_skill_blocks(agent)
    expected_ids = [str(getattr(skill, "id", "") or "") for skill in required]
    unexpected = [block["id"] or block["name"] for block in blocks if block["id"] not in expected_ids]
    if unexpected:
        raise ScheduledSkillPreloadError(
            "skill_context_injection_failed",
            f"Run Context 出现非 required 的 <activated-skill>: {', '.join(unexpected)}",
        )
    verified: list[dict[str, Any]] = []
    for skill in required:
        skill_id = str(getattr(skill, "id", "") or "")
        found = [block for block in blocks if block["id"] == skill_id]
        if len(found) != 1:
            raise ScheduledSkillPreloadError(
                "skill_context_injection_failed",
                f"required Skill {skill_id} 期望恰好 1 个 <activated-skill> 块，实际 {len(found)} 个",
            )
        block = found[0]
        frozen = _frozen_hash(skill)
        if not frozen or block["sha256"] != frozen or str(getattr(skill, "content_hash", "") or frozen) != frozen:
            raise ScheduledSkillPreloadError(
                "skill_context_hash_mismatch",
                f"required Skill {skill_id} 的 <activated-skill> hash 与冻结版本不一致",
            )
        expected_body = str(getattr(skill, "content", "") or "").strip()
        if not expected_body or block["body"] != expected_body:
            raise ScheduledSkillPreloadError(
                "skill_context_injection_failed",
                f"required Skill {skill_id} 的 <activated-skill> 正文与冻结版本不一致",
            )
        verified.append({
            "skillId": skill_id,
            "sha256": block["sha256"],
            "bodyChars": len(block["body"]),
            "bodySha256": _sha256_text(block["body"]),
        })
    return {
        "status": "passed",
        "blocks": verified,
        "injectionOrder": [block["id"] for block in blocks],
    }


def _fail(audit: dict[str, Any], exc: ScheduledSkillContextError) -> None:
    steps = list(audit.get("firstTurnSteps") or [])
    remaining = [step for step in SCHEDULED_FIRST_TURN_STEPS if step not in steps]
    audit.update({
        "validation": "failed",
        "errorCode": exc.code,
        "errorMessage": exc.message,
        "failedStep": remaining[0] if remaining else "model_call",
    })


async def prepare_scheduled_first_turn(
    agent: Any,
    ctx: Any,
    *,
    run_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    """按固定顺序完成 scheduled firing 首轮 Skill 预加载；返回（并挂到 agent 上的）运行时审计。

    任何异常都统一转成 `ScheduledSkillPreloadError`（保留 `skill_*` 错误码），调用方据此不调用模型。
    """

    audit = build_scheduled_skill_audit(ctx)
    agent._aw_skill_context = audit
    forced_skills = list(getattr(ctx, "forced_skills", None) or [])
    required = _required_skills(forced_skills)
    steps: list[str] = audit["firstTurnSteps"]
    try:
        _assert_preflight(audit, required)
        steps.append("preflight")
        audit["toolkit"] = await assert_scheduled_toolkit(agent)
        # 被拒 viewer 调用直接引用 Toolkit 的列表，trace 与 firing 审计看到同一份记录。
        rejected = getattr(getattr(agent, "toolkit", None), "rejected_viewer_calls", None)
        if isinstance(rejected, list):
            audit["rejectedViewerCalls"] = rejected
        steps.append("toolkit")
        audit["runSnapshot"] = restore_scheduled_run_snapshot(
            agent,
            session_history=getattr(ctx, "session_history", None) or [],
            run_snapshot=run_snapshot,
            policy=str(audit.get("policy") or "fresh"),
        )
        steps.append("run_snapshot")
        activated = activate_forced_skills(agent, forced_skills)
        if len(activated) != len(forced_skills):
            raise ScheduledSkillPreloadError(
                "skill_context_injection_failed",
                f"required Skill 只激活了 {len(activated)}/{len(forced_skills)} 个",
            )
        steps.append("skill_activation")
        verification = verify_required_skill_context(agent, forced_skills)
        audit["contextVerification"] = {"status": verification["status"], "blocks": verification["blocks"]}
        audit["injectionOrder"] = verification["injectionOrder"]
        steps.append("context_verification")
    except ScheduledSkillPreloadError as exc:
        _fail(audit, exc)
        raise
    except Exception as exc:  # 首轮前任何异常都必须阻止模型调用
        # SkillError / ScheduledSkillContextError 自带 skill_* 错误码（如 skill_version_changed）。
        code = str(getattr(exc, "code", "") or "")
        error = ScheduledSkillPreloadError(
            code if code.startswith("skill_") else "skill_preload_failed",
            str(getattr(exc, "message", "") or exc) or type(exc).__name__,
        )
        _fail(audit, error)
        raise error from exc
    audit["validation"] = "passed" if required else str(audit.get("preflightValidation") or "legacy")
    return audit


def mark_scheduled_job_prompt(agent: Any) -> None:
    """Job prompt 已构造、即将进入首次模型调用。"""

    audit = getattr(agent, "_aw_skill_context", None)
    if (
        isinstance(audit, dict)
        and isinstance(audit.get("firstTurnSteps"), list)
        and "job_prompt" not in audit["firstTurnSteps"]
    ):
        audit["firstTurnSteps"].append("job_prompt")


def _viewer_tool_calls(agent: Any) -> list[dict[str, str]]:
    """按时间顺序取出 context 中模型发出的 viewer 调用（ID 与请求的 Skill）。"""

    calls: list[dict[str, str]] = []
    state = getattr(agent, "state", None)
    for message in list(getattr(state, "context", None) or []):
        if getattr(message, "role", "") != "assistant":
            continue
        for block in getattr(message, "content", None) or []:
            if getattr(block, "type", "") == "tool_call" and getattr(block, "name", "") in SKILL_VIEWER_TOOL_NAMES:
                calls.append({
                    "toolCallId": str(getattr(block, "id", "") or ""),
                    "requestedSkill": requested_skill_from_input(getattr(block, "input", None)),
                })
    return calls


def _enrich_rejected_viewer_calls(agent: Any, audit: dict[str, Any]) -> None:
    """工具层拒绝时只知道工具名；用 context 中的调用块补齐 toolCallId 与请求的 Skill。"""

    records = audit.get("rejectedViewerCalls")
    if not isinstance(records, list):
        return
    pending = [record for record in records if isinstance(record, dict) and not record.get("toolCallId")]
    if not pending:
        return
    known = {record.get("toolCallId") for record in records if isinstance(record, dict) and record.get("toolCallId")}
    calls = [call for call in _viewer_tool_calls(agent) if call["toolCallId"] not in known]
    # 压缩可能丢掉更早的调用块，按“最近的拒绝对应最近的调用”对齐。
    for record, call in zip(reversed(pending), reversed(calls)):
        record.update({key: value for key, value in call.items() if value})


def publish_scheduled_skill_audit(firing_id: str, agent: Any) -> None:
    """Run 结束（成功或失败）时按 firing 暂存一份 JSON 安全、不含正文的审计副本。"""

    audit = getattr(agent, "_aw_skill_context", None)
    if not firing_id or not isinstance(audit, dict) or not audit:
        return
    _enrich_rejected_viewer_calls(agent, audit)
    snapshot = json.loads(json.dumps(audit, ensure_ascii=False, default=str))
    with _PENDING_LOCK:
        _PENDING_AUDITS[firing_id] = snapshot
        _PENDING_AUDITS.move_to_end(firing_id)
        while len(_PENDING_AUDITS) > _MAX_PENDING_AUDITS:
            _PENDING_AUDITS.popitem(last=False)


def pop_scheduled_skill_audit(firing_id: str) -> dict[str, Any] | None:
    """调度器在 Chat Run 终态后取走本 firing 的运行时 Skill Context 审计。"""

    if not firing_id:
        return None
    with _PENDING_LOCK:
        return _PENDING_AUDITS.pop(firing_id, None)
