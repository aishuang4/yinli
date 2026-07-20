"""向量存储策略测试。"""

from __future__ import annotations

import pytest

from src.storage import database
from src.vector_store.policy import get_vector_store_policy
from src.vector_store.providers.disabled import DisabledVectorStoreBackend
from src.vector_store.providers.zvec import ZvecVectorStoreBackend
from src.vector_store.registry import create_vector_store_backend


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """使用临时 SQLite，避免污染开发库。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)


def test_vector_store_policy_uses_env_only_on_first_init(isolated_db, monkeypatch):
    monkeypatch.setenv("VECTOR_STORE_PROVIDER", "disabled")

    first = get_vector_store_policy()

    monkeypatch.setenv("VECTOR_STORE_PROVIDER", "zvec")
    second = get_vector_store_policy()

    assert first.provider == "disabled"
    assert first.retrieval_level == 0
    assert second.provider == "disabled"
    assert second.retrieval_level == 0


def test_vector_store_policy_defaults_to_zvec_without_embedding(isolated_db, monkeypatch):
    monkeypatch.delenv("VECTOR_STORE_PROVIDER", raising=False)
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("EMBEDDING_DIMENSIONS", raising=False)

    policy = get_vector_store_policy()

    assert policy.provider == "zvec"
    assert policy.embedding_provider == "disabled"
    assert policy.embedding_dimensions is None
    assert policy.retrieval_level == 1


def test_vector_store_policy_enables_dense_level_with_embedding(isolated_db, monkeypatch):
    monkeypatch.setenv("VECTOR_STORE_PROVIDER", "zvec")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai_compatible")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "1536")

    policy = get_vector_store_policy()

    assert policy.provider == "zvec"
    assert policy.embedding_provider == "openai_compatible"
    assert policy.embedding_dimensions == 1536
    assert policy.retrieval_level == 2


def test_disabled_backend_is_noop():
    backend = create_vector_store_backend(
        get_disabled_policy(),
    )

    assert isinstance(backend, DisabledVectorStoreBackend)
    assert backend.query(query=object()) == []


def test_zvec_backend_creation_is_lazy_without_sdk():
    backend = create_vector_store_backend(
        get_zvec_policy(),
    )

    assert isinstance(backend, ZvecVectorStoreBackend)
    assert backend.capabilities.bm25 is True


def test_zvec_backend_creates_collection_and_upserts_docs(monkeypatch, tmp_path):
    from src.vector_store.providers import zvec as zvec_provider
    from src.vector_store.base import VectorCollectionConfig, VectorDocument

    fake = FakeZvecModule()
    monkeypatch.setattr(zvec_provider, "VECTOR_STORE_ROOT", tmp_path / "data" / "vector_store")
    backend = ZvecVectorStoreBackend(get_zvec_policy())
    backend._zvec_module = fake

    backend.ensure_collection(
        VectorCollectionConfig(
            name="memory_nodes",
            metadata_schema={"agent_id": "string", "created_at": "string"},
        ),
    )
    backend.upsert(
        "memory_nodes",
        [
            VectorDocument(
                id="node-1",
                text="用户想做记忆模块",
                metadata={"agent_id": "agent-1", "created_at": "2026-07-03"},
                sparse_vector={"memory": 1.0},
            ),
        ],
    )

    collection = fake.collections[str(tmp_path / "data/vector_store/zvec/memory_nodes")]
    assert collection.schema.name == "memory_nodes"
    assert collection.upserted[0].id == "node-1"
    assert collection.upserted[0].fields["agent_id"] == "agent-1"
    assert list(collection.upserted[0].vectors["sparse_vector"].values()) == [1.0]
    assert isinstance(next(iter(collection.upserted[0].vectors["sparse_vector"])), int)
    assert collection.flushed is True


def test_zvec_backend_queries_text_and_maps_results(monkeypatch, tmp_path):
    from src.vector_store.providers import zvec as zvec_provider
    from src.vector_store.base import VectorCollectionConfig, VectorQuery

    fake = FakeZvecModule()
    monkeypatch.setattr(zvec_provider, "VECTOR_STORE_ROOT", tmp_path / "data" / "vector_store")
    backend = ZvecVectorStoreBackend(get_zvec_policy())
    backend._zvec_module = fake
    backend.ensure_collection(
        VectorCollectionConfig(
            name="memory_nodes",
            metadata_schema={"agent_id": "string"},
        ),
    )
    collection = fake.collections[str(tmp_path / "data/vector_store/zvec/memory_nodes")]
    collection.query_results = [
        fake.Doc(
            id="node-1",
            score=0.9,
            fields={
                "text": "今天完成了旧记忆清理",
                "metadata_json": '{"agent_id": "agent-1"}',
            },
        ),
    ]

    results = backend.query(
        VectorQuery(
            collection="memory_nodes",
            text="旧记忆",
            filters={"agent_id": "agent-1"},
            limit=3,
        ),
    )

    assert collection.queries[0].fts.match_string == "旧记忆"
    assert collection.query_filters[0] == "agent_id = 'agent-1'"
    assert results[0].id == "node-1"
    assert results[0].score == 0.9
    assert results[0].text == "今天完成了旧记忆清理"
    assert results[0].metadata == {"agent_id": "agent-1"}


def test_zvec_backend_real_fts_query(monkeypatch, tmp_path):
    pytest.importorskip("zvec")
    from src.vector_store.providers import zvec as zvec_provider
    from src.vector_store.base import VectorCollectionConfig, VectorDocument, VectorQuery

    monkeypatch.setattr(zvec_provider, "VECTOR_STORE_ROOT", tmp_path / "data" / "vector_store")
    backend = ZvecVectorStoreBackend(get_zvec_policy())
    backend.ensure_collection(
        VectorCollectionConfig(
            name="memory_nodes_real",
            metadata_schema={"agent_id": "string"},
        ),
    )
    backend.upsert(
        "memory_nodes_real",
        [
            VectorDocument(
                id="node-1",
                text="今天完成了旧记忆清理",
                metadata={"agent_id": "agent-1"},
                sparse_vector={"memory": 1.0},
            ),
        ],
    )

    results = backend.query(
        VectorQuery(
            collection="memory_nodes_real",
            text="旧记忆",
            filters={"agent_id": "agent-1"},
            limit=5,
        ),
    )

    assert [item.id for item in results] == ["node-1"]
    assert results[0].metadata == {"agent_id": "agent-1"}


def test_zvec_backend_real_dense_query(monkeypatch, tmp_path):
    pytest.importorskip("zvec")
    from src.vector_store.providers import zvec as zvec_provider
    from src.vector_store.base import VectorCollectionConfig, VectorDocument, VectorQuery

    monkeypatch.setattr(zvec_provider, "VECTOR_STORE_ROOT", tmp_path / "data" / "vector_store")
    backend = ZvecVectorStoreBackend(get_dense_zvec_policy())
    backend.ensure_collection(
        VectorCollectionConfig(
            name="dense_nodes_real",
            embedding_dimensions=3,
            metadata_schema={"agent_id": "string"},
        ),
    )
    backend.upsert(
        "dense_nodes_real",
        [
            VectorDocument(
                id="node-a",
                text="alpha",
                metadata={"agent_id": "agent-1"},
                dense_vector=[1.0, 0.0, 0.0],
            ),
            VectorDocument(
                id="node-b",
                text="beta",
                metadata={"agent_id": "agent-1"},
                dense_vector=[0.0, 1.0, 0.0],
            ),
        ],
    )

    results = backend.query(
        VectorQuery(
            collection="dense_nodes_real",
            dense_vector=[1.0, 0.0, 0.0],
            filters={"agent_id": "agent-1"},
            limit=2,
        ),
    )

    assert [item.id for item in results] == ["node-a", "node-b"]
    assert results[0].score > results[1].score


def get_disabled_policy():
    from src.vector_store.policy import VectorStorePolicy

    return VectorStorePolicy(
        version=1,
        provider="disabled",
        embedding_provider="disabled",
        embedding_model="",
        embedding_dimensions=None,
        retrieval_level=0,
    )


def get_zvec_policy():
    from src.vector_store.policy import VectorStorePolicy

    return VectorStorePolicy(
        version=1,
        provider="zvec",
        embedding_provider="disabled",
        embedding_model="",
        embedding_dimensions=None,
        retrieval_level=1,
    )


def get_dense_zvec_policy():
    from src.vector_store.policy import VectorStorePolicy

    return VectorStorePolicy(
        version=1,
        provider="zvec",
        embedding_provider="openai_compatible",
        embedding_model="fake",
        embedding_dimensions=3,
        retrieval_level=2,
    )


class FakeDataType:
    STRING = "STRING"
    BOOL = "BOOL"
    INT64 = "INT64"
    DOUBLE = "DOUBLE"
    VECTOR_FP32 = "VECTOR_FP32"
    SPARSE_VECTOR_FP32 = "SPARSE_VECTOR_FP32"


class FakeZvecModule:
    DataType = FakeDataType

    def __init__(self):
        self.collections = {}

    def open(self, path):
        if path not in self.collections:
            raise FileNotFoundError(path)
        return self.collections[path]

    def create_and_open(self, path, schema):
        collection = FakeCollection(path=path, schema=schema)
        self.collections[path] = collection
        return collection

    class FieldSchema:
        def __init__(self, name, data_type, nullable=False, index_param=None):
            self.name = name
            self.data_type = data_type
            self.nullable = nullable
            self.index_param = index_param

    class VectorSchema:
        def __init__(self, name, data_type, dimension=None):
            self.name = name
            self.data_type = data_type
            self.dimension = dimension

    class CollectionSchema:
        def __init__(self, name, fields=None, vectors=None):
            self.name = name
            self.fields = fields or []
            self.vectors = vectors or []

    class InvertIndexParam:
        def __init__(self, enable_range_optimization=False, enable_extended_wildcard=False):
            self.enable_range_optimization = enable_range_optimization
            self.enable_extended_wildcard = enable_extended_wildcard

    class FtsIndexParam:
        def __init__(self, tokenizer_name="standard", filters=None, extra_params=""):
            self.tokenizer_name = tokenizer_name
            self.filters = filters or ["lowercase"]
            self.extra_params = extra_params

    class Doc:
        def __init__(self, id, score=None, vectors=None, fields=None):
            self.id = id
            self.score = score
            self.vectors = vectors or {}
            self.fields = fields or {}

    class Fts:
        def __init__(self, match_string):
            self.match_string = match_string

    class Query:
        def __init__(self, field_name, vector=None, fts=None):
            self.field_name = field_name
            self.vector = vector
            self.fts = fts


class FakeCollection:
    def __init__(self, path, schema):
        self.path = path
        self.schema = schema
        self.upserted = []
        self.deleted = []
        self.queries = []
        self.query_filters = []
        self.query_results = []
        self.flushed = False

    def upsert(self, docs):
        self.upserted.extend(docs)

    def delete(self, ids):
        self.deleted.extend(ids)

    def query(self, queries=None, topk=10, filter=None, include_vector=False):
        self.queries.append(queries)
        self.query_filters.append(filter)
        return self.query_results[:topk]

    def flush(self):
        self.flushed = True
