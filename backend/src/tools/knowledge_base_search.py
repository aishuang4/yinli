"""Knowledge base HTTP search tool."""

import json
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk


class KnowledgeBaseSearch(ToolBase):
    """Search an external knowledge base through a configured HTTP endpoint."""

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = True
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(
        self,
        tool_id: str,
        name: str,
        description: str,
        url: str,
        authorization: str = "",
        headers: dict[str, str] | None = None,
        default_payload: dict[str, Any] | None = None,
        parameter_instructions: str = "",
        timeout_seconds: float = 30,
        max_response_chars: int = 16000,
    ) -> None:
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", name).strip("_") or "default"
        self.name = (safe_name if safe_name == "KnowledgeBaseSearch" else f"KnowledgeBaseSearch_{safe_name}")[:64]
        self.tool_id = tool_id
        self.description = "\n".join(
            part
            for part in [
                description or "Search an external knowledge base.",
                parameter_instructions,
                (
                    "Use this tool when user questions require facts from the configured knowledge base. "
                    "Return grounded answers based on retrieved snippets; do not fabricate results when no hit is returned."
                ),
            ]
            if part
        )
        self.input_schema = {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query. Usually the user's question or a concise rewritten query.",
                },
                "filename_pattern": {
                    "type": "string",
                    "description": "Optional filename pattern filter.",
                },
                "filterCategories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional category filters.",
                },
                "searchMode": {
                    "type": "string",
                    "description": "Optional search mode.",
                },
            },
            "required": ["query"],
        }
        self.url = url
        self.authorization = authorization
        self.headers = headers or {}
        self.default_payload = default_payload or {}
        self.timeout_seconds = timeout_seconds
        self.max_response_chars = max_response_chars

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                message="KnowledgeBaseSearch requires an absolute http:// or https:// URL.",
                decision_reason="Invalid knowledge base URL",
                bypass_immune=True,
            )
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="Knowledge base search requires normal permission evaluation.",
            decision_reason="Knowledge base HTTP search",
        )

    async def __call__(
        self,
        query: str,
        filename_pattern: str | None = None,
        filterCategories: list[str] | None = None,
        searchMode: str | None = None,
    ) -> ToolChunk:
        payload = dict(self.default_payload)
        payload["query"] = query
        if filename_pattern is not None:
            payload["filename_pattern"] = filename_pattern
        if filterCategories is not None:
            payload["filterCategories"] = filterCategories
        if searchMode is not None:
            payload["searchMode"] = searchMode

        request_headers = {
            "Content-Type": "application/json",
            **{str(key): str(value) for key, value in self.headers.items() if str(key).strip()},
        }
        if self.authorization:
            request_headers["Authorization"] = self.authorization

        try:
            from src.security.egress import guarded_async_client

            async with guarded_async_client(follow_redirects=True) as client:
                response = await client.post(
                    self.url,
                    headers=request_headers,
                    json=payload,
                    timeout=max(1.0, min(float(self.timeout_seconds or 30), 120.0)),
                )
        except Exception as exc:
            return self._error(f"Knowledge base request failed: {exc}")

        text = response.text
        truncated = False
        if len(text) > self.max_response_chars:
            text = text[: self.max_response_chars]
            truncated = True

        result = {
            "tool_id": self.tool_id,
            "url": str(response.url),
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "request_payload": payload,
            "body": self._format_body(text, response.headers.get("content-type", "")),
            "truncated": truncated,
        }
        return ToolChunk(
            content=[TextBlock(text=json.dumps(result, ensure_ascii=False, indent=2))],
            state=ToolResultState.SUCCESS if response.is_success else ToolResultState.ERROR,
            is_last=True,
        )

    def _format_body(self, text: str, content_type: str) -> Any:
        if "json" not in content_type.lower():
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
