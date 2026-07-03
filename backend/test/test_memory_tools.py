"""长期节点记忆工具测试。"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.agents.factory import _create_default_toolkit
from src.memory.nodes import MemoryNodeService, MemoryNodeWrite
from src.storage import database
from src.tools.memory_nodes import MemoryNodeQuery, MemoryNodeTree


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """使用临时 SQLite，避免污染开发库。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)


def test_memory_node_tree_tool_returns_node_tree(isolated_db):
    MemoryNodeService(vector_backend=NoopVectorBackend()).upsert_node(
        MemoryNodeWrite(
            id="node-1",
            agent_id="agent-1",
            node_date="2026-07-03",
            title="记忆模块设计",
            summary="讨论了节点查询工具。",
            tags=["memory"],
        ),
    )

    chunk = asyncio.run(
        MemoryNodeTree()(
            agentId="agent-1",
            dateFrom="2026-07-01",
            dateTo="2026-07-03",
            limit=10,
        ),
    )

    payload = _tool_payload(chunk)
    assert payload["nodes"][0]["nodeId"] == "node-1"
    assert payload["nodes"][0]["summary"] == "讨论了节点查询工具。"


def test_memory_node_query_tool_returns_node_details(isolated_db):
    MemoryNodeService(vector_backend=NoopVectorBackend()).upsert_node(
        MemoryNodeWrite(
            id="node-1",
            agent_id="agent-1",
            node_date="2026-07-03",
            title="记忆模块设计",
            summary="讨论了 hook 查询。",
            user_inputs=["hook 怎么设计"],
            agent_final_outputs=["使用 tree 和 query 两个工具。"],
        ),
    )

    chunk = asyncio.run(
        MemoryNodeQuery()(
            agentId="agent-1",
            query="hook",
            limit=10,
        ),
    )

    payload = _tool_payload(chunk)
    assert payload["results"][0]["nodeId"] == "node-1"
    assert payload["results"][0]["userInputs"] == ["hook 怎么设计"]
    assert payload["results"][0]["agentFinalOutputs"] == ["使用 tree 和 query 两个工具。"]


def test_default_toolkit_includes_memory_tools():
    toolkit = _create_default_toolkit()
    tool_names = asyncio.run(_tool_names(toolkit))

    assert "memory_node_tree" in tool_names
    assert "memory_node_query" in tool_names


def test_infrastructure_toolkit_can_exclude_memory_tools():
    toolkit = _create_default_toolkit(include_default_tools=False)
    tool_names = asyncio.run(_tool_names(toolkit))

    assert "memory_node_tree" not in tool_names
    assert "memory_node_query" not in tool_names


def _tool_payload(chunk) -> dict:
    return json.loads(chunk.content[0].text)


async def _tool_names(toolkit) -> list[str]:
    schemas = await toolkit.get_tool_schemas()
    return [
        item.get("function", {}).get("name") or item.get("name")
        for item in schemas
    ]


class NoopVectorBackend:
    provider = "noop"

    def ensure_collection(self, config):
        return None

    def upsert(self, collection, documents):
        return None

    def delete(self, collection, ids):
        return None

    def query(self, query):
        return []
