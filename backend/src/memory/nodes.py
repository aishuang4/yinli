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
    find_session_day_memory_node,
    get_memory_node,
    list_memory_retrieval_feedback_scores,
    list_memory_nodes,
    list_stale_active_memory_node_ids,
    mark_memory_node_used,
    search_memory_nodes,
    update_memory_node_status,
    update_memory_node_vector_state,
    upsert_memory_node,
)

# 超过这么多天未被命中（且未置顶）的历史记忆自动归档：不删除、不进注入 / 召回，Memory 页可恢复。
MEMORY_NODE_STALE_DAYS = 90
# 同一会话同一天聚合进一个节点时，最多保留多少轮原文。
MEMORY_NODE_MAX_TURNS = 40
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
    project_id: str = "default"
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

    def append_turn_to_session_day_node(
        self,
        item: MemoryNodeWrite,
        *,
        turn_summary: str,
        turn_digest: str,
    ) -> dict[str, Any] | None:
        """同一会话、同一天已有节点时把本轮并进去，返回更新后的节点；没有则返回 None。

        标题保留首轮（它是这段会话的主题），summary 记录"首轮要点 ｜ 最近一轮要点（共 N 轮）"，
        digest 用最新一轮，原文列表追加。这样一段多轮会话在索引和召回里只占一条。
        """

        existing = find_session_day_memory_node(
            workspace_id=item.workspace_id,
            project_id=item.project_id,
            agent_id=item.agent_id,
            node_date=item.node_date,
            source_session_id=item.source_session_id,
        )
        if not existing:
            return None
        user_inputs = list(existing.get("user_inputs") or []) + list(item.user_inputs)
        outputs = list(existing.get("agent_final_outputs") or []) + list(item.agent_final_outputs)
        user_inputs = user_inputs[-MEMORY_NODE_MAX_TURNS:]
        outputs = outputs[-MEMORY_NODE_MAX_TURNS:]
        turns = len(user_inputs)
        first_summary = str(existing.get("summary") or "").split(" ｜ ", 1)[0]
        summary = f"{first_summary} ｜ 最近：{turn_summary}（共 {turns} 轮）"
        tags = list(dict.fromkeys([*(existing.get("tags") or []), *item.tags]))
        merged = MemoryNodeWrite(
            id=existing["id"],
            agent_id=item.agent_id,
            node_date=item.node_date,
            title=str(existing.get("title") or item.title),
            summary=summary[:260],
            conversation_digest=turn_digest,
            user_inputs=user_inputs,
            agent_final_outputs=outputs,
            tags=tags,
            workspace_id=item.workspace_id,
            project_id=item.project_id,
            user_id=item.user_id,
            source=item.source,
            source_session_id=item.source_session_id,
            source_run_id=item.source_run_id,
            source_message_id=item.source_message_id,
            confidence=max(float(existing.get("confidence") or 0.0), item.confidence),
            status="active",
            pinned=bool(existing.get("pinned")),
        )
        return self.upsert_node(merged)

    def archive_stale_nodes(self, *, stale_days: int = MEMORY_NODE_STALE_DAYS, limit: int = 200) -> list[str]:
        """把长期未命中的活跃节点改为 archived 并从向量库移除。返回归档的 id。"""

        cutoff = (date.today() - timedelta(days=stale_days)).isoformat()
        archived: list[str] = []
        for node_id in list_stale_active_memory_node_ids(before_date=cutoff, unused_since=cutoff, limit=limit):
            update_memory_node_status(node_id, status="archived")
            try:
                self.sync_node_to_vector(node_id)
            except Exception:
                pass
            archived.append(node_id)
        return archived

    def sync_node_to_vector(self, node_id: str) -> dict[str, Any]:
        """把单个 SQLite 节点同步到向量库。"""

        node = get_memory_node(node_id)
        if not node:
            raise ValueError(f"Memory node not found: {node_id}")
        if node["status"] != "active":
            try:
                self.vector_backend.delete(MEMORY_NODES_COLLECTION, [node_id])
            except Exception as exc:
                updated = update_memory_node_vector_state(
                    node_id,
                    vector_status="pending",
                    vector_error=str(exc),
                )
                return updated or node
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
        project_id: str = "default",
        user_id: str | None = None,
        status: str | None = "active",
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
            status=status,
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
                    "status": node["status"],
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
        project_id: str = "default",
        user_id: str | None = None,
        status: str | None = "active",
        mark_used: bool = True,
    ) -> dict[str, Any]:
        """按语义或日期查询节点详情。

        `mark_used=False` 供运行前自动召回使用：自动召回不代表模型真的用到了，不计入 use_count。
        """

        vector_results = self._query_vector(
            query_text=query_text,
            agent_id=agent_id,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            workspace_id=workspace_id,
            project_id=project_id,
            user_id=user_id,
            status=status,
        )
        vector_nodes = []
        for result in vector_results:
            node = get_memory_node(result.id)
            if node and _node_matches_query_filters(
                node,
                workspace_id=workspace_id,
                project_id=project_id,
                agent_id=agent_id,
                user_id=user_id,
                date_from=date_from,
                date_to=date_to,
                status=status,
            ):
                candidate = dict(node)
                candidate["_vector_score"] = float(result.score or 0.0)
                candidate["_retrieval_sources"] = ["vector"]
                vector_nodes.append(candidate)
        lexical_nodes = search_memory_nodes(
            workspace_id=workspace_id,
            project_id=project_id,
            agent_id=agent_id,
            user_id=user_id,
            date_from=date_from,
            date_to=date_to,
            query_text=query_text,
            status=status,
            limit=min(max(limit * 2, limit), 100),
        )
        for node in lexical_nodes:
            node["_retrieval_sources"] = ["lexical"]
        nodes = _merge_query_candidates(vector_nodes, lexical_nodes)
        feedback_scores = _load_retrieval_feedback_scores()
        for node in nodes:
            node["_feedback_score"] = feedback_scores.get(str(node.get("id") or ""), 0.0)
        nodes = _rerank_nodes(nodes, query_text=query_text)
        if mark_used:
            for node in nodes[:limit]:
                mark_memory_node_used(node["id"])
        results = nodes[:limit]
        return {
            "results": [_node_query_payload(node) for node in results],
            "meta": _query_meta(
                query_text=query_text,
                vector_count=len(vector_nodes),
                lexical_count=len(lexical_nodes),
                merged_count=len(nodes),
                returned_nodes=results,
            ),
        }

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
        status: str | None,
    ):
        if not query_text.strip() or status != "active":
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

def _node_matches_query_filters(
    node: dict[str, Any],
    *,
    workspace_id: str,
    project_id: str,
    agent_id: str | None,
    user_id: str | None,
    date_from: str | None,
    date_to: str | None,
    status: str | None,
) -> bool:
    """校验向量候选仍满足 SQLite 权威过滤条件。"""

    if node.get("workspace_id") != workspace_id or node.get("project_id") != project_id:
        return False
    if status is not None and node.get("status") != status:
        return False
    if agent_id and node.get("agent_id") != agent_id:
        return False
    if user_id and node.get("user_id") != user_id:
        return False
    node_date = str(node.get("node_date") or "")
    if date_from and node_date < date_from:
        return False
    if date_to and node_date > date_to:
        return False
    return True


def _merge_query_candidates(
    vector_nodes: list[dict[str, Any]],
    lexical_nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """合并向量和关键词候选，SQLite 同 ID 节点保留向量分数。"""

    merged: dict[str, dict[str, Any]] = {}
    for node in vector_nodes:
        node_id = str(node.get("id") or "")
        if node_id:
            merged[node_id] = node
    for node in lexical_nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        if node_id in merged:
            sources = set(merged[node_id].get("_retrieval_sources") or [])
            sources.update(node.get("_retrieval_sources") or [])
            merged[node_id].update({key: value for key, value in node.items() if key != "_vector_score"})
            merged[node_id]["_retrieval_sources"] = sorted(sources)
        else:
            merged[node_id] = node
    return list(merged.values())


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
        "status": node["status"],
        "title": node["title"],
        "confidence": node["confidence"],
        "lastUsedAt": node["last_used_at"],
        "useCount": node["use_count"],
        "sourceSessionId": node["source_session_id"],
        "sourceRunId": node["source_run_id"],
        "sourceMessageId": node["source_message_id"],
        "userInputs": node["user_inputs"],
        "agentFinalOutputs": node["agent_final_outputs"],
        "summary": node["summary"],
        "conversationDigest": node["conversation_digest"],
        "sourceRunIds": [node["source_run_id"]] if node["source_run_id"] else [],
        "tags": node["tags"],
        "rankScore": round(float(node.get("_rank_score") or 0.0), 3),
        "matchedTerms": list(node.get("_matched_terms") or []),
    }


def _rerank_nodes(nodes: list[dict[str, Any]], *, query_text: str) -> list[dict[str, Any]]:
    query_terms = _rank_terms(query_text)

    def score(node: dict[str, Any]) -> tuple[float, str]:
        rank_score, matched_terms = _node_rank_score(node, query_terms=query_terms)
        node["_rank_score"] = rank_score
        node["_matched_terms"] = sorted(matched_terms)
        return (rank_score, str(node.get("node_date") or ""))

    return sorted(nodes, key=score, reverse=True)


def _query_meta(
    *,
    query_text: str,
    vector_count: int,
    lexical_count: int,
    merged_count: int,
    returned_nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    """生成召回质量审计摘要，不包含完整记忆正文。"""

    query_terms = sorted(_rank_terms(query_text))
    top_score = float(returned_nodes[0].get("_rank_score") or 0.0) if returned_nodes else 0.0
    retrieval_sources = sorted({
        source
        for node in returned_nodes
        for source in (node.get("_retrieval_sources") or [])
    })
    return {
        "queryTermCount": len(query_terms),
        "queryTerms": query_terms[:20],
        "vectorCandidateCount": vector_count,
        "lexicalCandidateCount": lexical_count,
        "mergedCandidateCount": merged_count,
        "returnedCount": len(returned_nodes),
        "topScore": round(top_score, 4),
        "empty": len(returned_nodes) == 0,
        "retrievalSources": retrieval_sources,
        "resultQuality": [
            {
                "nodeId": str(node.get("id") or ""),
                "rankScore": round(float(node.get("_rank_score") or 0.0), 4),
                "vectorScore": round(float(node.get("_vector_score") or 0.0), 4),
                "feedbackScore": round(float(node.get("_feedback_score") or 0.0), 4),
                "matchedTerms": list(node.get("_matched_terms") or [])[:20],
                "sources": list(node.get("_retrieval_sources") or []),
                "confidence": float(node.get("confidence") or 0.0),
                "useCount": int(node.get("use_count") or 0),
                "pinned": bool(node.get("pinned")),
            }
            for node in returned_nodes[:10]
        ],
    }


def _node_rank_score(node: dict[str, Any], *, query_terms: set[str]) -> tuple[float, set[str]]:
    """计算轻量召回排序分，并返回命中的查询词。"""

    text = "\n".join(
        [
            str(node.get("title") or ""),
            str(node.get("summary") or ""),
            str(node.get("conversation_digest") or ""),
            " ".join(str(item) for item in node.get("tags") or []),
        ]
    ).lower()
    matched_terms = {term for term in query_terms if term in text}
    # 中文滑窗后命中数会偏多，封顶避免压过向量分和反馈分。
    lexical = min(float(len(matched_terms)), 4.0)
    vector = min(float(node.get("_vector_score") or 0), 1.0) * 0.35
    feedback = max(-1.0, min(float(node.get("_feedback_score") or 0), 1.0))
    pinned = 0.4 if node.get("pinned") else 0.0
    confidence = min(float(node.get("confidence") or 0), 1.0) * 0.25
    used = min(int(node.get("use_count") or 0), 10) * 0.01
    return lexical + vector + feedback + pinned + confidence + used, matched_terms


def _load_retrieval_feedback_scores(limit: int = 200) -> dict[str, float]:
    """从结构化召回反馈表聚合分数，用于轻量排序权重。"""

    try:
        return list_memory_retrieval_feedback_scores(limit=limit)
    except Exception:
        return {}


def _rank_terms(value: str) -> set[str]:
    """查询词：英文 / 数字按 token，中文按 2 字滑窗。

    此前中文连续串只算一个词（"帮我把销售表按地区汇总"），几乎不可能整段命中；
    滑窗后"销售表"、"地区"这类片段能命中标题 / 摘要。
    """

    terms: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9_]{2,}", value):
        terms.add(token.lower())
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", value):
        for index in range(len(run) - 1):
            terms.add(run[index : index + 2])
    return terms


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
