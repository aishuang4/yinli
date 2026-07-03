"""向量存储基础设施。

该包提供全局 `VectorStoreBackend` 抽象，供 Memory、Skill、知识库等模块按 collection 复用。
"""

from src.vector_store.base import (
    VectorCollectionConfig,
    VectorDocument,
    VectorQuery,
    VectorQueryResult,
    VectorStoreBackend,
    VectorStoreCapabilities,
)
from src.vector_store.policy import VectorStorePolicy, get_vector_store_policy
from src.vector_store.registry import get_vector_store_backend

__all__ = [
    "VectorCollectionConfig",
    "VectorDocument",
    "VectorQuery",
    "VectorQueryResult",
    "VectorStoreBackend",
    "VectorStoreCapabilities",
    "VectorStorePolicy",
    "get_vector_store_backend",
    "get_vector_store_policy",
]
