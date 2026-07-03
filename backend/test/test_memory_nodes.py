"""长期节点记忆数据层测试。"""

from __future__ import annotations

import pytest

from src.memory.nodes import MemoryNodeService, MemoryNodeWrite
from src.storage import database
from src.vector_store.base import VectorQueryResult


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """使用临时 SQLite，避免污染开发库。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)


def test_memory_node_upsert_syncs_sqlite_and_vector(isolated_db):
    backend = FakeVectorBackend()
    service = MemoryNodeService(vector_backend=backend)

    node = service.upsert_node(
        MemoryNodeWrite(
            id="node-1",
            agent_id="agent-1",
            node_date="2026-07-03",
            title="记忆模块设计",
            summary="讨论了 memory nodes 和 Zvec。",
            user_inputs=["继续推进memory吧"],
            agent_final_outputs=["已实现 memory_nodes 数据层。"],
            tags=["memory", "zvec"],
            source_run_id="run-1",
        ),
    )

    assert node["id"] == "node-1"
    assert node["vector_status"] == "synced"
    assert node["user_inputs"] == ["继续推进memory吧"]
    assert backend.collections == ["memory_nodes"]
    assert backend.documents[0].id == "node-1"
    assert backend.documents[0].metadata["agent_id"] == "agent-1"


def test_memory_node_tree_returns_recent_summary(isolated_db):
    service = MemoryNodeService(vector_backend=FakeVectorBackend())
    service.upsert_node(
        MemoryNodeWrite(
            id="node-1",
            agent_id="agent-1",
            node_date="2026-07-03",
            title="记忆模块设计",
            summary="讨论了节点树。",
            tags=["memory"],
        ),
    )

    tree = service.tree(date_from="2026-07-01", date_to="2026-07-03")

    assert tree["nodes"] == [
        {
            "nodeId": "node-1",
            "date": "2026-07-03",
            "agentId": "agent-1",
            "title": "记忆模块设计",
            "summary": "讨论了节点树。",
            "tags": ["memory"],
        },
    ]


def test_memory_node_query_uses_vector_results(isolated_db):
    backend = FakeVectorBackend(query_results=[VectorQueryResult(id="node-1", score=0.9, text="", metadata={})])
    service = MemoryNodeService(vector_backend=backend)
    service.upsert_node(
        MemoryNodeWrite(
            id="node-1",
            agent_id="agent-1",
            node_date="2026-07-03",
            title="记忆模块设计",
            summary="讨论了查询工具。",
            user_inputs=["hook 怎么设计"],
            agent_final_outputs=["使用三个 hook。"],
            source_run_id="run-1",
        ),
    )

    result = service.query(query_text="hook", limit=5)

    assert result["results"][0]["nodeId"] == "node-1"
    assert result["results"][0]["userInputs"] == ["hook 怎么设计"]
    assert result["results"][0]["agentFinalOutputs"] == ["使用三个 hook。"]
    assert result["results"][0]["sourceRunIds"] == ["run-1"]


def test_memory_node_query_falls_back_to_sqlite(isolated_db):
    service = MemoryNodeService(vector_backend=FailingQueryBackend())
    service.upsert_node(
        MemoryNodeWrite(
            id="node-1",
            agent_id="agent-1",
            node_date="2026-07-03",
            title="记忆模块设计",
            summary="讨论了 Zvec 降级。",
        ),
    )

    result = service.query(query_text="Zvec", limit=5)

    assert [item["nodeId"] for item in result["results"]] == ["node-1"]


def test_memory_node_vector_failure_marks_pending(isolated_db):
    service = MemoryNodeService(vector_backend=FailingUpsertBackend())

    node = service.upsert_node(
        MemoryNodeWrite(
            id="node-1",
            agent_id="agent-1",
            node_date="2026-07-03",
            title="记忆模块设计",
            summary="讨论了同步失败降级。",
        ),
    )

    assert node["vector_status"] == "pending"
    assert "boom" in node["vector_error"]


class FakeVectorBackend:
    provider = "fake"

    def __init__(self, query_results=None):
        self.query_results = query_results or []
        self.collections = []
        self.documents = []

    def ensure_collection(self, config):
        self.collections.append(config.name)

    def upsert(self, collection, documents):
        self.documents.extend(documents)

    def delete(self, collection, ids):
        return None

    def query(self, query):
        return self.query_results


class FailingQueryBackend(FakeVectorBackend):
    def query(self, query):
        raise RuntimeError("query boom")


class FailingUpsertBackend(FakeVectorBackend):
    def upsert(self, collection, documents):
        raise RuntimeError("upsert boom")
