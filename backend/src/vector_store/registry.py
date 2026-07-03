"""向量存储 provider 注册与创建。"""

from __future__ import annotations

from functools import lru_cache

from src.vector_store.base import VectorStoreBackend
from src.vector_store.policy import VectorStorePolicy, get_vector_store_policy
from src.vector_store.providers.disabled import DisabledVectorStoreBackend
from src.vector_store.providers.zvec import ZvecVectorStoreBackend


@lru_cache(maxsize=1)
def get_vector_store_backend() -> VectorStoreBackend:
    """按持久化策略创建全局向量存储 backend。"""

    return create_vector_store_backend(get_vector_store_policy())


def create_vector_store_backend(policy: VectorStorePolicy) -> VectorStoreBackend:
    """按策略创建 provider 实例。"""

    if policy.provider == "disabled":
        return DisabledVectorStoreBackend()
    if policy.provider == "zvec":
        return ZvecVectorStoreBackend(policy)
    raise ValueError(f"Unsupported vector store provider: {policy.provider}")
