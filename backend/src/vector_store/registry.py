"""向量存储 provider 注册与创建。"""

from __future__ import annotations

from src.tenancy.registry import TenantScoped
from src.vector_store.base import VectorStoreBackend
from src.vector_store.policy import VectorStorePolicy, get_vector_store_policy
from src.vector_store.providers.disabled import DisabledVectorStoreBackend
from src.vector_store.providers.zvec import ZvecVectorStoreBackend

_backends: TenantScoped[VectorStoreBackend] = TenantScoped(
    lambda: create_vector_store_backend(get_vector_store_policy())
)


def get_vector_store_backend() -> VectorStoreBackend:
    """按当前租户的持久化策略创建向量存储 backend。"""

    return _backends.get()


def create_vector_store_backend(policy: VectorStorePolicy) -> VectorStoreBackend:
    """按策略创建 provider 实例。"""

    if policy.provider == "disabled":
        return DisabledVectorStoreBackend()
    if policy.provider == "zvec":
        return ZvecVectorStoreBackend(policy)
    raise ValueError(f"Unsupported vector store provider: {policy.provider}")
