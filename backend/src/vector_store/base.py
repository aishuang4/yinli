"""向量存储抽象接口。

这里定义的是业务无关的 collection 级接口，Memory 只是其中一个调用方。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


ScalarValue = str | int | float | bool | None


@dataclass(frozen=True)
class VectorStoreCapabilities:
    """描述当前向量存储可用能力。"""

    dense_vector: bool = False
    sparse_vector: bool = False
    bm25: bool = False
    scalar_filter: bool = False


@dataclass(frozen=True)
class VectorCollectionConfig:
    """collection 结构配置。"""

    name: str
    schema_version: int = 1
    embedding_dimensions: int | None = None
    metadata_schema: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class VectorDocument:
    """写入向量存储的通用文档。"""

    id: str
    text: str
    metadata: dict[str, ScalarValue] = field(default_factory=dict)
    dense_vector: list[float] | None = None
    sparse_vector: dict[str, float] | None = None


@dataclass(frozen=True)
class VectorQuery:
    """向量存储查询请求。"""

    collection: str
    text: str = ""
    dense_vector: list[float] | None = None
    sparse_vector: dict[str, float] | None = None
    filters: dict[str, ScalarValue] = field(default_factory=dict)
    limit: int = 10


@dataclass(frozen=True)
class VectorQueryResult:
    """向量存储召回结果。"""

    id: str
    score: float
    text: str
    metadata: dict[str, ScalarValue] = field(default_factory=dict)


class VectorStoreBackend(Protocol):
    """所有向量存储 provider 必须实现的接口。"""

    @property
    def provider(self) -> str:
        """Provider 标识。"""

    @property
    def capabilities(self) -> VectorStoreCapabilities:
        """当前 backend 的能力集合。"""

    def ensure_collection(self, config: VectorCollectionConfig) -> None:
        """确保 collection 存在且结构兼容。"""

    def upsert(self, collection: str, documents: list[VectorDocument]) -> None:
        """写入或更新文档。"""

    def delete(self, collection: str, ids: list[str]) -> None:
        """按 ID 删除文档。"""

    def query(self, query: VectorQuery) -> list[VectorQueryResult]:
        """查询 collection。"""
