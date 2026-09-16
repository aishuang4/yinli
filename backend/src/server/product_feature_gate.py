"""按产品档关闭页面-only API，不切断 Chat 运行时。"""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.config.product_features import is_surface_enabled, is_view_enabled

_PAGE_ONLY_PREFIXES: tuple[tuple[str, str, str], ...] = (
    ("/api/traces", "view", "traces"),
    ("/api/sandbox", "view", "sandbox"),
    ("/api/subagents", "view", "subagents"),
    ("/api/workflows", "surface", "workflow"),
)


def _path_matches(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}/")


class ProductFeatureGateMiddleware:
    """关闭的页面管理 API 直接 404，避免只藏入口仍能被调用。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] == "http":
            path = str(scope.get("path") or "")
            for prefix, kind, key in _PAGE_ONLY_PREFIXES:
                if not _path_matches(path, prefix):
                    continue
                enabled = is_view_enabled(key) if kind == "view" else is_surface_enabled(key)
                if enabled:
                    break
                response = JSONResponse(
                    {"detail": "feature_disabled", "feature": key},
                    status_code=404,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
