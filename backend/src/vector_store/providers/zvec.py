"""Zvec 向量存储 provider 适配层。"""

from __future__ import annotations

import json
import re
import zlib
from pathlib import Path
from typing import Any

from src.config.runtime_paths import VECTOR_STORE_ROOT
from src.vector_store.base import (
    ScalarValue,
    VectorCollectionConfig,
    VectorDocument,
    VectorQuery,
    VectorQueryResult,
    VectorStoreCapabilities,
)
from src.vector_store.policy import VectorStorePolicy

TEXT_FIELD = "text"
METADATA_FIELD = "metadata_json"
DENSE_VECTOR_FIELD = "dense_vector"
SPARSE_VECTOR_FIELD = "sparse_vector"


class ZvecVectorStoreBackend:
    """Zvec provider 适配。"""

    provider = "zvec"

    def __init__(self, policy: VectorStorePolicy):
        self.policy = policy
        self.capabilities = VectorStoreCapabilities(
            dense_vector=policy.retrieval_level >= 2,
            sparse_vector=True,
            bm25=True,
            scalar_filter=True,
        )
        self._zvec_module: object | None = None
        self._collections: dict[str, Any] = {}

    def ensure_collection(self, config: VectorCollectionConfig) -> None:
        self._collection(config.name, config)

    def upsert(self, collection: str, documents: list[VectorDocument]) -> None:
        if not documents:
            return
        coll = self._collection(collection)
        coll.upsert([self._to_zvec_doc(doc) for doc in documents])
        self._flush(coll)

    def delete(self, collection: str, ids: list[str]) -> None:
        if not ids:
            return
        coll = self._collection(collection)
        coll.delete(ids)
        self._flush(coll)

    def query(self, query: VectorQuery) -> list[VectorQueryResult]:
        coll = self._collection(query.collection)
        zvec_query = self._to_zvec_query(query)
        if zvec_query is None:
            return []
        docs = coll.query(
            queries=zvec_query,
            topk=max(1, query.limit),
            filter=self._filter_expression(query.filters),
            include_vector=False,
        )
        return [self._from_zvec_doc(doc) for doc in docs]

    def _collection(
        self,
        collection: str,
        config: VectorCollectionConfig | None = None,
    ) -> Any:
        if collection in self._collections:
            return self._collections[collection]

        zvec = self._require_zvec_module()
        path = self._collection_path(collection)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            coll = zvec.open(str(path))
        except Exception:
            if config is None:
                config = VectorCollectionConfig(name=collection)
            coll = zvec.create_and_open(str(path), self._schema(zvec, config))
        self._collections[collection] = coll
        return coll

    def _schema(self, zvec: Any, config: VectorCollectionConfig) -> Any:
        fields = [
            zvec.FieldSchema(
                TEXT_FIELD,
                zvec.DataType.STRING,
                nullable=False,
                index_param=zvec.FtsIndexParam(tokenizer_name="jieba"),
            ),
            zvec.FieldSchema(METADATA_FIELD, zvec.DataType.STRING, nullable=False),
        ]
        fields.extend(
            zvec.FieldSchema(
                name,
                self._data_type(zvec, kind),
                nullable=True,
                index_param=zvec.InvertIndexParam(enable_range_optimization=True),
            )
            for name, kind in sorted(config.metadata_schema.items())
        )

        vectors = []
        if config.embedding_dimensions:
            vectors.append(
                zvec.VectorSchema(
                    DENSE_VECTOR_FIELD,
                    zvec.DataType.VECTOR_FP32,
                    dimension=config.embedding_dimensions,
                ),
            )
        vectors.append(
            zvec.VectorSchema(
                SPARSE_VECTOR_FIELD,
                zvec.DataType.SPARSE_VECTOR_FP32,
                dimension=0,
            ),
        )
        return zvec.CollectionSchema(name=config.name, fields=fields, vectors=vectors)

    def _to_zvec_doc(self, document: VectorDocument) -> Any:
        zvec = self._require_zvec_module()
        fields: dict[str, Any] = {
            TEXT_FIELD: document.text,
            METADATA_FIELD: json.dumps(document.metadata, ensure_ascii=False, sort_keys=True),
        }
        fields.update(document.metadata)
        vectors: dict[str, Any] = {}
        if document.dense_vector is not None:
            vectors[DENSE_VECTOR_FIELD] = document.dense_vector
        vectors[SPARSE_VECTOR_FIELD] = _normalize_sparse_vector(document.sparse_vector or {})
        return zvec.Doc(id=document.id, vectors=vectors or None, fields=fields)

    def _to_zvec_query(self, query: VectorQuery) -> Any | None:
        zvec = self._require_zvec_module()
        if query.dense_vector is not None:
            return zvec.Query(field_name=DENSE_VECTOR_FIELD, vector=query.dense_vector)
        if query.sparse_vector is not None:
            return zvec.Query(
                field_name=SPARSE_VECTOR_FIELD,
                vector=_normalize_sparse_vector(query.sparse_vector),
            )
        if query.text.strip():
            return zvec.Query(field_name=TEXT_FIELD, fts=zvec.Fts(match_string=query.text.strip()))
        return None

    def _from_zvec_doc(self, doc: Any) -> VectorQueryResult:
        fields = self._doc_fields(doc)
        metadata = self._metadata_from_fields(fields)
        return VectorQueryResult(
            id=str(getattr(doc, "id", "")),
            score=float(getattr(doc, "score", 0) or 0),
            text=str(fields.get(TEXT_FIELD) or ""),
            metadata=metadata,
        )

    @staticmethod
    def _doc_fields(doc: Any) -> dict[str, Any]:
        fields = getattr(doc, "fields", None)
        if isinstance(fields, dict):
            return fields
        return {}

    @staticmethod
    def _metadata_from_fields(fields: dict[str, Any]) -> dict[str, ScalarValue]:
        raw = fields.get(METADATA_FIELD)
        if isinstance(raw, str):
            try:
                loaded = json.loads(raw)
            except json.JSONDecodeError:
                loaded = {}
            if isinstance(loaded, dict):
                return {
                    str(key): value
                    for key, value in loaded.items()
                    if value is None or isinstance(value, (str, int, float, bool))
                }
        return {
            key: value
            for key, value in fields.items()
            if key not in {TEXT_FIELD, METADATA_FIELD}
            and (value is None or isinstance(value, (str, int, float, bool)))
        }

    @staticmethod
    def _data_type(zvec: Any, kind: str) -> Any:
        normalized = kind.strip().lower()
        if normalized in {"int", "integer", "int64"}:
            return zvec.DataType.INT64
        if normalized in {"float", "double", "number"}:
            return zvec.DataType.DOUBLE
        if normalized in {"bool", "boolean"}:
            return zvec.DataType.BOOL
        return zvec.DataType.STRING

    @staticmethod
    def _filter_expression(filters: dict[str, ScalarValue]) -> str | None:
        parts = [
            f"{key} = {ZvecVectorStoreBackend._filter_value(value)}"
            for key, value in sorted(filters.items())
            if _is_safe_field_name(key)
        ]
        return " AND ".join(parts) if parts else None

    @staticmethod
    def _filter_value(value: ScalarValue) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

    @staticmethod
    def _collection_path(collection: str) -> Path:
        return VECTOR_STORE_ROOT / "zvec" / _safe_collection_name(collection)

    @staticmethod
    def _flush(collection: Any) -> None:
        flush = getattr(collection, "flush", None)
        if callable(flush):
            flush()

    def _require_zvec_module(self) -> object:
        if self._zvec_module is not None:
            return self._zvec_module
        try:
            import zvec  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "VECTOR_STORE_PROVIDER=zvec 已锁定，但当前 Python 环境未安装 zvec。"
                "请安装 zvec 后再启用该 provider，或通过显式迁移切换 provider。"
            ) from exc
        self._zvec_module = zvec
        return zvec


def _safe_collection_name(collection: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "_", collection.strip())
    return normalized[:120] or "default"


def _is_safe_field_name(value: str) -> bool:
    return re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", value) is not None


def _normalize_sparse_vector(vector: dict[str, float]) -> dict[int, float]:
    return {
        _sparse_key_to_uint32(key): float(value)
        for key, value in vector.items()
    }


def _sparse_key_to_uint32(key: str) -> int:
    try:
        value = int(key)
    except ValueError:
        return zlib.crc32(key.encode("utf-8"))
    if value < 0:
        return zlib.crc32(key.encode("utf-8"))
    return value % (2**32)
