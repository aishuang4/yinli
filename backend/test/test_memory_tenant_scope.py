"""記憶檢索不再按 Workspace / Project 過濾。"""

from __future__ import annotations

from src.memory.nodes import MemoryNodeService, MemoryNodeWrite
from src.vector_store.base import VectorQueryResult


class _RecordingVector:
    provider = "fake"

    def __init__(self) -> None:
        self.documents = []
        self.last_query = None

    def ensure_collection(self, config) -> None:
        del config

    def upsert(self, collection, documents) -> None:
        del collection
        self.documents.extend(documents)

    def delete(self, collection, ids) -> None:
        del collection, ids

    def query(self, query):
        self.last_query = query
        return [VectorQueryResult(id=document.id, score=1.0, text="") for document in self.documents]


def test_empty_scope_recalls_lexical_and_vector_nodes() -> None:
    backend = _RecordingVector()
    service = MemoryNodeService(vector_backend=backend)
    for node_id, workspace_id, project_id in (
        ("node-w1", "w1", "p1"),
        ("node-default", "default", "default"),
    ):
        service.upsert_node(MemoryNodeWrite(
            id=node_id,
            agent_id="agent-1",
            node_date="2026-10-20",
            title="共享標記",
            summary="tenant-scope-token",
            user_inputs=["tenant-scope-token"],
            workspace_id=workspace_id,
            project_id=project_id,
        ))
    result = service.query(
        query_text="tenant-scope-token",
        workspace_id="",
        project_id="",
        mark_used=False,
        date_from=None,
        date_to=None,
    )
    found = {item["nodeId"] for item in result["results"]}
    assert {"node-w1", "node-default"} <= found
    assert "workspace_id" not in (backend.last_query.filters or {})
    assert "project_id" not in (backend.last_query.filters or {})
