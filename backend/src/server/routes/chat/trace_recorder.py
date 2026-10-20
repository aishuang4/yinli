"""Trace 记录 — 运行事件、fallback、workspace、AgentScope 事件与工具产物。

所有 trace 记录都不允许因可观测性失败而中断主执行流。
"""

import json
import re
from typing import Any

from src.agents.factory import AgentGuardrails
from src.security.permissions import (
    PermissionAction,
    PermissionDecision,
    ResourceType,
)
from src.storage.database import append_run_event

from src.sessions.files import is_user_facing_session_artifact
from src.server.routes.chat.helpers import _trim_text
from src.server.routes.chat.models import ChatModelCacheRun, ChatToolRun, TRACE_EVENT_TYPES
from src.server.routes.chat.tool_state import _tool_result_status


def _record_run_event(
    *,
    session_id: str,
    event_type: str,
    persist: bool,
    scope: str = "leader",
    agent_id: str | None = None,
    agent_name: str | None = None,
    status: str = "",
    title: str = "",
    content: str = "",
    payload: dict | None = None,
) -> None:
    """Persist trace events without allowing observability to break execution."""
    if not persist:
        return
    try:
        append_run_event(
            session_id=session_id,
            event_type=event_type,
            scope=scope,
            agent_id=agent_id,
            agent_name=agent_name,
            status=status,
            title=title,
            content=content,
            payload=payload or {},
        )
    except Exception as exc:
        print(f"[Trace] Failed to record run event {event_type}: {exc}")


def _make_fallback_trace_recorder(
    *,
    session_id: str,
    persist: bool,
    scope: str,
    agent_id: str,
    agent_name: str,
):
    """Create a callback that records application-level fallback attempts."""

    def record(event: dict) -> None:
        event_kind = str(event.get("type") or "attempt")
        model = str(event.get("model") or "")
        status = {
            "attempt": "running",
            "success": "completed",
            "failure": "failed",
        }.get(event_kind, "running")
        title = {
            "attempt": f"Trying model: {model}",
            "success": f"Model succeeded: {model}",
            "failure": f"Model failed: {model}",
        }.get(event_kind, f"Model fallback event: {model}")
        _record_run_event(
            session_id=session_id,
            event_type=f"MODEL_FALLBACK_{event_kind.upper()}",
            persist=persist,
            scope=scope,
            agent_id=agent_id,
            agent_name=agent_name,
            status=status,
            title=title,
            content=str(event.get("error") or ""),
            payload=event,
        )

    return record


def _workspace_trace_payload(guardrails: AgentGuardrails) -> dict:
    """Build trace payload for local/Docker execution workspace config."""
    return {
        "backend": guardrails.execution_backend,
        "filesystemScope": guardrails.filesystem_scope,
        "networkEnabled": guardrails.network_enabled,
        "toolPermissionProfile": guardrails.tool_permission_profile,
        "permissionProfile": guardrails.permission_profile.audit_payload(),
        "timeoutSeconds": guardrails.timeout_seconds,
        "dockerImage": guardrails.docker_image if guardrails.execution_backend == "docker" else "",
        "persistentSandbox": guardrails.persistent_sandbox if guardrails.execution_backend == "docker" else False,
    }


def _record_workspace_event(
    *,
    session_id: str,
    persist: bool,
    scope: str,
    agent_id: str,
    agent_name: str,
    guardrails: AgentGuardrails,
) -> None:
    payload = _workspace_trace_payload(guardrails)
    title = (
        f"Docker workspace: {guardrails.docker_image}"
        if guardrails.execution_backend == "docker"
        else "Local workspace"
    )
    _record_run_event(
        session_id=session_id,
        event_type="WORKSPACE_CONFIG",
        persist=persist,
        scope=scope,
        agent_id=agent_id,
        agent_name=agent_name,
        status="configured",
        title=title,
        content=(
            f"{payload['backend']} · scope={payload['filesystemScope']} · "
            f"profile={payload['toolPermissionProfile']}"
        ),
        payload=payload,
    )
    _record_run_event(
        session_id=session_id,
        event_type="PERMISSION_POLICY",
        persist=persist,
        scope=scope,
        agent_id=agent_id,
        agent_name=agent_name,
        status="configured",
        title="Permission policy configured",
        content=(
            f"policy={guardrails.permission_profile.tool_policy.value} · "
            f"network={guardrails.permission_profile.network_enabled}"
        ),
        payload={
            "policy": guardrails.permission_profile.audit_payload(),
            "legacy": {
                "toolPermissionMode": guardrails.tool_permission_mode,
                "toolPermissionProfile": guardrails.tool_permission_profile,
            },
        },
    )


def _record_permission_decision(
    *,
    session_id: str,
    event_type: str,
    event_data: dict,
    persist: bool,
    scope: str,
    agent_id: str,
    agent_name: str,
    guardrails: AgentGuardrails,
) -> None:
    """Record a platform-level permission decision for tool execution."""

    authorization = event_data.get("authorization")
    if isinstance(authorization, dict) and event_type in {"TOOL_RESULT_START", "REQUIRE_USER_CONFIRM"}:
        _record_run_event(session_id=session_id, event_type="PERMISSION_DECISION", persist=persist,
                          scope=scope, agent_id=agent_id, agent_name=agent_name,
                          status=str(authorization.get("verdict") or ""), title="平台行动权限决策",
                          payload=authorization)
        return
    if event_data.get("platformAuthorization") or event_type != "TOOL_CALL_START":
        return

    payload = _permission_decision_payload(event_data, guardrails)
    decision = str(payload["decision"])
    tool_name = str(payload["resourceId"])
    action = str(payload["action"])
    _record_run_event(
        session_id=session_id,
        event_type="PERMISSION_DECISION",
        persist=persist,
        scope=scope,
        agent_id=agent_id,
        agent_name=agent_name,
        status=decision,
        title=f"Permission {decision}: {tool_name}",
        content=f"{payload['resourceType']}:{tool_name} · action={action}",
        payload=payload,
    )


def _permission_decision_payload(event_data: dict, guardrails: AgentGuardrails) -> dict:
    """Build a normalized permission-decision payload for one tool call."""

    tool_name = _normalize_tool_event_name(str(event_data.get("tool_call_name") or "tool"))
    resource_type = _resource_type_for_tool(tool_name)
    action = _action_for_tool(tool_name)
    decision = guardrails.permission_profile.decide(resource_type, action)
    reason = _permission_reason(
        decision=decision,
        tool_name=tool_name,
        resource_type=resource_type,
        action=action,
        guardrails=guardrails,
    )
    return {
        "resourceType": resource_type.value,
        "resourceId": tool_name,
        "action": action.value,
        "decision": decision.value,
        "reason": reason,
        "toolCallId": str(event_data.get("tool_call_id") or ""),
        "policy": guardrails.permission_profile.audit_payload(),
        "legacy": {
            "toolPermissionMode": guardrails.tool_permission_mode,
            "toolPermissionProfile": guardrails.tool_permission_profile,
        },
    }


def _resource_type_for_tool(tool_name: str) -> ResourceType:
    """Map AgentScope tool names to platform resource types."""

    normalized = _normalize_tool_event_name(tool_name)
    if (
        normalized == "KnowledgeBaseSearch"
        or normalized.endswith("KnowledgeBaseSearch")
        or normalized.startswith("HttpTool_")
    ):
        return ResourceType.KNOWLEDGE_TOOL
    if normalized in {"Read", "Grep", "Glob", "Write", "Edit", "Bash", "HttpRequest", "WebFetch", "AskUser"}:
        return ResourceType.TOOL
    return ResourceType.MCP


def _action_for_tool(tool_name: str) -> PermissionAction:
    """Map tool names to a coarse platform action."""

    normalized = _normalize_tool_event_name(tool_name)
    if (
        normalized in {"Read", "Grep", "Glob", "KnowledgeBaseSearch"}
        or normalized.endswith("KnowledgeBaseSearch")
        or normalized.startswith("HttpTool_")
    ):
        return PermissionAction.READ
    if normalized in {"Write", "Edit"}:
        return PermissionAction.WRITE
    if normalized == "AskUser":
        return PermissionAction.APPROVE
    return PermissionAction.EXECUTE


def _permission_reason(
    *,
    decision: PermissionDecision,
    tool_name: str,
    resource_type: ResourceType,
    action: PermissionAction,
    guardrails: AgentGuardrails,
) -> str:
    """Explain why the platform-level decision was made."""

    profile = guardrails.permission_profile
    if decision == PermissionDecision.DENY and profile.is_read_only:
        return "read_only profile denies write/execute/configure actions"
    if decision == PermissionDecision.DENY and resource_type in {ResourceType.MCP, ResourceType.KNOWLEDGE_TOOL}:
        return "network resource denied by network policy"
    if decision == PermissionDecision.ASK and profile.human_approval:
        return "human approval is enabled for this Agent"
    if decision == PermissionDecision.ASK:
        return f"policy {profile.tool_policy.value} requires confirmation for {action.value}"
    if decision == PermissionDecision.ALLOW:
        return f"policy {profile.tool_policy.value} allows {tool_name}"
    return "decision inherited from parent policy"


TOOL_INPUT_PREVIEW_LIMIT = 600


def _tool_input_preview(tool_calls: list[ChatToolRun] | None, tool_call_id: str) -> str:
    """取工具调用已累计的输入参数，脱敏并截断后写入 trace。"""

    if not tool_calls or not tool_call_id:
        return ""
    for item in tool_calls:
        if str(getattr(item, "id", "") or "") == tool_call_id:
            args = str(getattr(item, "args", "") or "")
            if not args:
                return ""
            from src.memory.sanitizer import redact_sensitive_text

            return _trim_text(redact_sensitive_text(args).text, TOOL_INPUT_PREVIEW_LIMIT)
    return ""


# react_runtime 把 system prompt 分段字数挂在实例上，供 contextStats 定位膨胀来源。
PROMPT_SECTIONS_ATTR = "_aw_prompt_sections"
# 最近一次成功取到的工具 schema JSON 字节数缓存：同步路径拿不到 async schema 时复用。
TOOL_SCHEMA_BYTES_ATTR = "_aw_tool_schema_bytes"
# AgentScope `ChatModelBase.count_tokens` 对每个多模态 DataBlock 的固定估算。
MULTIMODAL_BLOCK_TOKENS = 2000


def _utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _collect_context_stats(agent: object) -> tuple[dict[str, Any], int, int]:
    """遍历 system prompt / summary / context，返回 (统计, 文本字节数, DataBlock 数)。

    文本字节数与 AgentScope `count_tokens` 同源（utf-8 bytes），这样 `estimatedTokens`
    与真正触发压缩的判定一致，而不是另一套字符折算。
    """

    stats: dict[str, Any] = {}
    text_bytes = 0
    data_blocks = 0
    system_prompt = getattr(agent, "_system_prompt", None)
    if isinstance(system_prompt, str):
        stats["systemPromptChars"] = len(system_prompt)
        text_bytes += _utf8_len(system_prompt)
    sections = getattr(agent, PROMPT_SECTIONS_ATTR, None)
    if isinstance(sections, dict) and sections:
        stats["promptSections"] = {str(key): int(value) for key, value in sections.items()}
    state = getattr(agent, "state", None)
    if state is not None:
        summary = getattr(state, "summary", "")
        summary_text = summary if isinstance(summary, str) else "".join(
            str(getattr(item, "text", "") or "") for item in (summary or [])
        )
        stats["summaryChars"] = len(summary_text)
        text_bytes += _utf8_len(summary_text)
        context = list(getattr(state, "context", []) or [])
        stats["contextMessages"] = len(context)
        chars = 0
        tool_results = 0
        for msg in context:
            content = getattr(msg, "content", []) or []
            if isinstance(content, str):
                chars += len(content)
                text_bytes += _utf8_len(content)
                continue
            for block in content:
                block_type = str(getattr(block, "type", "") or "")
                text = ""
                if block_type == "text":
                    text = str(getattr(block, "text", "") or "")
                elif block_type == "thinking":
                    text = str(getattr(block, "thinking", "") or "")
                elif block_type == "hint":
                    hint = getattr(block, "hint", "")
                    if isinstance(hint, str):
                        text = hint
                    else:
                        for item in hint or []:
                            if str(getattr(item, "type", "")) == "data":
                                data_blocks += 1
                            else:
                                text += str(getattr(item, "text", "") or "")
                elif block_type == "tool_call":
                    text = str(getattr(block, "input", "") or "")
                elif block_type == "tool_result":
                    tool_results += 1
                    output = getattr(block, "output", "")
                    if isinstance(output, str):
                        text = output
                    else:
                        for item in output or []:
                            if str(getattr(item, "type", "")) == "data":
                                data_blocks += 1
                            else:
                                text += str(getattr(item, "text", "") or "")
                elif block_type == "data":
                    data_blocks += 1
                chars += len(text)
                text_bytes += _utf8_len(text)
        stats["contextChars"] = chars
        stats["toolResultBlocks"] = tool_results
        if data_blocks:
            stats["dataBlocks"] = data_blocks
    return stats, text_bytes, data_blocks


def _finish_context_stats(agent: object, stats: dict[str, Any], text_bytes: int, data_blocks: int, tool_schema_bytes: int | None) -> dict[str, Any]:
    if tool_schema_bytes is None:
        cached = getattr(agent, TOOL_SCHEMA_BYTES_ATTR, None)
        tool_schema_bytes = int(cached) if isinstance(cached, int) else 0
    else:
        try:
            setattr(agent, TOOL_SCHEMA_BYTES_ATTR, int(tool_schema_bytes))
        except Exception:
            pass
    if tool_schema_bytes:
        stats["toolSchemaChars"] = int(tool_schema_bytes)
    if "toolCount" not in stats:
        toolkit = getattr(agent, "toolkit", None)
        groups = getattr(toolkit, "tool_groups", None)
        if isinstance(groups, list):
            stats["toolCount"] = sum(len(getattr(group, "tools", []) or []) for group in groups)
    model = getattr(agent, "model", None)
    window = int(getattr(model, "context_size", 0) or 0)
    if window > 0:
        stats["contextWindow"] = window
    skill_context = getattr(agent, "_aw_skill_context", None)
    if isinstance(skill_context, dict) and skill_context:
        stats["skillContext"] = dict(skill_context)
    config = getattr(agent, "context_config", None)
    trigger_ratio = float(getattr(config, "trigger_ratio", 0.8) or 0.8)
    stats["triggerRatio"] = round(trigger_ratio, 3)
    if window > 0:
        stats["triggerTokens"] = int(window * trigger_ratio)
    # 与 AgentScope count_tokens 同一算法：utf-8 字节 / 4 + 每个 DataBlock 固定 2000。
    stats["estimatedTokens"] = int((text_bytes + int(tool_schema_bytes or 0)) / 4 + 0.5) + data_blocks * MULTIMODAL_BLOCK_TOKENS
    stats["estimateMethod"] = "bytes/4"
    stats["compressed"] = 1 if int(stats.get("summaryChars") or 0) > 0 else 0
    try:
        from src.server.routes.chat.context_middleware import context_governance_stats

        stats.update(context_governance_stats(agent))
    except Exception:
        pass
    apply_context_usage_threshold(stats)
    return stats


def build_model_call_context_stats(agent: object) -> dict[str, Any]:
    """统计本次模型调用的上下文规模（同步版）。

    不调模型、不调 tokenizer。工具 schema 字节数用最近一次 async 统计缓存的值；
    供 Trace 和 Chat 顶栏对照窗口、压缩阈值、定位膨胀来源。
    """

    stats, text_bytes, data_blocks = _collect_context_stats(agent)
    return _finish_context_stats(agent, stats, text_bytes, data_blocks, None)


async def build_model_call_context_stats_async(agent: object) -> dict[str, Any]:
    """同上，但按 AgentScope 实际下发的工具 schema 精确统计 `toolSchemaChars`。"""

    stats, text_bytes, data_blocks = _collect_context_stats(agent)
    tool_schema_bytes: int | None = None
    toolkit = getattr(agent, "toolkit", None)
    getter = getattr(toolkit, "get_tool_schemas", None)
    if callable(getter):
        try:
            state = getattr(agent, "state", None)
            activated = list(getattr(getattr(state, "tool_context", None), "activated_groups", []) or [])
            schemas = await getter(activated)
            tool_schema_bytes = _utf8_len(json.dumps(schemas, ensure_ascii=False)) if schemas else 0
            stats["toolCount"] = len(schemas or [])
        except Exception:
            tool_schema_bytes = None
    return _finish_context_stats(agent, stats, text_bytes, data_blocks, tool_schema_bytes)


def apply_context_usage_threshold(stats: dict[str, Any]) -> dict[str, Any]:
    """按供应商回报 token 优先、否则按估算，对照压缩阈值。"""

    used = int(stats.get("inputTokens") or stats.get("estimatedTokens") or 0)
    trigger = int(stats.get("triggerTokens") or 0)
    if trigger:
        stats["overTrigger"] = 1 if used >= trigger else 0
    return stats


def apply_model_call_token_usage(stats: dict[str, Any], event_data: dict[str, Any]) -> dict[str, Any]:
    """把 MODEL_CALL_END 的供应商用量写进占用统计，记录估算偏差，并重算是否越过压缩阈值。"""

    stats["inputTokens"] = int(event_data.get("input_tokens") or 0)
    stats["outputTokens"] = int(event_data.get("output_tokens") or 0)
    estimated = int(stats.get("estimatedTokens") or 0)
    if stats["inputTokens"] > 0 and estimated > 0:
        # 正值 = 估算偏高（压缩会提前触发），负值 = 估算偏低（有超窗风险）。
        stats["estimateErrorPct"] = round((estimated - stats["inputTokens"]) / stats["inputTokens"] * 100, 1)
    return apply_context_usage_threshold(stats)


def record_context_compression_events(
    *,
    agent: object,
    session_id: str,
    persist: bool,
    scope: str,
    agent_id: str,
    agent_name: str,
) -> list[dict[str, Any]]:
    """把 ContextCompressionMiddleware 记下的压缩事件写成 `CONTEXT_COMPRESSED` trace。"""

    try:
        from src.server.routes.chat.context_middleware import drain_compression_records

        records = drain_compression_records(agent)
    except Exception:
        return []
    for record in records:
        _record_run_event(
            session_id=session_id,
            event_type="CONTEXT_COMPRESSED",
            persist=persist,
            scope=scope,
            agent_id=agent_id,
            agent_name=agent_name,
            status="completed",
            title=f"Context compressed #{record.get('index', 1)}",
            content=(
                f"messages {record.get('messagesBefore')} -> {record.get('messagesAfter')}, "
                f"summary {record.get('summaryCharsAfter')} chars"
            ),
            payload=_compression_payload(record),
        )
    return records


def _compression_payload(record: dict) -> dict:
    """壓縮審計帶上目前 Run snapshot 摘要，不重新展開權限。"""

    payload = dict(record)
    from src.sessions.run_binding import current_snapshot_audit

    payload.update(current_snapshot_audit())
    return payload


def _record_agent_scope_event(
    *,
    session_id: str,
    event_type: str,
    event_data: dict,
    persist: bool,
    scope: str,
    agent_id: str,
    agent_name: str,
    tool_calls: list[ChatToolRun] | None = None,
    agent: object | None = None,
    context_stats: dict[str, Any] | None = None,
) -> None:
    """Record selected AgentScope events as compact trace events.

    `tool_calls` 提供时，TOOL_CALL_END / TOOL_RESULT_START 会附带脱敏后的输入参数预览；
    `agent` 提供时，MODEL_CALL_START 会附带上下文规模统计（`context_stats` 已算好时直接复用）。
    """
    if event_type not in TRACE_EVENT_TYPES:
        return
    tool_name = str(event_data.get("tool_call_name") or "")
    model_name = str(event_data.get("model_name") or "")
    finished_reason = str(event_data.get("finished_reason") or "completed").lower()
    reply_failed = event_type == "REPLY_END" and finished_reason != "completed"
    model_interrupted = event_type == "MODEL_CALL_END" and finished_reason == "interrupted"
    title = {
        "REPLY_START": "Reply started",
        "REPLY_END": (
            "Reply completed" if not reply_failed else f"Reply {finished_reason.replace('_', ' ')}"
        ),
        "MODEL_CALL_START": f"Model call started{f': {model_name}' if model_name else ''}",
        "MODEL_CALL_END": (
            f"Model call {'interrupted' if model_interrupted else 'completed'}"
            f"{f': {model_name}' if model_name else ''}"
        ),
        "TOOL_CALL_START": f"Tool call started{f': {tool_name}' if tool_name else ''}",
        "TOOL_CALL_END": f"Tool call ended{f': {tool_name}' if tool_name else ''}",
        "TOOL_RESULT_START": f"Tool result started{f': {tool_name}' if tool_name else ''}",
        "TOOL_RESULT_END": f"Tool result completed{f': {tool_name}' if tool_name else ''}",
    }.get(event_type, event_type)
    if reply_failed or model_interrupted:
        status = "failed"
    elif event_type in {"REPLY_END", "MODEL_CALL_END", "TOOL_RESULT_END"}:
        status = "completed"
    else:
        status = "running"
    payload = dict(event_data)
    for key in ("observationInput", "observationOutput", "previousGenerationOutput", "observationText"):
        payload.pop(key, None)
    if "delta" in payload:
        payload["delta"] = _trim_text(str(payload.get("delta") or ""), 240)
    if event_type in {"TOOL_CALL_END", "TOOL_RESULT_START"}:
        preview = _tool_input_preview(tool_calls, str(event_data.get("tool_call_id") or ""))
        if preview:
            payload["inputPreview"] = preview
    if event_type == "MODEL_CALL_START":
        if context_stats:
            payload["contextStats"] = dict(context_stats)
        elif agent is not None:
            try:
                payload["contextStats"] = build_model_call_context_stats(agent)
            except Exception:
                pass
    _record_run_event(
        session_id=session_id,
        event_type=event_type,
        persist=persist,
        scope=scope,
        agent_id=agent_id,
        agent_name=agent_name,
        status=status,
        title=title,
        payload=payload,
    )
    provider_cache = payload.get("provider_cache")
    if event_type == "MODEL_CALL_END" and isinstance(provider_cache, dict) and provider_cache.get("available"):
        created_tokens = int(provider_cache.get("created_tokens") or 0)
        hit_tokens = int(provider_cache.get("hit_tokens") or 0)
        _record_run_event(
            session_id=session_id,
            event_type="MODEL_CACHE_USAGE",
            persist=persist,
            scope=scope,
            agent_id=agent_id,
            agent_name=agent_name,
            status="completed",
            title="Provider prompt cache usage",
            content=f"created={created_tokens}, hit={hit_tokens}",
            payload={
                "model": model_name,
                "createdTokens": created_tokens,
                "hitTokens": hit_tokens,
                "estimatedSavedInputTokens": hit_tokens,
            },
        )


def _record_tool_execution_artifacts(
    *,
    session_id: str,
    persist: bool,
    scope: str,
    agent_id: str,
    agent_name: str,
    guardrails: AgentGuardrails,
    event_type: str,
    event_data: dict,
    tool_calls: list["ChatToolRun"],
) -> None:
    """Persist command output and explicit file-change artifacts into traces."""

    if event_type != "TOOL_RESULT_END":
        return

    tool_call_id = str(event_data.get("tool_call_id") or "")
    if not tool_call_id:
        return
    tool_call = next((item for item in tool_calls if item.id == tool_call_id), None)
    if tool_call is None:
        return

    tool_name = _normalize_tool_event_name(tool_call.name)
    status = _tool_result_status(event_data)
    args = _parse_tool_args(tool_call.args)

    if tool_name == "Bash":
        command_payload = _build_command_result_payload(
            command=str(args.get("command") or ""),
            result=tool_call.result,
            status=status,
            guardrails=guardrails,
        )
        _record_run_event(
            session_id=session_id,
            event_type="COMMAND_RESULT",
            persist=persist,
            scope=scope,
            agent_id=agent_id,
            agent_name=agent_name,
            status=status,
            title="Command execution result",
            content=_trim_text(command_payload.get("summary", ""), 600),
            payload=command_payload,
        )
        if _command_may_change_files(command_payload["command"]):
            _record_run_event(
                session_id=session_id,
                event_type="FILE_CHANGE",
                persist=persist,
                scope=scope,
                agent_id=agent_id,
                agent_name=agent_name,
                status="observed",
                title="Shell command may have changed files",
                content=_trim_text(command_payload["command"], 600),
                payload={
                    "operation": "shell",
                    "toolName": tool_name,
                    "toolCallId": tool_call_id,
                    "command": command_payload["command"],
                    "changedFiles": [],
                    "changeDetection": "not_available_for_shell_commands",
                    "workspace": _workspace_trace_payload(guardrails),
                },
            )
        return

    if tool_name not in {"Write", "Edit"}:
        return

    file_path = str(args.get("file_path") or "")
    operation = "write" if tool_name == "Write" else "edit"
    payload = {
        "operation": operation,
        "toolName": tool_name,
        "toolCallId": tool_call_id,
        "filePath": file_path,
        "status": status,
        "workspace": _workspace_trace_payload(guardrails),
        "result": _trim_text(tool_call.result, 1200),
    }
    if tool_name == "Write":
        content = str(args.get("content") or "")
        payload["lineCount"] = len(content.splitlines()) if content else 0
        payload["contentBytes"] = len(content.encode("utf-8"))
    else:
        payload["replaceAll"] = bool(args.get("replace_all"))
        payload["oldStringBytes"] = len(str(args.get("old_string") or "").encode("utf-8"))
        payload["newStringBytes"] = len(str(args.get("new_string") or "").encode("utf-8"))

    _record_run_event(
        session_id=session_id,
        event_type="FILE_CHANGE",
        persist=persist,
        scope=scope,
        agent_id=agent_id,
        agent_name=agent_name,
        status=status,
        title=f"{tool_name} file operation",
        content=file_path,
        payload=payload,
    )


def build_tool_artifact_runtime_event(
    *,
    event_type: str,
    event_data: dict,
    tool_calls: list["ChatToolRun"],
) -> tuple[str, dict] | None:
    """从工具结果中提取可交付产物 Runtime Event。"""

    if event_type != "TOOL_RESULT_END":
        return None
    tool_call_id = str(event_data.get("tool_call_id") or "")
    if not tool_call_id:
        return None
    tool_call = next((item for item in tool_calls if item.id == tool_call_id), None)
    if tool_call is None:
        return None
    tool_name = _normalize_tool_event_name(tool_call.name)
    if tool_name not in {"Write", "Edit"}:
        return None
    args = _parse_tool_args(tool_call.args)
    file_path = str(args.get("file_path") or "")
    if not file_path or not is_user_facing_session_artifact(file_path):
        return None
    operation = "write" if tool_name == "Write" else "edit"
    runtime_type = "ARTIFACT_CREATED" if operation == "write" else "ARTIFACT_UPDATED"
    payload = {
        "operation": operation,
        "toolName": tool_name,
        "toolCallId": tool_call_id,
        "filePath": file_path,
        "downloadPath": file_path,
        "status": _tool_result_status(event_data),
        "result": _trim_text(tool_call.result, 1200),
    }
    if tool_name == "Write":
        content = str(args.get("content") or "")
        payload["lineCount"] = len(content.splitlines()) if content else 0
        payload["contentBytes"] = len(content.encode("utf-8"))
    else:
        payload["replaceAll"] = bool(args.get("replace_all"))
    return runtime_type, payload


def _normalize_tool_event_name(name: str) -> str:
    """Normalize repeated display names like `A_A` back to one tool name."""

    raw = str(name or "tool").strip() or "tool"
    if "_" in raw:
        left, right = raw.split("_", 1)
        if left and left == right:
            return left
    return raw


def _parse_tool_args(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _build_command_result_payload(
    *,
    command: str,
    result: str,
    status: str,
    guardrails: AgentGuardrails,
) -> dict:
    stdout = result
    stderr = ""
    exit_code: int | None = 0 if status == "completed" else 1
    exit_code_known = status == "completed"
    summary = result

    failed_match = re.match(
        r"Command failed: (?P<command>.*?)\n(?:\nStdout:\n(?P<stdout>.*?))?(?:\n(?:Stderr|Error):\n(?P<stderr>.*))?$",
        result,
        flags=re.DOTALL,
    )
    if failed_match:
        command = command or failed_match.group("command") or ""
        stdout = failed_match.group("stdout") or ""
        stderr = failed_match.group("stderr") or ""
        summary = stderr or stdout or result
        exit_code_known = False
    elif result.startswith("Command timed out"):
        stdout = ""
        stderr = result
        summary = result
        exit_code = None
        exit_code_known = False

    return {
        "command": command,
        "stdout": _trim_text(stdout, 12000),
        "stderr": _trim_text(stderr, 12000),
        "exitCode": exit_code,
        "exitCodeKnown": exit_code_known,
        "status": status,
        "summary": _trim_text(summary, 1200),
        "workspace": _workspace_trace_payload(guardrails),
    }


def _command_may_change_files(command: str) -> bool:
    """Best-effort shell heuristic; explicit Write/Edit events are exact."""

    lowered = f" {command.lower()} "
    patterns = [
        " >",
        ">>",
        " tee ",
        " touch ",
        " mkdir ",
        " mv ",
        " cp ",
        " rm ",
        " sed -i",
        " python ",
        " python3 ",
        " node ",
        " npm ",
        " pnpm ",
        " yarn ",
    ]
    return any(pattern in lowered for pattern in patterns)


def _attach_provider_cache_usage(event_type: str, event_data: dict) -> None:
    """Expose provider-side prompt cache usage on MODEL_CALL_END events."""

    if event_type != "MODEL_CALL_END":
        return

    metadata = event_data.get("metadata")
    if not isinstance(metadata, dict):
        return

    cache_creation = int(metadata.get("cache_creation_input_tokens") or 0)
    cache_read = int(metadata.get("cache_input_tokens") or 0)
    event_data["cache_creation_input_tokens"] = cache_creation
    event_data["cache_input_tokens"] = cache_read
    event_data["provider_cache"] = {
        "created_tokens": cache_creation,
        "hit_tokens": cache_read,
        "available": bool(cache_creation or cache_read),
    }


def _model_cache_from_event(event_data: dict, model_name: str) -> ChatModelCacheRun | None:
    """Build provider prompt-cache stats from a MODEL_CALL_END payload."""

    created_tokens = int(event_data.get("cache_creation_input_tokens") or 0)
    hit_tokens = int(event_data.get("cache_input_tokens") or 0)
    if created_tokens == 0 and hit_tokens == 0:
        return None
    input_tokens = int(event_data.get("input_tokens") or 0)
    output_tokens = int(event_data.get("output_tokens") or 0)
    cache_base = input_tokens + hit_tokens
    return ChatModelCacheRun.model_validate({
        "id": str(event_data.get("id") or ""),
        "modelName": model_name,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "cacheCreationInputTokens": created_tokens,
        "cacheInputTokens": hit_tokens,
        "cacheHitRate": hit_tokens / cache_base if cache_base > 0 else 0,
        "estimatedSavedInputTokens": hit_tokens,
        "createdAt": str(event_data.get("created_at") or ""),
    })
