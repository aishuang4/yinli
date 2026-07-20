"""Memory Wiki Vault 同步测试。"""

from __future__ import annotations

import asyncio

from src.memory.markdown_store import MarkdownMemoryStore
from src.memory.nodes import MemoryNodeService, MemoryNodeWrite
from src.memory.wiki_vault import MemoryWikiVaultSyncer
from src.server.routes import memory as memory_routes
from src.storage import database
from src.storage.database import replace_skills
from src.vector_store.providers.disabled import DisabledVectorStoreBackend


def test_memory_wiki_vault_syncs_markdown_history_and_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)

    store = MarkdownMemoryStore(root=tmp_path / "memory")
    store.write_user_memory("default", "# Summary\n\n用户默认中文。")
    store.write_agent_memory("agent-1", "# Summary\n\nAgent 使用 hooks。")
    store.write_global_memory("default", "# Summary\n\n禁止恢复旧 Plan Runtime。")

    MemoryNodeService(vector_backend=DisabledVectorStoreBackend()).upsert_node(
        MemoryNodeWrite(
            id="node-1",
            agent_id="agent-1",
            node_date="2026-07-06",
            title="记忆系统推进",
            summary="完成 MemoryAdmissionGate 和 Wiki 设计。",
            user_inputs=["继续推进 memory"],
            agent_final_outputs=["已完成 gate。"],
            tags=["memory"],
        )
    )
    replace_skills({
        "custom-memory-gate": {
            "id": "custom-memory-gate",
            "name": "Memory Gate",
            "description": "判断记忆准入。",
            "category": "自进化",
            "trigger": "需要判断记忆是否写入时使用。",
            "content": "# Memory Gate\n\n1. 检查敏感信息。",
            "tags": ["memory"],
            "resourcePath": "",
            "resources": [],
            "status": "installed",
            "enabled": True,
            "installedAt": "2026-07-06T00:00:00Z",
            "updatedAt": "2026-07-06T00:00:00Z",
        }
    })

    result = MemoryWikiVaultSyncer(
        memory_store=store,
        wiki_root=tmp_path / "memory" / "wiki",
    ).sync()

    assert result.files_written >= 5
    assert result.markdown_memories == 3
    assert result.history_nodes == 1
    assert result.skills == 1
    assert result.entities > 0
    assert (tmp_path / "memory" / "wiki" / "README.md").exists()
    history_content = (
        tmp_path / "memory" / "wiki" / "history" / "agent-1" / "2026-07-06-node-1.md"
    ).read_text(encoding="utf-8")
    assert "[[entities/agent/agent-1|Agent:agent-1]]" in history_content
    assert "[[entities/module/MemoryAdmissionGate|模块:MemoryAdmissionGate]]" in history_content

    skill_content = (
        tmp_path / "memory" / "wiki" / "skills" / "Memory_Gate.md"
    ).read_text(encoding="utf-8")
    assert "Skill: Memory Gate" in skill_content
    assert "[[entities/skill/Memory_Gate|Skill:Memory Gate]]" in skill_content

    assert (tmp_path / "memory" / "wiki" / "entities" / "module" / "MemoryAdmissionGate.md").exists()
    assert (tmp_path / "memory" / "wiki" / "entities" / "graph.md").exists()
    assert "entities/module/MemoryAdmissionGate.md" in result.files


def test_memory_wiki_vault_builds_structured_graph(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)

    store = MarkdownMemoryStore(root=tmp_path / "memory")
    store.write_agent_memory("agent-1", "# Summary\n\nAgent 使用 MemoryAdmissionGate 和 VectorStoreBackend。")

    MemoryNodeService(vector_backend=DisabledVectorStoreBackend()).upsert_node(
        MemoryNodeWrite(
            id="node-graph",
            agent_id="agent-1",
            node_date="2026-07-06",
            title="Wiki 图谱测试",
            summary="继续推进 Obsidian Wiki 和 MemoryAdmissionGate。",
            user_inputs=["继续推进 Wiki Vault"],
            agent_final_outputs=["已生成图谱接口。"],
            tags=["wiki"],
        )
    )

    graph = MemoryWikiVaultSyncer(
        memory_store=store,
        wiki_root=tmp_path / "memory" / "wiki",
    ).graph(history_limit=20)

    node_ids = {node.id for node in graph.nodes}
    assert graph.documents == 2
    assert "agent:agent-1" in node_ids
    assert "module:MemoryAdmissionGate" in node_ids
    assert "module:VectorStoreBackend" in node_ids
    assert any(
        {edge.source, edge.target} == {"agent:agent-1", "module:MemoryAdmissionGate"}
        for edge in graph.edges
    )


def test_memory_wiki_sync_route_returns_summary(tmp_path, monkeypatch):
    from src.memory import wiki_vault

    monkeypatch.setattr(wiki_vault, "MEMORY_ROOT", tmp_path / "data" / "memory")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)

    response = asyncio.run(memory_routes.sync_memory_wiki_vault(history_limit=10))

    assert response.path.endswith("/data/memory/wiki")
    assert response.filesWritten >= 1
    assert "README.md" in response.files


def test_memory_wiki_graph_route_returns_graph(tmp_path, monkeypatch):
    from src.memory import markdown_store
    from src.memory import wiki_vault

    monkeypatch.setattr(markdown_store, "MEMORY_ROOT", tmp_path / "data" / "memory")
    monkeypatch.setattr(wiki_vault, "MEMORY_ROOT", tmp_path / "data" / "memory")
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)

    store = MarkdownMemoryStore(root=tmp_path / "data" / "memory")
    store.write_global_memory("default", "# Summary\n\n全局规则：禁止恢复旧 Plan Runtime。")

    response = asyncio.run(memory_routes.get_memory_wiki_graph(history_limit=10, edge_limit=20))

    assert response.documents == 1
    assert any(node.id == "rule:Plan Runtime" for node in response.nodes)
