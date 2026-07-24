"""进程内 Runtime 源信号；不持久化，也不是 SSE 契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SourceEvent:
    """AgentScope/provider 与 projection normalizer 之间的结构化信号。"""

    event_type: str
    data: dict[str, Any]
