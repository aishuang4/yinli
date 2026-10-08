"""长期节点记忆数据层测试。"""

from __future__ import annotations

import pytest

from src.memory.nodes import MemoryNodeService, MemoryNodeWrite
from src.server.routes import memory as memory_routes
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
            "status": "active",
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


def test_memory_node_query_merges_vector_and_lexical_results(isolated_db):
    backend = FakeVectorBackend(query_results=[VectorQueryResult(id="node-vector", score=0.9, text="", metadata={})])
    service = MemoryNodeService(vector_backend=backend)
    service.upsert_node(
        MemoryNodeWrite(
            id="node-vector",
            agent_id="agent-1",
            node_date="2026-07-03",
            title="语义召回",
            summary="讨论了历史记忆混合召回。",
        ),
    )
    service.upsert_node(
        MemoryNodeWrite(
            id="node-lexical",
            agent_id="agent-1",
            node_date="2026-07-04",
            title="BM25 关键词召回",
            summary="讨论了 BM25 字段结果合并。",
        ),
    )

    result = service.query(query_text="BM25", agent_id="agent-1", limit=5)

    assert {item["nodeId"] for item in result["results"]} == {"node-vector", "node-lexical"}
    assert result["meta"]["vectorCandidateCount"] == 1
    assert result["meta"]["lexicalCandidateCount"] == 1
    assert result["meta"]["mergedCandidateCount"] == 2
    assert result["meta"]["returnedCount"] == 2
    assert result["meta"]["empty"] is False
    assert set(result["meta"]["retrievalSources"]) == {"lexical", "vector"}
    assert {item["nodeId"] for item in result["meta"]["resultQuality"]} == {"node-vector", "node-lexical"}


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


def test_memory_node_status_update_disables_and_restores_with_audit(isolated_db, monkeypatch):
    import asyncio

    service = MemoryNodeService(vector_backend=FakeVectorBackend())
    monkeypatch.setattr("src.server.routes.memory_api.nodes.MemoryNodeService", lambda: service)
    service.upsert_node(
        MemoryNodeWrite(
            id="node-1",
            agent_id="agent-1",
            node_date="2026-07-03",
            title="记忆模块设计",
            summary="讨论了状态管理。",
            source_session_id="session-1",
        ),
    )

    disabled = asyncio.run(
        memory_routes.update_node_status(
            "node-1",
            memory_routes.MemoryNodeStatusUpdate(status="disabled", reason="测试禁用"),
        )
    )
    active_tree = service.tree(date_from="2026-07-01", date_to="2026-07-03")
    all_tree = service.tree(date_from="2026-07-01", date_to="2026-07-03", status=None)

    assert disabled["status"] == "disabled"
    assert disabled["vector_status"] == "deleted"
    assert active_tree["nodes"] == []
    assert all_tree["nodes"][0]["status"] == "disabled"

    restored = asyncio.run(
        memory_routes.update_node_status(
            "node-1",
            memory_routes.MemoryNodeStatusUpdate(status="active", reason="测试恢复"),
        )
    )
    audit = asyncio.run(memory_routes.memory_audit_events(limit=10, offset=0))

    assert restored["status"] == "active"
    assert restored["vector_status"] in {"synced", "pending"}
    assert audit.events[0]["eventType"] == "MEMORY_AUDIT"
    assert audit.events[0]["payload"]["memoryType"] == "memory_node_status_update"


def test_memory_retrieval_feedback_records_audit(isolated_db):
    import asyncio

    service = MemoryNodeService(vector_backend=FakeVectorBackend())
    service.upsert_node(
        MemoryNodeWrite(
            id="node-1",
            agent_id="agent-1",
            node_date="2026-07-03",
            title="召回反馈",
            summary="记录用户判断召回是否相关。",
        ),
    )

    result = asyncio.run(
        memory_routes.record_retrieval_feedback(
            memory_routes.MemoryRetrievalFeedback(
                auditEventId="audit-1",
                nodeId="node-1",
                rating="irrelevant",
                reason="不相关",
                sessionId="session-1",
                agentId="agent-1",
                query="memory feedback",
            )
        )
    )
    audit = asyncio.run(memory_routes.memory_audit_events(limit=10, offset=0))
    feedback_rows = database.list_memory_retrieval_feedback(limit=10)

    assert result["ok"] is True
    assert result["feedbackId"] == feedback_rows[0]["id"]
    assert feedback_rows[0]["nodeId"] == "node-1"
    assert feedback_rows[0]["rating"] == "irrelevant"
    assert feedback_rows[0]["auditEventId"] == "audit-1"
    assert audit.events[0]["payload"]["memoryType"] == "memory_retrieval_feedback"
    assert audit.events[0]["payload"]["rating"] == "irrelevant"
    assert audit.events[0]["payload"]["nodeId"] == "node-1"
    assert audit.events[0]["payload"]["feedbackId"] == feedback_rows[0]["id"]


def test_memory_retrieval_feedback_affects_query_ranking(isolated_db):
    import asyncio

    service = MemoryNodeService(vector_backend=FakeVectorBackend())
    service.upsert_node(
        MemoryNodeWrite(
            id="node-positive",
            agent_id="agent-1",
            node_date="2026-07-01",
            title="反馈排序",
            summary="讨论了 memory feedback ranking。",
        ),
    )
    service.upsert_node(
        MemoryNodeWrite(
            id="node-negative",
            agent_id="agent-1",
            node_date="2026-07-02",
            title="反馈排序",
            summary="讨论了 memory feedback ranking。",
        ),
    )
    asyncio.run(memory_routes.record_retrieval_feedback(
        memory_routes.MemoryRetrievalFeedback(
            auditEventId="audit-1",
            nodeId="node-positive",
            rating="relevant",
        )
    ))
    asyncio.run(memory_routes.record_retrieval_feedback(
        memory_routes.MemoryRetrievalFeedback(
            auditEventId="audit-2",
            nodeId="node-negative",
            rating="irrelevant",
        )
    ))

    result = service.query(query_text="feedback ranking", agent_id="agent-1", limit=2)
    quality = {item["nodeId"]: item for item in result["meta"]["resultQuality"]}

    assert [item["nodeId"] for item in result["results"]] == ["node-positive", "node-negative"]
    assert quality["node-positive"]["feedbackScore"] > 0
    assert quality["node-negative"]["feedbackScore"] < 0


def test_memory_settings_exposes_admission_policy(isolated_db):
    import asyncio

    settings = asyncio.run(memory_routes.memory_settings())

    assert settings.admission["mode"] == "rules_scoring"
    assert settings.admission["historyThreshold"] > 0
    assert settings.admission["markdownThreshold"] > settings.admission["historyThreshold"]
    assert settings.runtime["usesHooksAndTools"] is True


def test_memory_admission_settings_can_be_updated(isolated_db):
    import asyncio

    updated = asyncio.run(
        memory_routes.update_memory_admission_settings(
            memory_routes.MemoryAdmissionPolicyUpdate(
                historyThreshold=0.4,
                markdownThreshold=0.7,
                skillThreshold=0.6,
                enableModelScoring=True,
                modelScoreWeight=0.5,
                summaryMaxItemsPerSection=5,
                markdownWriteRetries=3,
            )
        )
    )
    settings = asyncio.run(memory_routes.memory_settings())

    assert updated["historyThreshold"] == 0.4
    assert updated["enableModelScoring"] is True
    assert settings.admission["markdownThreshold"] == 0.7
    assert settings.admission["summaryMaxItemsPerSection"] == 5


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


def test_session_day_turns_aggregate_into_one_node(isolated_db):
    from src.memory.node_persist import MemoryNodePersistInput, persist_completed_run_memory_node
    from src.memory.admission import MemoryAdmissionInput, evaluate_memory_admission

    backend = FakeVectorBackend()
    service = MemoryNodeService(vector_backend=backend)
    admission = evaluate_memory_admission(MemoryAdmissionInput(user_message="帮我把 Q3 销售表按地区汇总生成 xlsx 文件", assistant_text="已生成 q3_by_region.xlsx，共 5 个地区。"))
    assert admission.should_persist_history

    first = persist_completed_run_memory_node(
        MemoryNodePersistInput(session_id="s-1", agent_id="agent-1", agent_name="A",
                               user_message="帮我把 Q3 销售表按地区汇总生成 xlsx 文件",
                               assistant_text="已生成 q3_by_region.xlsx，共 5 个地区。", source_run_id="run-1"),
        service=service, admission=admission,
    )
    second = persist_completed_run_memory_node(
        MemoryNodePersistInput(session_id="s-1", agent_id="agent-1", agent_name="A",
                               user_message="再把华东的明细单独导出一份 csv",
                               assistant_text="已导出 huadong_detail.csv。", source_run_id="run-2"),
        service=service, admission=admission,
    )
    other_session = persist_completed_run_memory_node(
        MemoryNodePersistInput(session_id="s-2", agent_id="agent-1", agent_name="A",
                               user_message="帮我审阅这份采购合同的付款条款",
                               assistant_text="付款条款有两处风险。", source_run_id="run-3"),
        service=service, admission=admission,
    )

    assert first["id"] == second["id"]
    assert other_session["id"] != first["id"]
    assert second["title"] == first["title"]
    assert second["user_inputs"] == ["帮我把 Q3 销售表按地区汇总生成 xlsx 文件", "再把华东的明细单独导出一份 csv"]
    assert "共 2 轮" in second["summary"] and "最近：" in second["summary"]
    assert second["source_run_id"] == "run-2"
    assert len(database.list_memory_nodes(workspace_id="default", project_id="default")) == 2


def test_archive_stale_nodes_skips_pinned_and_recently_used(isolated_db):
    from datetime import date, timedelta

    backend = FakeVectorBackend()
    service = MemoryNodeService(vector_backend=backend)
    old = (date.today() - timedelta(days=120)).isoformat()
    for node_id, pinned in (("old-plain", False), ("old-pinned", True)):
        service.upsert_node(MemoryNodeWrite(id=node_id, agent_id="agent-1", node_date=old, title=node_id, summary="s", pinned=pinned))
    service.upsert_node(MemoryNodeWrite(id="old-used", agent_id="agent-1", node_date=old, title="used", summary="s"))
    database.mark_memory_node_used("old-used")
    service.upsert_node(MemoryNodeWrite(id="fresh", agent_id="agent-1", node_date=date.today().isoformat(), title="fresh", summary="s"))

    archived = service.archive_stale_nodes()

    assert archived == ["old-plain"]
    assert database.get_memory_node("old-plain")["status"] == "archived"
    assert database.get_memory_node("old-plain")["vector_status"] == "deleted"
    assert database.get_memory_node("old-pinned")["status"] == "active"
    assert database.get_memory_node("old-used")["status"] == "active"
    assert database.get_memory_node("fresh")["status"] == "active"
