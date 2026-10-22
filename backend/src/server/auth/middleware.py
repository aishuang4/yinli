"""托管模式下，回环来源也不能代替登录会话。"""

from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.config.settings import settings
from src.server.auth.routes import token_from_request
from src.server.auth.service import current_user

PUBLIC_PATHS = {
    "/api/health",
    "/api/auth/config",
    "/api/auth/register",
    "/api/auth/verify",
    "/api/auth/login",
    "/api/auth/forgot-password",
    "/api/auth/reset-password",
    "/api/auth/desktop/session",
}


class HostedSessionMiddleware:
    """hosted 时校验会话并绑定租户。desktop 不拦截本机请求。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or settings.serve_mode.strip().lower() != "hosted":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path") or "")
        if not path.startswith("/api/") or path in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        found = current_user(token_from_request(_RequestView(scope)))
        if found is None:
            response = JSONResponse(status_code=401, content={"detail": "需要登录"})
            await response(scope, receive, send)
            return
        user = found["user"]
        from src.storage.control.control_records import get_tenant
        from src.tenancy.context import TenantContext, reset_tenant, set_tenant
        from src.tenancy.lifecycle import ensure_tenant

        tenant = get_tenant(str(user["id"]))
        if tenant is None:
            ctx = ensure_tenant(user)
        elif tenant["status"] != "active":
            response = JSONResponse(status_code=403, content={"detail": "账号已停用"})
            await response(scope, receive, send)
            return
        else:
            ctx = TenantContext.hosted(str(user["id"]), role=str(user.get("role") or "user"))
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["auth_user"] = user
            state["auth_session"] = found["session"]
        token = set_tenant(ctx)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_tenant(token)


class _RequestView:
    """给 token_from_request 提供 headers 和 cookies，避免在中间件里构造完整 Request。"""

    def __init__(self, scope: Scope) -> None:
        raw_headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers") or []
        }
        self.headers = raw_headers
        cookie = raw_headers.get("cookie", "")
        self.cookies = {
            item.split("=", 1)[0].strip(): item.split("=", 1)[1]
            for item in cookie.split(";")
            if "=" in item
        }
