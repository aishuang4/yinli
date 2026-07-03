"""长期节点记忆服务。

SQLite `memory_nodes` 是事实源；Zvec `memory_nodes` collection 是可重建索引副本。
节点只保存用户输入、Agent 最终输出和摘要，不保存工具调用过程。
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

from src.storage.database import (
    get_memory_node,
    list_memory_nodes,
    mark_memory_node_used,
    search_memory_nodes,
    update_memory_node_vector_state,
    upsert_memory_node,
)
from src.vector_store.base import (
    VectorCollectionConfig,
    VectorDocument,
    VectorQuery,
    VectorStoreBackend,
)
from src.vector_store.registry import get_vector_store_backend

MEMORY_NODES_COLLECTION = "memory_nodes"


@dataclass(frozen=True)
class MemoryNodeWrite:
    """写入长期节点记忆的输入。"""

    agent_id: str
    node_date: str
    title: str
    summary: str
    user_inputs: list[str] = field(default_factory=list)
    agent_final_outputs: list[str] = field(default_factory=list)
    conversation_digest: str = ""
    tags: list[str] = field(default_factory=list)
    workspace_id: str = "default"
    project_id: str = ""
    user_id: str = "default"
    date_range_start: str | None = None
    date_range_end: str | None = None
    source: str = "chat_run"
    source_session_id: str = ""
    source_run_id: str = ""
    source_message_id: str = ""
    confidence: float = 1.0
    status: str = "active"
    pinned: bool = False
    id: str | None = None


class MemoryNodeService:
    """长期节点记忆读写服务。"""

    def __init__(self, vector_backend: VectorStoreBackend | None = None):
        self.vector_backend = vector_backend or get_vector_store_backend()

    def upsert_node(self, item: MemoryNodeWrite) -> dict[str, Any]:
        """写入 SQLite 主表，并尽力同步向量索引。"""

        node_id = item.id or _new_node_id(item.agent_id, item.node_date)
        search_text = _build_search_text(item)
        now = _utc_now()
        node = upsert_memory_node(
            {
                "id": node_id,
                "workspace_id": item.workspace_id,
                "project_id": item.project_id,
                "agent_id": item.agent_id,
                "user_id": item.user_id,
                "node_date": item.node_date,
                "date_range_start": item.date_range_start or item.node_date,
                "date_range_end": item.date_range_end or item.node_date,
                "title": item.title,
                "summary": item.summary,
                "conversation_digest": item.conversation_digest,
                "search_text": search_text,
                "user_inputs": item.user_inputs,
                "agent_final_outputs": item.agent_final_outputs,
                "tags": item.tags,
                "source": item.source,
                "source_session_id": item.source_session_id,
                "source_run_id": item.source_run_id,
                "source_message_id": item.source_message_id,
                "confidence": item.confidence,
                "status": item.status,
                "pinned": item.pinned,
                "vector_status": "pending",
                "vector_error": "",
                "created_at": now,
                "updated_at": now,
            },
        )
        return self.sync_node_to_vector(node["id"])

    def sync_node_to_vector(self, node_id: str) -> dict[str, Any]:
        """把单个 SQLite 节点同步到向量库。"""

        node = get_memory_node(node_id)
        if not node:
            raise ValueError(f"Memory node not found: {node_id}")
        if node["status"] != "active":
            self.vector_backend.delete(MEMORY_NODES_COLLECTION, [node_id])
            updated = update_memory_node_vector_state(node_id, vector_status="deleted")
            return updated or node
        try:
            self.vector_backend.ensure_collection(_collection_config())
            self.vector_backend.upsert(MEMORY_NODES_COLLECTION, [_node_to_document(node)])
        except Exception as exc:
            updated = update_memory_node_vector_state(
                node_id,
                vector_status="pending",
                vector_error=str(exc),
            )
            return updated or node
        updated = update_memory_node_vector_state(node_id, vector_status="synced", vector_error="")
        return updated or node

    def tree(
        self,
        *,
        agent_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 30,
        workspace_id: str = "default",
        project_id: str = "",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """返回最近或指定范围的节点树。"""

        resolved_to = date_to or date.today().isoformat()
        resolved_from = date_from or _date_days_ago(resolved_to, 29)
        nodes = list_memory_nodes(
            workspace_id=workspace_id,
            project_id=project_id,
            agent_id=agent_id,
            user_id=user_id,
            date_from=resolved_from,
            date_to=resolved_to,
            limit=limit,
        )
        return {
            "nodes": [
                {
                    "nodeId": node["id"],
                    "date": node["node_date"],
                    "agentId": node["agent_id"],
                    "title": node["title"],
                    "summary": node["summary"],
                    "tags": node["tags"],
                }
                for node in nodes
            ],
        }

    def query(
        self,
        *,
        query_text: str = "",
        agent_id: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 10,
        workspace_id: str = "default",
        project_id: str = "",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """按语义或日期查询节点详情。"""

        vector_results = self._query_vector(
            query_text=query_text,
            agent_id=agent_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
        )
        if vector_results:
            nodes = [
                node
                for result in vector_results
                if (node := get_memory_node(result.id)) and node["status"] == "active"
            ]
        else:
            nodes = search_memory_nodes(
                workspace_id=workspace_id,
                project_id=project_id,
                agent_id=agent_id,
                user_id=user_id,
                date_from=date_from,
                date_to=date_to,
                query_text=query_text,
                limit=limit,
            )
        for node in nodes:
            mark_memory_node_used(node["id"])
        return {"results": [_node_query_payload(node) for node in nodes[:limit]]}

    def _query_vector(
        self,
        *,
        query_text: str,
        agent_id: str | None,
        date_from: str | None,
        date_to: str | None,
        limit: int,
        workspace_id: str,
        project_id: str,
        user_id: str | None,
    ):
        if not query_text.strip():
            return []
        filters: dict[str, Any] = {
            "workspace_id": workspace_id,
            "project_id": project_id,
            "status": "active",
        }
        if agent_id:
            filters["agent_id"] = agent_id
        if user_id:
            filters["user_id"] = user_id
        if date_from or date_to:
            return []
        try:
            return self.vector_backend.query(
                VectorQuery(
                    collection=MEMORY_NODES_COLLECTION,
                    text=query_text,
                    filters=filters,
                    limit=limit,
                ),
            )
        except Exception:
            return []


def _collection_config() -> VectorCollectionConfig:
    return VectorCollectionConfig(
        name=MEMORY_NODES_COLLECTION,
        metadata_schema={
            "workspace_id": "string",
            "project_id": "string",
            "agent_id": "string",
            "user_id": "string",
            "node_date": "string",
            "status": "string",
            "pinned": "bool",
            "confidence": "float",
        },
    )


def _node_to_document(node: dict[str, Any]) -> VectorDocument:
    return VectorDocument(
        id=node["id"],
        text=node["search_text"],
        metadata={
            "workspace_id": node["workspace_id"],
            "project_id": node["project_id"],
            "agent_id": node["agent_id"],
            "user_id": node["user_id"],
            "node_date": node["node_date"],
            "status": node["status"],
            "pinned": node["pinned"],
            "confidence": node["confidence"],
            "content_hash": _content_hash(node["search_text"]),
            "updated_at": node["updated_at"],
        },
        sparse_vector=_sparse_terms(node["search_text"]),
    )


def _node_query_payload(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "nodeId": node["id"],
        "date": node["node_date"],
        "agentId": node["agent_id"],
        "userInputs": node["user_inputs"],
        "agentFinalOutputs": node["agent_final_outputs"],
        "summary": node["summary"],
        "conversationDigest": node["conversation_digest"],
        "sourceRunIds": [node["source_run_id"]] if node["source_run_id"] else [],
        "tags": node["tags"],
    }


def _build_search_text(item: MemoryNodeWrite) -> str:
    parts = [
        item.title,
        item.summary,
        item.conversation_digest,
        "\n".join(item.user_inputs),
        "\n".join(item.agent_final_outputs),
        " ".join(item.tags),
    ]
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _sparse_terms(text: str) -> dict[str, float]:
    terms: dict[str, float] = {}
    for token in re.findall(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]", text.lower()):
        terms[token] = terms.get(token, 0.0) + 1.0
    return terms


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _new_node_id(agent_id: str, node_date: str) -> str:
    suffix = uuid.uuid4().hex[:12]
    safe_agent = re.sub(r"[^a-zA-Z0-9]+", "_", agent_id).strip("_")[:32] or "agent"
    return f"mn_{node_date.replace('-', '')}_{safe_agent}_{suffix}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _date_days_ago(value: str, days: int) -> str:
    parsed = datetime.fromisoformat(value).date()
    return (parsed - timedelta(days=days)).isoformat()
