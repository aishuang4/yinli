"""Trace 记录 — 运行事件、fallback、workspace、AgentScope 事件与工具产物。

所有 trace 记录都不允许因可观测性失败而中断主执行流。
"""

import json
import re

from src.agents.factory import AgentGuardrails
from src.security.permissions import (
    PermissionAction,
    PermissionDecision,
    ResourceType,
)
from src.storage.database import append_run_event

from src.server.routes.chat.helpers import _trim_text
from src.server.routes.chat.models import ChatModelCacheRun, ChatToolRun, TRACE_EVENT_TYPES
from src.server.routes.chat.team_state import _tool_result_status


def _record_run_event(
    *,
    session_id: str,
    event_type: str,
    persist: bool,
    scope: str = "leader",
    agent_id: str | None = None,
    agent_name: str | None = None,
    team_activity_id: str | None = None,
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
            team_activity_id=team_activity_id,
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
    team_activity_id: str | None = None,
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
            team_activity_id=team_activity_id,
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
    team_activity_id: str | None = None,
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
        team_activity_id=team_activity_id,
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
        team_activity_id=team_activity_id,
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
    team_activity_id: str | None = None,
) -> None:
    """Record a platform-level permission decision for tool execution."""

    if event_type != "TOOL_CALL_START":
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
        team_activity_id=team_activity_id,
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
    if normalized in {"Read", "Grep", "Glob", "Write", "Edit", "Bash", "HttpRequest", "AskUser"}:
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


def _record_agent_scope_event(
    *,
    session_id: str,
    event_type: str,
    event_data: dict,
    persist: bool,
    scope: str,
    agent_id: str,
    agent_name: str,
    team_activity_id: str | None = None,
) -> None:
    """Record selected AgentScope events as compact trace events."""
    if event_type not in TRACE_EVENT_TYPES:
        return
    tool_name = str(event_data.get("tool_call_name") or "")
    model_name = str(event_data.get("model_name") or "")
    title = {
        "REPLY_START": "Reply started",
        "REPLY_END": "Reply completed",
        "MODEL_CALL_START": f"Model call started{f': {model_name}' if model_name else ''}",
        "MODEL_CALL_END": f"Model call completed{f': {model_name}' if model_name else ''}",
        "TOOL_CALL_START": f"Tool call started{f': {tool_name}' if tool_name else ''}",
        "TOOL_CALL_END": f"Tool call ended{f': {tool_name}' if tool_name else ''}",
        "TOOL_RESULT_START": f"Tool result started{f': {tool_name}' if tool_name else ''}",
        "TOOL_RESULT_END": f"Tool result completed{f': {tool_name}' if tool_name else ''}",
    }.get(event_type, event_type)
    status = (
        "completed"
        if event_type in {"REPLY_END", "MODEL_CALL_END", "TOOL_RESULT_END"}
        else "running"
    )
    payload = dict(event_data)
    if "delta" in payload:
        payload["delta"] = _trim_text(str(payload.get("delta") or ""), 240)
    _record_run_event(
        session_id=session_id,
        event_type=event_type,
        persist=persist,
        scope=scope,
        agent_id=agent_id,
        agent_name=agent_name,
        team_activity_id=team_activity_id,
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
            team_activity_id=team_activity_id,
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
    team_activity_id: str | None,
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
            team_activity_id=team_activity_id,
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
                team_activity_id=team_activity_id,
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
        team_activity_id=team_activity_id,
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
    if not file_path:
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
        r"Command failed: (?P<command>.*?)\n(?:\nStdout:\n(?P<stdout>.*?))?(?:\nStderr:\n(?P<stderr>.*))?$",
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
