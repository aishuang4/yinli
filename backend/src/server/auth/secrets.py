"""高熵令牌与验证码的单向摘要。"""

import hashlib
import secrets


def new_token() -> str:
    """生成会话令牌。调用方只把摘要写入数据库。"""

    return secrets.token_urlsafe(32)


def new_code() -> str:
    """生成 6 位数字验证码。"""

    return f"{secrets.randbelow(1_000_000):06d}"


def hash_secret(value: str) -> str:
    """对令牌或验证码做 SHA-256。"""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()
