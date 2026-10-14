"""通用可配置 HTTP 工具。"""

import json
import re
import time
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

import httpx

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk


_TEMPLATE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}|\$\{\s*([a-zA-Z0-9_.-]+)\s*\}")


def safe_http_tool_name(name: str) -> str:
    """把用户配置的工具名转成 AgentScope 可用工具名。"""

    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", name).strip("_") or "Custom"
    return f"HttpTool_{safe_name}"[:64]


def _value_at_path(data: Any, path: str, default: Any = "") -> Any:
    current = data
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part, default)
            continue
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if 0 <= index < len(current) else default
            continue
        return default
    return current


def _render_value(value: Any, parameters: dict[str, Any]) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            key = match.group(1) or match.group(2) or ""
            resolved = _value_at_path(parameters, key, "")
            return str(resolved)

        return _TEMPLATE_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_render_value(item, parameters) for item in value]
    if isinstance(value, dict):
        return {
            str(_render_value(key, parameters)): _render_value(item, parameters)
            for key, item in value.items()
        }
    return value


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge(merged.get(key), value)
        return merged
    if override in (None, ""):
        return base
    return override


def _headers_from_auth(config: dict[str, Any], query: dict[str, Any]) -> tuple[dict[str, str], httpx.Auth | None]:
    auth_type = str(config.get("authType") or config.get("auth_type") or "none").lower()
    auth_config = dict(config.get("authConfig") or config.get("auth_config") or {})
    headers: dict[str, str] = {}
    auth: httpx.Auth | None = None

    if auth_type == "bearer":
        token = str(auth_config.get("token") or auth_config.get("bearerToken") or "").strip()
        if token:
            headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    elif auth_type == "basic":
        username = str(auth_config.get("username") or "")
        password = str(auth_config.get("password") or "")
        auth = httpx.BasicAuth(username, password)
    elif auth_type == "api_key":
        key_name = str(auth_config.get("name") or auth_config.get("key") or "Authorization")
        key_value = str(auth_config.get("value") or auth_config.get("token") or "")
        location = str(auth_config.get("in") or auth_config.get("location") or "header").lower()
        if key_name and key_value:
            if location == "query":
                query[key_name] = key_value
            else:
                headers[key_name] = key_value
    elif auth_type == "custom":
        headers.update({
            str(key): str(value)
            for key, value in dict(auth_config.get("headers") or {}).items()
            if str(key).strip()
        })

    return headers, auth


def _extract_response(body: Any, mode: str, path: str) -> Any:
    if mode == "body":
        return body
    if mode == "path" and path:
        return _value_at_path(body, path, None)
    return body


async def execute_configured_http_tool(
    config: dict[str, Any],
    parameters: dict[str, Any],
    *,
    timeout_seconds: float = 30,
    max_response_chars: int = 16000,
) -> dict[str, Any]:
    """按配置执行 HTTP 请求，供 Agent 工具和调试接口复用。"""

    method = str(config.get("method") or "GET").upper()
    url = str(_render_value(config.get("url") or "", parameters))
    parsed = urlparse(url)
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        raise ValueError(f"Unsupported HTTP method: {method}")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL must be absolute and start with http:// or https://")

    query_template = deepcopy(config.get("queryTemplate") or config.get("query_template") or {})
    body_template = deepcopy(config.get("bodyTemplate") or config.get("body_template") or {})
    query = _render_value(query_template, parameters)
    body = _render_value(body_template, parameters)
    if not isinstance(query, dict):
        query = {}

    remaining_parameters = {
        key: value
        for key, value in parameters.items()
        if key not in {"parameters", "body", "query", "headers"}
    }
    if isinstance(parameters.get("query"), dict):
        query = _deep_merge(query, parameters["query"])
    elif method in {"GET", "DELETE"}:
        query = _deep_merge(query, remaining_parameters)

    if isinstance(parameters.get("body"), (dict, list, str)):
        body = _deep_merge(body, parameters["body"])
    elif method not in {"GET", "DELETE"} and not body:
        body = remaining_parameters

    request_headers = {
        str(key): str(value)
        for key, value in (config.get("headers") or {}).items()
        if str(key).strip()
    }
    if isinstance(parameters.get("headers"), dict):
        request_headers.update({
            str(key): str(value)
            for key, value in parameters["headers"].items()
            if str(key).strip()
        })
    auth_headers, auth = _headers_from_auth(config, query)
    request_headers.update(auth_headers)

    timeout = max(1.0, min(float(timeout_seconds or 30), 120.0))
    kwargs: dict[str, Any] = {
        "method": method,
        "url": url,
        "headers": request_headers or None,
        "params": query or None,
        "auth": auth,
        "timeout": timeout,
    }
    if body not in (None, {}, []) and method not in {"GET", "DELETE"}:
        if isinstance(body, (dict, list)):
            kwargs["json"] = body
        else:
            kwargs["content"] = str(body)

    started = time.perf_counter()
    from src.security.egress import guarded_async_client

    async with guarded_async_client(follow_redirects=True) as client:
        response = await client.request(**kwargs)
    duration_ms = int((time.perf_counter() - started) * 1000)

    content_type = response.headers.get("content-type", "")
    text = response.text
    truncated = False
    if len(text) > max_response_chars:
        text = text[:max_response_chars]
        truncated = True
    body_value: Any = text
    if "json" in content_type.lower():
        try:
            body_value = json.loads(text)
        except json.JSONDecodeError:
            body_value = text

    response_mode = str(config.get("responseMode") or config.get("response_mode") or "full")
    response_path = str(config.get("responsePath") or config.get("response_path") or "")
    extracted = _extract_response(body_value, response_mode, response_path)

    return {
        "tool_id": str(config.get("id") or ""),
        "tool_name": str(config.get("name") or ""),
        "method": method,
        "url": str(response.url),
        "status_code": response.status_code,
        "reason_phrase": response.reason_phrase,
        "content_type": content_type,
        "duration_ms": duration_ms,
        "request": {
            "query": query,
            "body": body,
        },
        "response": extracted,
        "raw_body": body_value if response_mode == "full" else None,
        "truncated": truncated,
    }


class ConfiguredHttpTool(ToolBase):
    """把用户配置的 REST API 暴露为独立 Agent 工具。"""

    is_mcp: bool = False
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(
        self,
        config: dict[str, Any],
        *,
        timeout_seconds: float = 30,
        max_response_chars: int = 16000,
    ) -> None:
        self.config = dict(config)
        self.tool_id = str(config.get("id") or "")
        self.name = safe_http_tool_name(str(config.get("name") or "Custom"))
        self.is_read_only = bool(config.get("readonly", True))
        self.timeout_seconds = timeout_seconds
        self.max_response_chars = max_response_chars
        instructions = str(config.get("parameterInstructions") or config.get("parameter_instructions") or "")
        self.description = "\n".join(
            part for part in [
                str(config.get("description") or "Call a configured HTTP API."),
                f"HTTP {str(config.get('method') or 'GET').upper()} {config.get('url') or ''}",
                instructions,
                "Return the configured API response. Use only when this API matches the user's task.",
            ] if part
        )
        schema = config.get("parameterSchema") or config.get("parameter_schema") or {}
        self.input_schema = self._normalize_schema(schema)

    def _normalize_schema(self, schema: Any) -> dict[str, Any]:
        if isinstance(schema, dict) and schema.get("type") == "object":
            return schema
        return {
            "type": "object",
            "properties": {
                "parameters": {
                    "type": "object",
                    "description": "HTTP tool parameters. Keys are merged into query/body templates.",
                    "additionalProperties": True,
                }
            },
        }

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        url = str(_render_value(self.config.get("url") or "", tool_input))
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message="ConfiguredHttpTool requires an absolute http:// or https:// URL.",
                decision_reason="Invalid configured HTTP URL",
                bypass_immune=True,
            )
        approval_policy = str(self.config.get("approvalPolicy") or self.config.get("approval_policy") or "inherit").lower()
        if approval_policy == "deny":
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message=f"Configured HTTP tool {self.name} is disabled by its approval policy.",
                decision_reason="HTTP tool approval policy denies execution",
                bypass_immune=True,
            )
        if approval_policy == "ask":
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                message=f"Confirm configured HTTP tool call: {self.name}",
                decision_reason="HTTP tool approval policy requires confirmation",
                bypass_immune=True,
            )
        if approval_policy == "allow":
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message=f"Configured HTTP tool {self.name} is allowed by its approval policy.",
                decision_reason="HTTP tool approval policy allows execution",
            )
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="Configured HTTP tool requires normal permission evaluation.",
            decision_reason="Configured HTTP request",
        )

    async def check_read_only(self, tool_input: dict[str, Any]) -> bool:
        return self.is_read_only

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        parameters = dict(kwargs.get("parameters") or kwargs)
        try:
            result = await execute_configured_http_tool(
                self.config,
                parameters,
                timeout_seconds=self.timeout_seconds,
                max_response_chars=self.max_response_chars,
            )
        except Exception as exc:
            return self._error(f"Configured HTTP request failed: {exc}")

        return ToolChunk(
            content=[TextBlock(text=json.dumps(result, ensure_ascii=False, indent=2))],
            state=ToolResultState.SUCCESS if int(result.get("status_code") or 0) < 400 else ToolResultState.ERROR,
            is_last=True,
        )

    def _error(self, message: str) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=f"Error: {message}")],
            state=ToolResultState.ERROR,
            is_last=True,
        )
