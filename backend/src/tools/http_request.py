"""HTTP 请求工具；授权和执行共用最终请求解析。"""

import json
from typing import Any
from urllib.parse import urlparse

import httpx

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk


def prepare_http_request(
    url: str, method: str = "GET", headers: dict[str, str] | None = None,
    query: dict[str, Any] | None = None, body: Any = None, timeout_seconds: float = 30,
) -> dict[str, Any]:
    """解析最终参数，授权快照和真正请求必须复用同一路径。"""
    method = method.upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        raise ValueError("不支持的 HTTP 方法")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("HTTP 请求需要完整的 HTTP(S) URL")

    timeout = max(1.0, min(float(timeout_seconds or 30), 120.0))
    request_headers = {
        str(key): str(value)
        for key, value in (headers or {}).items()
        if str(key).strip()
    }
    kwargs: dict[str, Any] = {
        "method": method,
        "url": url,
        "headers": request_headers or None,
        "params": query or None,
        "timeout": timeout,
    }
    if body is not None and method not in {"GET", "HEAD"}:
        if isinstance(body, (dict, list)):
            kwargs["json"] = body
        else:
            kwargs["content"] = str(body)
    return kwargs


class HttpRequest(ToolBase):
    """Make outbound HTTP requests from an Agent."""

    name: str = "HttpRequest"
    description: str = (
        "Make an HTTP request to an external API or web endpoint. "
        "Use this for JSON APIs, webhooks, REST calls, and lightweight HTTP checks. "
        "Do not use it for local file access."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Absolute http:// or https:// URL.",
            },
            "method": {
                "type": "string",
                "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
                "description": "HTTP method. Defaults to GET.",
                "default": "GET",
            },
            "headers": {
                "type": "object",
                "description": "Optional request headers.",
                "additionalProperties": {"type": "string"},
            },
            "query": {
                "type": "object",
                "description": "Optional query parameters.",
                "additionalProperties": {"type": ["string", "number", "boolean"]},
            },
            "body": {
                "description": "Optional request body. Objects are sent as JSON; strings are sent as text.",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "Request timeout in seconds.",
                "default": 30,
                "minimum": 1,
                "maximum": 120,
            },
        },
        "required": ["url"],
    }
    is_mcp: bool = False
    is_read_only: bool = False
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(self, max_response_chars: int = 12000) -> None:
        self.max_response_chars = max_response_chars

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        url = str(tool_input.get("url") or "")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message="HttpRequest requires an absolute http:// or https:// URL.",
                decision_reason="Invalid URL",
                bypass_immune=True,
            )
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="HTTP request requires normal permission evaluation.",
            decision_reason="Network request",
        )

    async def check_read_only(self, tool_input: dict[str, Any]) -> bool:
        method = str(tool_input.get("method") or "GET").upper()
        return method in {"GET", "HEAD"}

    async def __call__(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        query: dict[str, Any] | None = None,
        body: Any = None,
        timeout_seconds: float = 30,
    ) -> ToolChunk:
        try:
            kwargs = prepare_http_request(url, method, headers, query, body, timeout_seconds)
        except ValueError:
            return self._error("HTTP 请求参数无效")

        try:
            from src.security.egress import guarded_async_client

            async with guarded_async_client(follow_redirects=True) as client:
                response = await client.request(**kwargs)
        except Exception as exc:
            return self._error(f"HTTP request failed: {exc}")

        content_type = response.headers.get("content-type", "")
        text = response.text
        truncated = False
        if len(text) > self.max_response_chars:
            text = text[: self.max_response_chars]
            truncated = True

        payload = {
            "url": str(response.url),
            "status_code": response.status_code,
            "reason_phrase": response.reason_phrase,
            "content_type": content_type,
            "headers": dict(response.headers),
            "body": self._format_body(text, content_type),
            "truncated": truncated,
        }
        return ToolChunk(
            content=[TextBlock(text=json.dumps(payload, ensure_ascii=False, indent=2))],
            state=ToolResultState.SUCCESS if response.is_success else ToolResultState.ERROR,
            is_last=True,
        )

    def _format_body(self, text: str, content_type: str) -> Any:
        if "application/json" not in content_type.lower():
            return text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    def _error(self, message: str) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=f"Error: {message}")],
            state=ToolResultState.ERROR,
            is_last=True,
        )
