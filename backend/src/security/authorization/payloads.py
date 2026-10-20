"""仅存活 Run 可读取的敏感行动参数；普通快照只保存引用和带密钥摘要。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from typing import Any

from src.tenancy.context import current_tenant

from .models import RunAuthorization


@dataclass
class ActionInputVault:
    """不落凭据实值；进程重启失去执行现场后旧引用不可用于重放。"""

    owner: RunAuthorization
    _key: bytes = field(default_factory=lambda: secrets.token_bytes(32), repr=False)
    _values: dict[str, str] = field(default_factory=dict, repr=False)

    def _check_owner(self) -> None:
        if current_tenant().tenant_id != self.owner.tenant_id:
            raise ValueError("敏感行动参数租户不匹配")

    def digest(self, value: str) -> str:
        """HMAC 不暴露短密码/Key 的可枚举裸摘要。"""
        self._check_owner()
        return hmac.new(self._key, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def put(self, canonical: str) -> tuple[str, str]:
        self._check_owner()
        digest = self.digest(canonical)
        ref = f"permission-input:{self.owner.run_id}:{self.owner.actor_id}:{digest}"
        self._values[ref] = canonical
        return ref, digest

    def get(self, ref: str) -> str:
        self._check_owner()
        if ref not in self._values:
            raise ValueError("行动参数现场已失效")
        return self._values[ref]

    def clear(self) -> None:
        """Run 收口后清除原参数与凭据。"""
        self._values.clear()


def public_input(value: Any) -> Any:
    """普通快照仅展示结构，不猜测任意 MCP 字段是否含凭据。"""

    if isinstance(value, dict):
        return {str(key): public_input(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_input(item) for item in value]
    if value is None:
        return None
    return "[内容见受限行动参数]"
