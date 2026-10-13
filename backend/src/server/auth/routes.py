"""账号与管理员 HTTP 接口。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.config.settings import settings
from src.tenancy.context import is_hosted_mode
from src.server.auth.service import (
    AuthError,
    admin_directory,
    current_user,
    delete_user,
    disable_user,
    enable_user,
    heartbeat,
    login_user,
    logout_token,
    register_user,
    request_password_reset,
    reset_password,
    verify_email,
)
from src.storage.auth_records import SESSION_TTL

SESSION_COOKIE = "yinli_session"


def require_hosted_mode() -> None:
    """桌面端没有控制面，账号和管理员接口按不存在处理。"""

    if not is_hosted_mode():
        raise HTTPException(status_code=404, detail="仅托管服务可用")


auth_router = APIRouter(prefix="/api/auth", tags=["账号"])
admin_router = APIRouter(
    prefix="/api/admin",
    tags=["管理员"],
    dependencies=[Depends(require_hosted_mode)],
)
_HOSTED = [Depends(require_hosted_mode)]


class RegisterRequest(BaseModel):
    email: str
    password: str


class VerifyRequest(BaseModel):
    email: str
    code: str


class LoginRequest(BaseModel):
    email: str
    password: str
    client_kind: str = Field(default="web", alias="clientKind")

    model_config = {"populate_by_name": True}


class EmailRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    email: str
    code: str
    password: str


def _raise(error: AuthError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.detail) from error


def token_from_request(request: Request) -> str:
    """优先读取 Authorization，其次读取 Web 会话 Cookie。"""

    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(SESSION_COOKIE, "")


def require_user(request: Request) -> dict:
    found = current_user(token_from_request(request))
    if found is None:
        raise HTTPException(status_code=401, detail="需要登录")
    return found


def require_admin(request: Request) -> dict:
    found = require_user(request)
    if found["user"]["role"] != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return found


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    """只在实际 HTTPS 下加 Secure。本地 http://127.0.0.1 否则不会保存登录态。"""

    forwarded = request.headers.get("x-forwarded-proto", "")
    scheme = forwarded.split(",")[0].strip().lower() if forwarded else request.url.scheme
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=scheme == "https",
        samesite="lax",
        path="/",
    )


class DesktopLoginRequest(BaseModel):
    base_url: str = Field(alias="baseUrl")
    email: str
    password: str

    model_config = {"populate_by_name": True}


def _require_desktop_mode() -> None:
    if settings.serve_mode.strip().lower() != "desktop":
        raise HTTPException(status_code=404, detail="仅桌面本机可用")


@auth_router.get("/desktop/session")
async def desktop_session() -> dict:
    _require_desktop_mode()
    from src.server.auth.desktop_account import public_desktop_account

    return public_desktop_account()


@auth_router.post("/desktop/session")
async def desktop_login(data: DesktopLoginRequest) -> dict:
    """向托管服务登录，并把令牌只写在本机用户数据目录。"""

    _require_desktop_mode()
    import httpx

    from src.server.auth.desktop_account import save_desktop_account

    url = data.base_url.rstrip("/") + "/api/auth/login"
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            url,
            json={"email": data.email, "password": data.password, "clientKind": "desktop"},
        )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="托管登录失败")
    body = response.json()
    token = str(body.get("token") or "")
    if not token:
        raise HTTPException(status_code=502, detail="托管服务没有返回桌面令牌")
    email = str((body.get("user") or {}).get("email") or data.email)
    save_desktop_account(base_url=data.base_url, token=token, email=email)
    return {"connected": True, "email": email}


@auth_router.delete("/desktop/session")
async def desktop_logout() -> dict[str, bool]:
    _require_desktop_mode()
    from src.server.auth.desktop_account import clear_desktop_account

    clear_desktop_account()
    return {"ok": True}


@auth_router.get("/config")
async def auth_config() -> dict[str, str]:
    """公开当前服务模式，不返回邮件或模型密钥。"""

    mode = settings.serve_mode.strip().lower() or "desktop"
    return {"serveMode": mode if mode in {"desktop", "hosted"} else "desktop"}


@auth_router.post("/register", dependencies=_HOSTED)
async def register(data: RegisterRequest) -> dict[str, bool]:
    try:
        register_user(data.email, data.password)
    except AuthError as error:
        _raise(error)
    return {"ok": True}


@auth_router.post("/verify", dependencies=_HOSTED)
async def verify(data: VerifyRequest) -> dict:
    try:
        return {"user": verify_email(data.email, data.code)}
    except AuthError as error:
        _raise(error)


@auth_router.post("/login", dependencies=_HOSTED)
async def login(data: LoginRequest, request: Request, response: Response) -> dict:
    try:
        result = login_user(data.email, data.password, client_kind=data.client_kind)
    except AuthError as error:
        _raise(error)
    body: dict = {"user": result["user"]}
    if result["clientKind"] == "desktop":
        body["token"] = result["token"]
    else:
        _set_session_cookie(response, result["token"], request)
    return body


@auth_router.post("/logout", dependencies=_HOSTED)
async def logout(request: Request, response: Response) -> dict[str, bool]:
    logout_token(token_from_request(request))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@auth_router.get("/me", dependencies=_HOSTED)
async def me(request: Request) -> dict:
    return {"user": require_user(request)["user"]}


@auth_router.get("/usage", dependencies=_HOSTED)
async def usage(request: Request) -> dict:
    """当前登录用户自己的平台额度。不返回 Key 或服务令牌。"""

    from src.server.auth.key2api import Key2ApiError, fetch_account_usage

    user = require_user(request)["user"]
    try:
        snapshot = fetch_account_usage(str(user["id"]))
    except Key2ApiError:
        return {"available": False}
    if snapshot is None:
        return {"available": False}
    return {"available": True, **snapshot}


@auth_router.post("/forgot-password", dependencies=_HOSTED)
async def forgot_password(data: EmailRequest) -> dict[str, bool]:
    request_password_reset(data.email)
    return {"ok": True}


@auth_router.post("/reset-password", dependencies=_HOSTED)
async def reset(data: ResetPasswordRequest) -> dict[str, bool]:
    try:
        reset_password(data.email, data.code, data.password)
    except AuthError as error:
        _raise(error)
    return {"ok": True}


@auth_router.post("/heartbeat", dependencies=_HOSTED)
async def post_heartbeat(request: Request) -> dict:
    try:
        return {"user": heartbeat(token_from_request(request))}
    except AuthError as error:
        _raise(error)


@admin_router.get("/users")
async def users(request: Request) -> dict:
    require_admin(request)
    return admin_directory()


@admin_router.post("/users/{user_id}/disable")
async def disable(user_id: str, request: Request) -> dict:
    actor = require_admin(request)
    try:
        return {"user": disable_user(actor["user"]["id"], user_id)}
    except AuthError as error:
        _raise(error)


@admin_router.delete("/users/{user_id}")
async def remove_user(user_id: str, request: Request) -> dict[str, bool]:
    actor = require_admin(request)
    try:
        delete_user(actor["user"]["id"], user_id)
    except AuthError as error:
        _raise(error)
    return {"ok": True}


@admin_router.post("/users/{user_id}/enable")
async def enable(user_id: str, request: Request) -> dict:
    require_admin(request)
    try:
        return {"user": enable_user(user_id)}
    except AuthError as error:
        _raise(error)
