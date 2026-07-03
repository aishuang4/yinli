"""向量存储策略初始化与锁定。

`VECTOR_STORE_PROVIDER` 只在首次初始化时生效；一旦策略写入 SQLite，后续启动必须读取
持久化策略，避免环境变量静默改变底层 provider 或 collection 结构。
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from src.storage.database import BACKEND_ROOT, get_json_record, upsert_json_record

VECTOR_STORE_NAMESPACE = "vector_store"
VECTOR_STORE_POLICY_KEY = "policy"
VECTOR_STORE_POLICY_VERSION = 1

SUPPORTED_VECTOR_PROVIDERS = {"disabled", "zvec"}
SUPPORTED_EMBEDDING_PROVIDERS = {"disabled", "openai_compatible"}


@dataclass(frozen=True)
class VectorStorePolicy:
    """持久化的向量存储策略。"""

    version: int
    provider: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int | None
    retrieval_level: int
    schema_version: int = 1

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VectorStorePolicy":
        """从 SQLite JSON 文档恢复策略。"""

        dimensions = data.get("embedding_dimensions")
        if dimensions is None:
            dimensions = data.get("embeddingDimensions")
        return cls(
            version=int(data.get("version") or VECTOR_STORE_POLICY_VERSION),
            provider=_parse_stored_provider(data.get("provider")),
            embedding_provider=_parse_stored_embedding_provider(
                data.get("embedding_provider") or data.get("embeddingProvider"),
            ),
            embedding_model=str(data.get("embedding_model") or data.get("embeddingModel") or ""),
            embedding_dimensions=_normalize_dimensions(dimensions),
            retrieval_level=int(data.get("retrieval_level") or data.get("retrievalLevel") or 0),
            schema_version=int(data.get("schema_version") or data.get("schemaVersion") or 1),
        )

    def to_dict(self) -> dict[str, Any]:
        """转换为稳定的 JSON 文档。"""

        return asdict(self)


def get_vector_store_policy() -> VectorStorePolicy:
    """读取或首次初始化向量存储策略。"""

    stored = get_json_record(VECTOR_STORE_NAMESPACE, VECTOR_STORE_POLICY_KEY)
    if stored:
        return VectorStorePolicy.from_dict(stored)

    policy = _policy_from_environment()
    upsert_json_record(VECTOR_STORE_NAMESPACE, VECTOR_STORE_POLICY_KEY, policy.to_dict())
    return policy


def _policy_from_environment() -> VectorStorePolicy:
    env = _merged_env()
    provider = _normalize_provider(env.get("VECTOR_STORE_PROVIDER"))
    embedding_provider = _normalize_embedding_provider(env.get("EMBEDDING_PROVIDER"))
    embedding_model = str(env.get("EMBEDDING_MODEL") or "").strip()
    embedding_dimensions = _normalize_dimensions(env.get("EMBEDDING_DIMENSIONS"))
    retrieval_level = _resolve_retrieval_level(provider, embedding_provider, embedding_dimensions)
    return VectorStorePolicy(
        version=VECTOR_STORE_POLICY_VERSION,
        provider=provider,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
        retrieval_level=retrieval_level,
        schema_version=1,
    )


def _merged_env() -> dict[str, str]:
    """读取真实环境变量，并补充 backend/.env 中未导出的配置。"""

    result = {
        key: value
        for key, value in os.environ.items()
        if isinstance(value, str)
    }
    env_path = Path(BACKEND_ROOT) / ".env"
    for key, value in dotenv_values(env_path).items():
        if value is not None and key not in result:
            result[key] = value
    return result


def _normalize_provider(value: Any) -> str:
    provider = str(value or "zvec").strip().lower()
    if provider not in SUPPORTED_VECTOR_PROVIDERS:
        return "zvec"
    return provider


def _parse_stored_provider(value: Any) -> str:
    provider = str(value or "").strip().lower()
    if provider not in SUPPORTED_VECTOR_PROVIDERS:
        raise ValueError(f"Invalid persisted vector store provider: {provider}")
    return provider


def _normalize_embedding_provider(value: Any) -> str:
    provider = str(value or "disabled").strip().lower()
    if provider not in SUPPORTED_EMBEDDING_PROVIDERS:
        return "disabled"
    return provider


def _parse_stored_embedding_provider(value: Any) -> str:
    provider = str(value or "").strip().lower()
    if provider not in SUPPORTED_EMBEDDING_PROVIDERS:
        raise ValueError(f"Invalid persisted embedding provider: {provider}")
    return provider


def _normalize_dimensions(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        dimensions = int(value)
    except (TypeError, ValueError):
        return None
    if dimensions <= 0:
        return None
    return dimensions


def _resolve_retrieval_level(
    provider: str,
    embedding_provider: str,
    embedding_dimensions: int | None,
) -> int:
    if provider == "disabled":
        return 0
    if embedding_provider == "disabled" or embedding_dimensions is None:
        return 1
    return 2
