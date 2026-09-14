"""Catalog 扫描结果类型。"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.catalog.agent_models import AgentResponse


@dataclass
class ProfileParseResult:
    """单个 profile 文件的解析结果。"""

    agent: AgentResponse | None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class CatalogError:
    """扫描期错误，写入 INDEX。"""

    path: str
    message: str
    agent_id: str = ""


@dataclass
class CatalogSnapshot:
    """一次扫描的缓存快照。"""

    fingerprint: str
    agents: dict[str, AgentResponse]
    errors: list[CatalogError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    invalid_by_id: dict[str, str] = field(default_factory=dict)
