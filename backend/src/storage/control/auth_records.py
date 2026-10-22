"""账号、会话和验证码的 SQLite 读写。令牌与验证码只存摘要。"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from src.server.auth.secrets import hash_secret
from src.storage.engine import database
from src.storage.control import control_db

SESSION_TTL = timedelta(days=14)
DESKTOP_SESSION_TTL = timedelta(days=30)
CODE_TTL = timedelta(minutes=15)
ONLINE_WINDOW = timedelta(seconds=90)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _user_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "passwordHash": row["password_hash"],
        "role": row["role"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "verifiedAt": row["verified_at"],
        "disabledAt": row["disabled_at"],
    }


def create_user(*, email: str, password_hash: str, role: str = "user") -> dict[str, Any]:
    """插入待验证用户。"""

    control_db.ensure_control_db()
    user_id = f"usr_{uuid.uuid4().hex}"
    created_at = database._utc_now()
    with control_db._control_lock, control_db.control_connect() as conn:
        conn.execute(
            """
            INSERT INTO auth_users (
                id, email, password_hash, role, status, created_at, verified_at, disabled_at
            ) VALUES (?, ?, ?, ?, 'pending', ?, NULL, NULL)
            """,
            (user_id, email, password_hash, role, created_at),
        )
        row = conn.execute("SELECT * FROM auth_users WHERE id = ?", (user_id,)).fetchone()
    return _user_row(row)


def get_user_by_email(email: str) -> dict[str, Any] | None:
    control_db.ensure_control_db()
    with control_db._control_lock, control_db.control_connect() as conn:
        row = conn.execute("SELECT * FROM auth_users WHERE email = ?", (email,)).fetchone()
    return _user_row(row) if row else None


def get_user(user_id: str) -> dict[str, Any] | None:
    control_db.ensure_control_db()
    with control_db._control_lock, control_db.control_connect() as conn:
        row = conn.execute("SELECT * FROM auth_users WHERE id = ?", (user_id,)).fetchone()
    return _user_row(row) if row else None


def update_user(user_id: str, **fields: Any) -> dict[str, Any] | None:
    """按白名单更新用户状态、角色和验证时间。"""

    allowed = {
        "role": "role",
        "status": "status",
        "password_hash": "password_hash",
        "verified_at": "verified_at",
        "disabled_at": "disabled_at",
    }
    assignments = []
    params: list[Any] = []
    for key, column in allowed.items():
        if key in fields:
            assignments.append(f"{column} = ?")
            params.append(fields[key])
    if not assignments:
        return get_user(user_id)
    params.append(user_id)
    control_db.ensure_control_db()
    with control_db._control_lock, control_db.control_connect() as conn:
        conn.execute(
            f"UPDATE auth_users SET {', '.join(assignments)} WHERE id = ?",
            params,
        )
        row = conn.execute("SELECT * FROM auth_users WHERE id = ?", (user_id,)).fetchone()
    return _user_row(row) if row else None


def list_users() -> list[dict[str, Any]]:
    control_db.ensure_control_db()
    with control_db._control_lock, control_db.control_connect() as conn:
        rows = conn.execute("SELECT * FROM auth_users ORDER BY created_at ASC").fetchall()
    return [_user_row(row) for row in rows]


def promote_configured_admins(emails: set[str]) -> int:
    """把已经可登录、且邮箱在管理员名单里的账号标成管理员。"""

    if not emails:
        return 0
    control_db.ensure_control_db()
    with control_db._control_lock, control_db.control_connect() as conn:
        changed = 0
        for email in emails:
            cursor = conn.execute(
                """
                UPDATE auth_users
                SET role = 'admin'
                WHERE email = ? AND status = 'active' AND role != 'admin'
                """,
                (email,),
            )
            changed += cursor.rowcount
    return changed


def save_email_code(*, email: str, purpose: str, raw_code: str) -> None:
    """保存验证码摘要。明文只存在于调用方的发信过程。"""

    control_db.ensure_control_db()
    expires_at = _iso(_now() + CODE_TTL)
    with control_db._control_lock, control_db.control_connect() as conn:
        conn.execute(
            """
            INSERT INTO auth_email_codes (
                id, email, purpose, code_hash, expires_at, used_at, created_at
            ) VALUES (?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                f"code_{uuid.uuid4().hex}",
                email,
                purpose,
                hash_secret(raw_code),
                expires_at,
                database._utc_now(),
            ),
        )


def consume_email_code(*, email: str, purpose: str, raw_code: str) -> bool:
    """核销未过期验证码。错误、过期或已使用返回 False。"""

    control_db.ensure_control_db()
    now = _now()
    digest = hash_secret(raw_code)
    with control_db._control_lock, control_db.control_connect() as conn:
        row = conn.execute(
            """
            SELECT id, expires_at FROM auth_email_codes
            WHERE email = ? AND purpose = ? AND code_hash = ? AND used_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (email, purpose, digest),
        ).fetchone()
        if row is None:
            return False
        expires_at = _parse(row["expires_at"])
        if expires_at is None or expires_at < now:
            return False
        conn.execute(
            "UPDATE auth_email_codes SET used_at = ? WHERE id = ?",
            (database._utc_now(), row["id"]),
        )
    return True


def create_session(*, user_id: str, raw_token: str, client_kind: str) -> dict[str, Any]:
    """保存会话摘要。"""

    control_db.ensure_control_db()
    now = _now()
    session_id = f"ses_{uuid.uuid4().hex}"
    with control_db._control_lock, control_db.control_connect() as conn:
        conn.execute(
            """
            INSERT INTO auth_sessions (
                id, user_id, token_hash, client_kind, expires_at, last_seen_at, revoked_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                session_id,
                user_id,
                hash_secret(raw_token),
                client_kind,
                _iso(now + (DESKTOP_SESSION_TTL if client_kind == "desktop" else SESSION_TTL)),
                _iso(now),
                _iso(now),
            ),
        )
    return {"id": session_id, "userId": user_id, "clientKind": client_kind}


def get_session_by_token(raw_token: str) -> dict[str, Any] | None:
    """读取未撤销且未过期的会话，不更新最近活跃时间。"""

    if not raw_token:
        return None
    control_db.ensure_control_db()
    now = _now()
    with control_db._control_lock, control_db.control_connect() as conn:
        row = conn.execute(
            "SELECT * FROM auth_sessions WHERE token_hash = ?",
            (hash_secret(raw_token),),
        ).fetchone()
    if row is None or row["revoked_at"]:
        return None
    expires_at = _parse(row["expires_at"])
    if expires_at is None or expires_at < now:
        return None
    return _session_row(row)


def touch_session(session_id: str) -> None:
    """刷新最近活跃时间。桌面令牌顺延 30 天，Web 顺延 14 天。"""

    control_db.ensure_control_db()
    now = _now()
    with control_db._control_lock, control_db.control_connect() as conn:
        row = conn.execute(
            "SELECT client_kind FROM auth_sessions WHERE id = ? AND revoked_at IS NULL",
            (session_id,),
        ).fetchone()
        if row is None:
            return
        ttl = DESKTOP_SESSION_TTL if row["client_kind"] == "desktop" else SESSION_TTL
        conn.execute(
            "UPDATE auth_sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
            (_iso(now), _iso(now + ttl), session_id),
        )


def revoke_session(session_id: str) -> None:
    control_db.ensure_control_db()
    with control_db._control_lock, control_db.control_connect() as conn:
        conn.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (database._utc_now(), session_id),
        )


def delete_account(user_id: str, email: str) -> None:
    """删除账号、会话、验证码和平台 Key。"""

    control_db.ensure_control_db()
    with control_db._control_lock, control_db.control_connect() as conn:
        conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM auth_provider_keys WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM auth_email_codes WHERE email = ?", (email,))
        conn.execute("DELETE FROM auth_users WHERE id = ?", (user_id,))


def revoke_user_sessions(user_id: str) -> None:
    control_db.ensure_control_db()
    with control_db._control_lock, control_db.control_connect() as conn:
        conn.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (database._utc_now(), user_id),
        )


def list_sessions() -> list[dict[str, Any]]:
    control_db.ensure_control_db()
    with control_db._control_lock, control_db.control_connect() as conn:
        rows = conn.execute("SELECT * FROM auth_sessions").fetchall()
    return [_session_row(row) for row in rows]


def _session_row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "tokenHash": row["token_hash"],
        "clientKind": row["client_kind"],
        "expiresAt": row["expires_at"],
        "lastSeenAt": row["last_seen_at"],
        "revokedAt": row["revoked_at"],
        "createdAt": row["created_at"],
    }


def session_is_recent(session: dict[str, Any], *, now: datetime | None = None) -> bool:
    """未撤销、未过期，且最近活跃落在在线窗口内。"""

    current = now or _now()
    if session.get("revokedAt"):
        return False
    expires_at = _parse(str(session.get("expiresAt") or ""))
    seen = _parse(str(session.get("lastSeenAt") or ""))
    if expires_at is None or seen is None or expires_at < current:
        return False
    return current - seen <= ONLINE_WINDOW


def get_provider_key(user_id: str) -> dict[str, Any] | None:
    """读取用户的平台 Key。只给服务端调用，不能放进接口响应。"""

    control_db.ensure_control_db()
    with control_db._control_lock, control_db.control_connect() as conn:
        row = conn.execute(
            "SELECT * FROM auth_provider_keys WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "userId": row["user_id"],
        "token": row["token"],
        "key2apiUserId": row["key2api_user_id"],
        "key2apiTokenId": row["key2api_token_id"],
        "status": row["status"],
        "updatedAt": row["updated_at"],
    }


def save_provider_key(
    *,
    user_id: str,
    token: str,
    key2api_user_id: str,
    key2api_token_id: str,
    status: str,
) -> None:
    """保存或更新平台 Key。明文只留在这张表。"""

    control_db.ensure_control_db()
    with control_db._control_lock, control_db.control_connect() as conn:
        conn.execute(
            """
            INSERT INTO auth_provider_keys (
                user_id, token, key2api_user_id, key2api_token_id, status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                token = excluded.token,
                key2api_user_id = excluded.key2api_user_id,
                key2api_token_id = excluded.key2api_token_id,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (user_id, token, key2api_user_id, key2api_token_id, status, database._utc_now()),
        )
