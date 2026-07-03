"""禁用状态下的向量存储降级实现。"""

from __future__ import annotations

from src.vector_store.base import (
    VectorCollectionConfig,
    VectorDocument,
    VectorQuery,
    VectorQueryResult,
    VectorStoreCapabilities,
)


class DisabledVectorStoreBackend:
    """不写入外部向量库的空实现。"""

    provider = "disabled"
    capabilities = VectorStoreCapabilities()

    def ensure_collection(self, config: VectorCollectionConfig) -> None:
        return None

    def upsert(self, collection: str, documents: list[VectorDocument]) -> None:
        return None

    def delete(self, collection: str, ids: list[str]) -> None:
        return None

    def query(self, query: VectorQuery) -> list[VectorQueryResult]:
        return []
