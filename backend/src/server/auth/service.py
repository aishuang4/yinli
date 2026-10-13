"""注册、验证、登录和账号状态变更。"""

from __future__ import annotations

from typing import Any

from src.config.settings import parsed_admin_emails
from src.server.auth.mail import deliver_login_code
from src.server.auth.passwords import hash_password, verify_password
from src.server.auth.secrets import new_code, new_token
from src.storage import auth_records

MIN_PASSWORD_LENGTH = 8


class AuthError(Exception):
    """可返回给客户端的账号错误。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def normalize_email(email: str) -> str:
    return str(email or "").strip().lower()


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    """去掉密码哈希后的用户资料。"""

    return {
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "status": user["status"],
    }


def _password_ok(password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(422, "密码至少 8 位")


def _email_ok(email: str) -> str:
    normalized = normalize_email(email)
    if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise AuthError(422, "邮箱格式不正确")
    return normalized


def register_user(email: str, password: str) -> None:
    """注册待验证账号。已存在邮箱返回同样的成功，不新建可登录账号。"""

    normalized = _email_ok(email)
    _password_ok(password)
    existing = auth_records.get_user_by_email(normalized)
    if existing is not None:
        return
    auth_records.create_user(email=normalized, password_hash=hash_password(password))
    code = new_code()
    auth_records.save_email_code(email=normalized, purpose="register", raw_code=code)
    deliver_login_code(normalized, "register", code)


def verify_email(email: str, code: str) -> dict[str, Any]:
    normalized = _email_ok(email)
    user = auth_records.get_user_by_email(normalized)
    if user is None or not auth_records.consume_email_code(
        email=normalized, purpose="register", raw_code=code.strip(),
    ):
        raise AuthError(400, "验证码不正确或已过期")
    verified = _mark_active(user)
    from src.tenancy.lifecycle import ensure_tenant

    ensure_tenant(verified)
    return public_user(verified)


def login_user(email: str, password: str, *, client_kind: str) -> dict[str, Any]:
    """校验密码并创建会话。调用方决定写 Cookie 还是返回令牌。"""

    normalized = _email_ok(email)
    kind = client_kind if client_kind in {"web", "desktop"} else "web"
    user = auth_records.get_user_by_email(normalized)
    if (
        user is None
        or user["status"] != "active"
        or not verify_password(password, str(user["passwordHash"]))
    ):
        raise AuthError(401, "邮箱或密码不正确")
    token = new_token()
    session = auth_records.create_session(user_id=user["id"], raw_token=token, client_kind=kind)
    from src.tenancy.lifecycle import ensure_tenant

    ensure_tenant(user)
    return {"user": public_user(user), "token": token, "sessionId": session["id"], "clientKind": kind}


def logout_token(raw_token: str) -> None:
    session = auth_records.get_session_by_token(raw_token)
    if session is not None:
        auth_records.revoke_session(session["id"])


def current_user(raw_token: str) -> dict[str, Any] | None:
    """返回令牌对应的可登录用户，并刷新最近活跃时间。"""

    session = auth_records.get_session_by_token(raw_token)
    if session is None:
        return None
    user = auth_records.get_user(session["userId"])
    if user is None or user["status"] != "active":
        return None
    auth_records.touch_session(session["id"])
    return {"user": public_user(user), "session": session}


def request_password_reset(email: str) -> None:
    normalized = normalize_email(email)
    user = auth_records.get_user_by_email(normalized) if "@" in normalized else None
    if user is None or user["status"] == "disabled":
        return
    code = new_code()
    auth_records.save_email_code(email=normalized, purpose="reset", raw_code=code)
    deliver_login_code(normalized, "reset", code)


def reset_password(email: str, code: str, password: str) -> None:
    normalized = _email_ok(email)
    _password_ok(password)
    user = auth_records.get_user_by_email(normalized)
    if user is None or user["status"] == "disabled" or not auth_records.consume_email_code(
        email=normalized, purpose="reset", raw_code=code.strip(),
    ):
        raise AuthError(400, "验证码不正确或已过期")
    auth_records.update_user(user["id"], password_hash=hash_password(password))
    _mark_active(user)
    auth_records.revoke_user_sessions(user["id"])


def heartbeat(raw_token: str) -> dict[str, Any]:
    found = current_user(raw_token)
    if found is None:
        raise AuthError(401, "需要登录")
    return found["user"]


def disable_user(actor_id: str, target_id: str) -> dict[str, Any]:
    if actor_id == target_id:
        raise AuthError(409, "不能停用自己")
    target = auth_records.get_user(target_id)
    if target is None:
        raise AuthError(404, "用户不存在")
    from src.tenancy.lifecycle import disable_tenant

    disable_tenant(target_id)
    updated = auth_records.update_user(
        target_id,
        status="disabled",
        disabled_at=auth_records.database._utc_now(),
    )
    return public_user(updated or target)


def delete_user(actor_id: str, target_id: str) -> None:
    """删除其他用户。不能删除自己，也不能删除最后一名管理员。"""

    if actor_id == target_id:
        raise AuthError(409, "不能删除自己")
    target = auth_records.get_user(target_id)
    if target is None:
        raise AuthError(404, "用户不存在")
    if target["role"] == "admin":
        others = [
            item for item in auth_records.list_users()
            if item["role"] == "admin" and item["id"] != target_id
        ]
        if not others:
            raise AuthError(409, "不能删除最后一名管理员")
    from src.tenancy.lifecycle import delete_tenant

    delete_tenant(target_id)


def enable_user(target_id: str) -> dict[str, Any]:
    target = auth_records.get_user(target_id)
    if target is None:
        raise AuthError(404, "用户不存在")
    status = "active" if target.get("verifiedAt") else "pending"
    if status == "active":
        from src.tenancy.lifecycle import enable_tenant

        enable_tenant(target_id)
    updated = auth_records.update_user(target_id, status=status, disabled_at=None)
    return public_user(updated or target)


def admin_directory() -> dict[str, Any]:
    """管理员看到的用户列表和在线汇总。"""

    users = auth_records.list_users()
    sessions = auth_records.list_sessions()
    rows = []
    online_count = 0
    for user in users:
        owned = [item for item in sessions if item["userId"] == user["id"]]
        recent = [item for item in owned if auth_records.session_is_recent(item)]
        last_seen = max((item["lastSeenAt"] for item in owned), default=None)
        clients = sorted({item["clientKind"] for item in recent})
        online = bool(clients)
        if online:
            online_count += 1
        rows.append({
            **public_user(user),
            "createdAt": user["createdAt"],
            "lastSeenAt": last_seen,
            "online": online,
            "clients": clients,
        })
    rows.sort(key=lambda item: item["lastSeenAt"] or "", reverse=True)
    return {
        "summary": {
            "total": len(users),
            "online": online_count,
            "pending": sum(1 for item in users if item["status"] == "pending"),
            "disabled": sum(1 for item in users if item["status"] == "disabled"),
        },
        "users": rows,
    }


DEFAULT_ADMIN_EMAIL = "admin@yinli.test"
DEFAULT_ADMIN_PASSWORD = "yinli-admin"


def ensure_default_admins() -> None:
    """托管启动时保证至少有一个可登录的管理员。已有账号不改密码。"""

    from src.config.settings import settings
    from src.server.auth.passwords import hash_password

    if settings.serve_mode.strip().lower() != "hosted":
        return
    emails = parsed_admin_emails() or {DEFAULT_ADMIN_EMAIL}
    password = settings.admin_initial_password.strip() or DEFAULT_ADMIN_PASSWORD
    for email in sorted(emails):
        existing = auth_records.get_user_by_email(email)
        if existing is None:
            created = auth_records.create_user(
                email=email,
                password_hash=hash_password(password),
                role="admin",
            )
            auth_records.update_user(
                created["id"],
                status="active",
                role="admin",
                verified_at=auth_records.database._utc_now(),
                disabled_at=None,
            )
            print(f"[auth] 已创建默认管理员 {email}")
            from src.server.auth.key2api import ensure_provider_key

            ensure_provider_key(auth_records.get_user(created["id"]) or created)
            continue
        if existing["status"] == "active" and existing["role"] != "admin":
            auth_records.update_user(existing["id"], role="admin")
        if existing["status"] == "active":
            from src.server.auth.key2api import ensure_provider_key

            ensure_provider_key(auth_records.get_user(existing["id"]) or existing)
    promote_configured_admins()


def promote_configured_admins() -> None:
    auth_records.promote_configured_admins(parsed_admin_emails())


def _sync_provider_status(user: dict[str, Any], status: str) -> None:
    from src.server.auth.key2api import Key2ApiError, sync_provider_status

    try:
        sync_provider_status(user, status)
    except Key2ApiError as exc:
        raise AuthError(exc.status, str(exc)) from None


def _mark_active(user: dict[str, Any]) -> dict[str, Any]:
    role = user["role"]
    if user["email"] in parsed_admin_emails():
        role = "admin"
    updated = auth_records.update_user(
        user["id"],
        status="active",
        role=role,
        verified_at=user.get("verifiedAt") or auth_records.database._utc_now(),
        disabled_at=None,
    )
    return updated or user
