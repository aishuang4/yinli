"""Session 領域錯誤。code 給 API 與刪除衝突使用。"""

from __future__ import annotations

from typing import Any


class SessionError(Exception):
    """可預期的 Session 歸屬錯誤。"""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
