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
    from src.server.auth.cloud_client import public_account

    return public_account()


@auth_router.post("/desktop/session")
async def desktop_login(data: DesktopLoginRequest) -> dict:
    """向托管服务登录。令牌写入本机密钥存储，不进 models.json。"""

    _require_desktop_mode()
    from src.server.auth.cloud_client import CloudSessionError, login_cloud

    try:
        return await login_cloud(base_url=data.base_url, email=data.email, password=data.password)
    except CloudSessionError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from None


@auth_router.delete("/desktop/session")
async def desktop_logout() -> dict[str, bool]:
    _require_desktop_mode()
    from src.server.auth.cloud_client import logout_cloud

    await logout_cloud()
    return {"ok": True}


@auth_router.get("/config")
async def auth_config() -> dict:
    """公开当前服务模式，不返回邮件或模型密钥。"""

    mode = settings.serve_mode.strip().lower() or "desktop"
    resolved = mode if mode in {"desktop", "hosted"} else "desktop"
    from src.tenancy.capabilities import capabilities

    return {"serveMode": resolved, "capabilities": capabilities().as_public()}


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


@admin_router.get("/platform-models")
async def read_platform_models(request: Request) -> dict:
    require_admin(request)
    from src.models.platform_layer import read_platform_capability_document

    return read_platform_capability_document()


@admin_router.put("/platform-models")
async def write_platform_models(request: Request) -> dict:
    require_admin(request)
    from src.models.platform_layer import save_platform_capability_document

    try:
        payload = await request.json()
    except (UnicodeError, ValueError):
        raise HTTPException(status_code=422, detail="平台模型能力表格式无效") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="平台模型能力表格式无效")
    try:
        return save_platform_capability_document(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


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
