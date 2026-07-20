# Graph RAG 方案设计

> **版本**：v2.0
> **日期**：2026-07-20
> **项目**：Agent Workstation
> **目标**：基于现有基础设施（Zvec 向量库、Memory Node、Wiki Vault 实体图谱），以**渐进式、低侵入**的方式落地 Graph RAG
> **图数据库**：Memgraph（Cypher 查询、原生图遍历、内存级性能）

---

## 目录

- [一、现状分析](#一现状分析)
- [二、整体架构](#二整体架构)
- [三、模块详细设计](#三模块详细设计)
  - [3.1 知识图谱存储 — GraphStore](#31-知识图谱存储--graphstore)
  - [3.2 Memgraph 持久化与操作层（graph_db.py）](#32-memgraph-持久化与操作层graph_dbpy)
  - [3.3 LLM 实体/关系抽取（extraction.py）](#33-llm-实体关系抽取extractionpy)
  - [3.4 图遍历检索（traversal.py）](#34-图遍历检索traversalpy)
  - [3.5 融合检索器（retrieval.py）](#35-融合检索器retrievalpy)
  - [3.6 与现有记忆/Wiki 系统的融合](#36-与现有记忆wiki-系统的融合)
  - [3.7 对外 Tool 接口](#37-对外-tool-接口)
- [四、实现路线图](#四实现路线图)
- [五、与现有项目的关键协同点](#五与现有项目的关键协同点)

---

## 一、现状分析

### 已具备的 Graph RAG 前置能力

| 能力 | 现有实现 | 状态 |
|------|---------|------|
| 向量检索（Dense + Sparse + BM25） | `ZvecVectorStoreBackend` | ✅ 可用 |
| 实体提取 | `MemoryWikiVaultSyncer` + `extract_memory_entities` | ✅ 轻量规则级 |
| 实体共现图 | `WikiVaultGraphEdge` / `_entity_edges`（Counter） | ✅ 基础图谱 |
| Markdown 记忆 | `MarkdownMemoryStore` + 反思提炼 | ✅ 用户/Agent/全局三级 |
| 节点记忆 | `MemoryNodeService`（SQLite + 向量索引） | ✅ 含搜索文本 |
| 记忆准入/反思 | `MemoryAdmissionGate` + `MemoryReflectionAgent` | ✅ 可扩展 |

### 当前缺失（需新增）

1. **文档级知识图谱** — 将文档拆分为 chunk + 实体节点 + 关系边，并持久化到 Memgraph
2. **图遍历检索** — 从起始实体沿关联边做 BFS/DFS 召回（Cypher 原生支持）
3. **融合检索** — 向量召回 + 图召回 → 重排序 → LLM 生成
4. **LLM 驱动的实体/关系抽取** — 从非结构化内容中提取实体-关系三元组

---

## 二、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Graph RAG Pipeline                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   [Document Ingestion]                                      │
│   ┌──────────────┐    ┌───────────────┐    ┌────────────┐  │
│   │  Document     │───▶│ Chunking +    │───▶│ LLM Entity │  │
│   │  Loader       │    │ Embedding     │    │ Extraction │  │
│   └──────────────┘    └───────────────┘    └─────┬──────┘  │
│                                                   │         │
│   [Knowledge Graph Storage]                       ▼         │
│   ┌────────────────────────────────────────────────────┐   │
│   │  GraphStore (Memgraph + Zvec)                      │   │
│   │                                                     │   │
│   │  ┌──────────────┐    ┌──────────────┐              │   │
│   │  │  Memgraph     │    │  Zvec (Zinc) │              │   │
│   │  │  (图引擎)     │    │  (向量引擎)  │              │   │
│   │  │               │    │              │              │   │
│   │  │  :Chunk       │    │  graph_chunks│             │   │
│   │  │  :Entity      │    │  collection  │             │   │
│   │  │  :Document    │    │  (BM25+Dense)│             │   │
│   │  │  [:RELATED_TO]│    │              │             │   │
│   │  │  [:MENTIONED] │    │              │             │   │
│   │  └──────────────┘    └──────────────┘              │   │
│   └────────────────────────────────────────────────────┘   │
│                                                             │
│   [Retrieval & Generation]                                  │
│   ┌────────────────────────────────────────────────────┐   │
│   │  Retriever (Fusion)                                 │   │
│   │  ┌────────────┐  ┌────────────┐  ┌────────────┐   │   │
│   │  │ Vector     │  │ Graph      │  │ Keyword    │   │   │
│   │  │ Retrieval  │ + │ Traversal  │ + │ BM25       │   │   │
│   │  │ (Zvec)     │  │ (Cypher)   │  │ (Zvec)     │   │   │
│   │  └────────────┘  └────────────┘  └────────────┘   │   │
│   │            │               │                      │   │
│   │            ▼               ▼                      │   │
│   │       ┌──────────────────────────┐                 │   │
│   │       │  Reranker / Context      │                 │   │
│   │       │  Assembler               │                 │   │
│   │       └────────────┬─────────────┘                 │   │
│   │                    ▼                               │   │
│   │       ┌──────────────────────────┐                 │   │
│   │       │  LLM Generation          │                 │   │
│   │       └──────────────────────────┘                 │   │
│   └────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 为什么选择 Memgraph

| 维度 | SQLite（原方案） | Memgraph（v2.0） |
|------|-----------------|------------------|
| 存储引擎 | 关系型表 | **原生图数据库**（Labeled Property Graph） |
| 查询语言 | SQL JOIN | **Cypher**（声明式图查询） |
| 图遍历 | 代码层 BFS 模拟 | **原生 BFS/DFS/最短路径**（Cypher 内置） |
| 多跳查询 | 多层 JOIN，随深度指数级增长 | 固定时间复杂度，深度无关 |
| 内存模式 | 磁盘 I/O 瓶颈 | **内存级**（in-memory + 可持久化快照） |
| Cypher 兼容 | ❌ | ✅ **与 Neo4j 语法兼容** |
| 部署复杂度 | 无 | **单二进制启动**，零依赖 |
| 事务 | 支持 | 支持 ACID |
| 向量存储 | SQLite BLOB | 仍由 Zvec 负责（各司其职） |

---

## 三、模块详细设计

### 3.1 知识图谱存储 — `GraphStore`

```
backend/src/graph_store/
├── __init__.py              # 公共导出
├── models.py                # 数据模型（保持与 Memgraph 节点的映射）
├── graph_db.py              # Memgraph 持久化与操作层
├── graph_vector.py          # Zvec 向量索引层（复用现有接口）
├── extraction.py            # LLM 实体/关系抽取
├── traversal.py             # 图遍历（封装 Cypher 查询）
└── retrieval.py             # 融合检索器
```

**架构说明**：Memgraph 负责**图结构**（节点 + 关系），Zvec 负责**向量索引**（Chunk 文本的语义搜索）。两者通过 `chunk_id` / `entity_id` 关联，互不耦合。

#### 数据模型（`models.py`）

```python
from dataclasses import dataclass, field

# ── Memgraph 节点标签 ──
# :Document     — 源文档
# :Chunk        — 文档分块
# :Entity       — 实体节点（按 type 属性区分人物/概念/技术等）
#
# Memgraph 关系类型
# [:PART_OF]        — Chunk → Document
# [:MENTIONS]       — Chunk → Entity（该 chunk 提及了某实体）
# [:RELATED_TO]     — Entity → Entity（实体间关系，带 type 属性）
# [:HAS_CHUNK]      — Entity → Chunk（该实体出现在哪个 chunk 中）
# [:HAS_ENTITY]     — Document → Entity

@dataclass(frozen=True)
class GraphDocument:
    """源文档（一个文件/URL 对应一个文档）。"""
    id: str
    title: str
    text: str
    source: str                # "file", "web", "memory_node", etc.
    source_path: str           # 源路径
    metadata: dict = field(default_factory=dict)

@dataclass(frozen=True)
class GraphChunk:
    """文档分块后的 Chunk。"""
    id: str
    document_id: str
    chunk_index: int
    text: str
    embedding: list[float] | None = None

@dataclass(frozen=True)
class GraphEntity:
    """知识图谱实体节点。"""
    id: str                  # "entity:{type}:{name}"，如 "entity:person:爱因斯坦"
    type: str                # "person", "organization", "concept", "technology", "tool"
    name: str
    description: str
    aliases: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class GraphRelation:
    """知识图谱关系边。"""
    id: str                  # "rel:{source}:{target}:{type}"
    source_id: str
    target_id: str
    type: str                # "works_at", "authored_by", "part_of", "related_to", etc.
    weight: float = 1.0
```

### 3.2 Memgraph 持久化与操作层（`graph_db.py`）

#### 启动与连接

Memgraph 以**独立进程**运行，通过 Bolt 协议（TCP 7687 端口）连接。

```python
# ── 启动方式 ──
# $ brew install memgraph        # macOS
# $ memgraph                     # 启动服务（默认端口 7687）
#
# 或以 Docker 方式：
# $ docker run -p 7687:7687 memgraph/memgraph:latest

from mgclient import connect, Connection

class MemgraphDB:
    """Memgraph 图数据库连接与操作封装。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 7687):
        self._conn: Connection = connect(host=host, port=port)
        self._init_constraints()

    def _init_constraints(self):
        """初始化唯一约束（对标原方案的 PRIMARY KEY）。"""
        with self._conn.cursor() as cur:
            cur.execute("CREATE CONSTRAINT IF NOT EXISTS ON (d:Document) ASSERT d.id IS UNIQUE;")
            cur.execute("CREATE CONSTRAINT IF NOT EXISTS ON (c:Chunk) ASSERT c.id IS UNIQUE;")
            cur.execute("CREATE CONSTRAINT IF NOT EXISTS ON (e:Entity) ASSERT e.id IS UNIQUE;")
            self._conn.commit()

    # ── 写入操作 ──

    def upsert_document(self, doc: GraphDocument) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                MERGE (d:Document {id: $id})
                SET d.title = $title,
                    d.text = $text,
                    d.source = $source,
                    d.source_path = $source_path,
                    d.metadata = $metadata,
                    d.updated_at = timestamp()
                """,
                {"id": doc.id, "title": doc.title, "text": doc.text,
                 "source": doc.source, "source_path": doc.source_path,
                 "metadata": json.dumps(doc.metadata)}
            )
            self._conn.commit()

    def upsert_entity(self, entity: GraphEntity) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                """
                MERGE (e:Entity {id: $id})
                SET e.type = $type,
                    e.name = $name,
                    e.description = $description,
                    e.aliases = $aliases,
                    e.updated_at = timestamp()
                """,
                {"id": entity.id, "type": entity.type, "name": entity.name,
                 "description": entity.description,
                 "aliases": json.dumps(entity.aliases)}
            )
            self._conn.commit()

    def upsert_chunk(self, chunk: GraphChunk) -> None:
        """创建 Chunk 节点 + 与 Document 的 PART_OF 关系。"""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                MERGE (c:Chunk {id: $id})
                SET c.document_id = $document_id,
                    c.chunk_index = $chunk_index,
                    c.text = $text,
                    c.updated_at = timestamp()
                """,
                {"id": chunk.id, "document_id": chunk.document_id,
                 "chunk_index": chunk.chunk_index, "text": chunk.text}
            )
            # 关联到 Document
            cur.execute(
                """
                MATCH (c:Chunk {id: $chunk_id})
                MATCH (d:Document {id: $doc_id})
                MERGE (c)-[:PART_OF]->(d)
                """,
                {"chunk_id": chunk.id, "doc_id": chunk.document_id}
            )
            self._conn.commit()

    def upsert_relation(self, rel: GraphRelation) -> None:
        """创建实体间关系（含权重属性）。"""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                MATCH (s:Entity {id: $source_id})
                MATCH (t:Entity {id: $target_id})
                MERGE (s)-[r:RELATED_TO {type: $rel_type}]->(t)
                SET r.weight = $weight,
                    r.updated_at = timestamp()
                """,
                {"source_id": rel.source_id, "target_id": rel.target_id,
                 "rel_type": rel.type, "weight": rel.weight}
            )
            self._conn.commit()

    def link_chunk_entity(self, chunk_id: str, entity_id: str) -> None:
        """创建 Chunk → Entity 的 MENTIONS 关系。"""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                MATCH (c:Chunk {id: $chunk_id})
                MATCH (e:Entity {id: $entity_id})
                MERGE (c)-[:MENTIONS]->(e)
                """,
                {"chunk_id": chunk_id, "entity_id": entity_id}
            )
            self._conn.commit()

    # ── 读取操作 ──

    def get_entity_by_id(self, entity_id: str) -> dict | None:
        with self._conn.cursor() as cur:
            cur.execute("MATCH (e:Entity {id: $id}) RETURN e", {"id": entity_id})
            row = cur.fetchone()
            return dict(row[0]) if row else None

    def get_entity_by_name(self, name: str) -> list[dict]:
        """按名称模糊匹配实体。"""
        with self._conn.cursor() as cur:
            cur.execute(
                "MATCH (e:Entity) WHERE e.name CONTAINS $name RETURN e",
                {"name": name}
            )
            return [dict(row[0]) for row in cur.fetchall()]

    def close(self):
        self._conn.close()
```

#### 对比 SQLite 方案的优势（关键查询示例）

```cypher
-- 原方案（SQLite）：多跳关系需要递归 JOIN
-- SELECT e2.* FROM graph_relations r1
-- JOIN graph_relations r2 ON r1.target_id = r2.source_id
-- JOIN graph_entities e2 ON r2.target_id = e2.id
-- WHERE r1.source_id = ? AND r1.type = ? AND r2.type = ?

-- Memgraph 方案：Cypher 一步到位
MATCH (start:Entity {id: $id})-[:RELATED_TO*2]->(hop2:Entity)
RETURN DISTINCT hop2

-- 同时获取路径上的关系和节点
MATCH path = (start:Entity {id: $id})-[:RELATED_TO*1..3]->(related:Entity)
RETURN path
```

### 3.3 LLM 实体/关系抽取（`extraction.py`）

与 v1.0 保持一致，无变化。遵循项目已有模式，设计一个轻量级 **GraphExtractionAgent**：

```
GraphExtractionAgent
├── 输入: chunk_text
├── 输出: JSON
│   ├── entities: [{name, type, description}]
│   └── relations: [{source, target, type}]
└── 使用 AgentScope 的 structured output（Pydantic model）
```

**关键设计决策：**

- **每个 chunk 独立抽取**，避免上下文窗口超限
- **使用项目已有的 `AgentGuardrails` + `RuntimeToolScope`** 模式
- **抽取后去重合并**：同名实体合并，关系权重累加
- **Prompt 模板**提示从技术文档中提取"概念、技术、组件、人名、组织"五类实体

### 3.4 图遍历检索（`traversal.py`）

利用 Memgraph 原生的 Cypher 遍历能力，去除原方案中手写 BFS 的代码。

```python
from dataclasses import dataclass, field

@dataclass
class GraphTraversalResult:
    """图遍历结果。"""
    nodes: list[dict] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    chunk_ids: set[str] = field(default_factory=set)

class GraphTraverser:
    """基于 Memgraph 的图遍历检索器。"""

    def __init__(self, db: MemgraphDB):
        self._db = db

    def bfs_traverse(
        self,
        start_entity_ids: list[str],
        max_depth: int = 2,
        max_nodes: int = 50,
    ) -> GraphTraversalResult:
        """从起始实体出发，沿 RELATED_TO 做 BFS 遍历。

        利用 Cypher 的 BFS 原生能力（比 Python 手写 BFS 快 10-100x）。
        """
        with self._db._conn.cursor() as cur:
            # 查询关联实体及路径上的关系
            cur.execute(
                """
                MATCH path = (start:Entity)
                    -[:RELATED_TO*1..$max_depth]-(related:Entity)
                WHERE start.id IN $start_ids
                WITH path, related
                LIMIT $max_nodes
                RETURN
                    nodes(path) AS entities,
                    relationships(path) AS rels,
                    [n IN nodes(path) WHERE n:Chunk | n.id] AS chunk_ids
                """,
                {"start_ids": start_entity_ids,
                 "max_depth": max_depth,
                 "max_nodes": max_nodes}
            )
            rows = cur.fetchall()

        # 合并结果
        nodes_map = {}
        edges_set = set()
        chunk_ids = set()

        for row in rows:
            for node in row[0]:  # entities
                nodes_map[node["id"]] = dict(node)
            for rel in row[1]:   # rels
                edges_set.add({
                    "source": rel["source_id"],
                    "target": rel["target_id"],
                    "type": rel["type"],
                    "weight": rel.get("weight", 1.0)
                })
            chunk_ids.update(row[2] if row[2] else [])

        return GraphTraversalResult(
            nodes=list(nodes_map.values()),
            edges=list(edges_set),
            chunk_ids=chunk_ids,
        )

    def entity_link_traverse(
        self,
        entity_id: str,
        direction: str = "both"
    ) -> list[dict]:
        """查询某实体的所有一阶关系。"""
        dir_pattern = {
            "outgoing": "(e:Entity {id: $id})-[r:RELATED_TO]->(target:Entity)",
            "incoming": "(target:Entity)-[r:RELATED_TO]->(e:Entity {id: $id})",
            "both": "(e:Entity {id: $id})-[r:RELATED_TO]-(target:Entity)",
        }[direction]

        with self._db._conn.cursor() as cur:
            cur.execute(
                f"MATCH {dir_pattern} RETURN target, r",
                {"id": entity_id}
            )
            return [
                {"target": dict(row[0]), "relation": dict(row[1])}
                for row in cur.fetchall()
            ]

    def find_entity_by_chunk(self, chunk_id: str) -> list[dict]:
        """查找某 chunk 中提及的所有实体。"""
        with self._db._conn.cursor() as cur:
            cur.execute(
                """
                MATCH (c:Chunk {id: $chunk_id})-[:MENTIONS]->(e:Entity)
                RETURN e
                """,
                {"chunk_id": chunk_id}
            )
            return [dict(row[0]) for row in cur.fetchall()]
```

### 3.5 融合检索器（`retrieval.py`）

与 v1.0 核心流程一致，不同的是图检索部分调用 `GraphTraverser`。

```python
@dataclass
class GraphRagQuery:
    text: str
    top_k_chunks: int = 10
    top_k_entities: int = 8
    graph_max_depth: int = 2
    enable_graph: bool = True
    enable_vector: bool = True

@dataclass
class GraphRagResult:
    chunks: list[dict]            # 含 score 的 chunk 列表
    entities: list[dict]          # 含 score 的实体列表
    subgraph: GraphTraversalResult
    context_text: str             # 组装后的上下文文本

class GraphRagRetriever:
    """融合检索器：向量 + 图 + BM25 三路融合。"""

    def __init__(self, db: MemgraphDB, zvec_backend, traverser: GraphTraverser):
        self._db = db
        self._zvec = zvec_backend
        self._traverser = traverser

    def retrieve(self, query: GraphRagQuery) -> GraphRagResult:
        # 1. 向量检索 → top_k chunks（Zvec BM25 + Dense）
        vector_chunks = self._zvec.search(
            collection="graph_chunks",
            query=query.text,
            top_k=query.top_k_chunks,
        ) if query.enable_vector else []

        # 2. 从查询中提取实体名 → 图遍历
        query_entities = self._extract_entities_from_query(query.text)

        graph_result = GraphTraversalResult()
        if query_entities and query.enable_graph:
            # 在 Memgraph 中匹配查询实体
            matched = self._match_entities(query_entities)
            if matched:
                graph_result = self._traverser.bfs_traverse(
                    start_entity_ids=[e["id"] for e in matched],
                    max_depth=query.graph_max_depth,
                    max_nodes=query.top_k_entities * 5,
                )

        # 3. 合并去重
        merged = self._merge_results(vector_chunks, graph_result)

        # 4. 上下文组装（截断 token 上限）
        context = self._assemble_context(merged)

        return GraphRagResult(
            chunks=merged["chunks"],
            entities=merged["entities"],
            subgraph=graph_result,
            context_text=context,
        )

    def _extract_entities_from_query(self, text: str) -> list[str]:
        """从查询文本中提取可能的实体名。

        优先使用轻量规则（已有的 extract_memory_entities），
        失败时回退到 LLM 识别。
        """
        # 复用现有规则抽取
        ...

    def _match_entities(self, names: list[str]) -> list[dict]:
        """在 Memgraph 中匹配实体节点。"""
        with self._db._conn.cursor() as cur:
            cur.execute(
                """
                MATCH (e:Entity)
                WHERE any(name IN $names WHERE e.name CONTAINS name)
                RETURN e
                LIMIT $limit
                """,
                {"names": names, "limit": 10}
            )
            return [dict(row[0]) for row in cur.fetchall()]
```

**检索流程：**

```
用户查询
  │
  ├── 1. 向量检索 → top_k chunks（Zvec BM25 + Dense）
  │
  ├── 2. 实体识别 → 从查询中提取实体名（LLM / 规则分词）
  │      │
  │      └── 3. 图遍历 → Cypher BFS 从匹配实体出发，
  │                      沿 RELATED_TO 扩展到关联实体和 chunk
  │
  ├── 4. 合并去重 → 融合 1 和 3 的结果
  │
  └── 5. 上下文组装 → 按重要性排序，截断 token 上限
       │
       └── 6. LLM 生成
```

### 3.6 与现有记忆/Wiki 系统的融合

| 现有模块 | 融合方式 |
|---------|---------|
| `MemoryWikiVaultSyncer` | 将其 `WikiVaultGraphNode/Edge` 的共现数据作为实体关系，通过 `upsert_relation()` 写入 Memgraph（**取代原 SQLite 的 `graph_relations` 表**） |
| `extract_memory_entities` | 作为规则兜底层，在 LLM 抽取失败时提供基线实体，结果写入 Memgraph |
| `MemoryNodeService` | 把 `memory_nodes` 中高置信度的节点文本作为 `GraphDocument` 来源之一，导入 Memgraph |
| `MemoryReflectionAgent` | 在反思提炼时，同步触发实体抽取 → 写入 Memgraph |

### 3.7 对外 Tool 接口

按照项目 `ToolBase` 模式（参考 `knowledge_base_search.py` / `memory_nodes.py`），暴露两个 Tool：

```
GraphRagSearch(tool_id, name, ...)
  输入: {query, top_k, enable_graph, enable_vector}
  输出: 结构化结果 + 上下文文本

GraphRagAdmin(tool_id, name, ...)
  输入: {action: "ingest_document"|"rebuild_index"|"stats"|"cypher_query", ...}
  用于管理知识图谱的构建与维护
  - "cypher_query": 允许直接执行 Cypher 查询（仅管理员权限）
  - "stats": 返回 Memgraph 中节点/关系数量统计
```

---

## 四、实现路线图

### Phase 0 — 环境准备（0.5 天）

- [ ] 安装 Memgraph（brew / Docker）
- [ ] 启动 Memgraph 服务，验证 Bolt 连接（端口 7687）
- [ ] 安装 Python 驱动：`pip install mgclient`（或 `pymgclient`）
- [ ] 创建 `graph_store/` 模块骨架
- [ ] **产出**：Memgraph 服务就绪，连接测试通过

### Phase 1 — 核心存储（2-3 天）

- [ ] 定义数据模型 `models.py`
- [ ] 实现 `graph_db.py`：MemgraphDB 类（建约束 + CRUD）
- [ ] 实现 `graph_vector.py`：Chunk 向量写入 Zvec（与 v1.0 一致）
- [ ] **产出**：可通过 MemgraphDB 写入/读取实体、关系、Chunk

### Phase 2 — LLM 抽取（2 天）

- [ ] 设计 `GraphExtractionAgent` Pydantic schema
- [ ] 编写抽取 prompt 模板
- [ ] 实现 `extraction.py`：chunk→entities+relations
- [ ] **产出**：输入文档，自动生成知识图谱（写入 Memgraph + Zvec）

### Phase 3 — 检索与融合（2 天）

- [ ] 实现 `traversal.py`：基于 Cypher 的 BFS 遍历
- [ ] 实现 `retrieval.py`：双路融合检索（向量 + 图）
- [ ] 上下文组装 + token 截断策略
- [ ] **产出**：`GraphRagSearch(query)` 可用

### Phase 4 — 集成与工具（1-2 天）

- [ ] 注册 Tool：`GraphRagSearch`, `GraphRagAdmin`
- [ ] 导入已有 Wiki Vault 共现数据到 Memgraph
- [ ] 与 Memory Admission/Reflection 集成
- [ ] Memgraph 持久化策略（定期 SNAPSHOT 防止重启丢失数据）
- [ ] **产出**：Agent 可在对话中调用 Graph RAG

### Phase 5 — 优化（持续）

- [ ] 实体去重合并策略（Cypher MERGE + 别名匹配）
- [ ] 增量更新 vs 全量重建
- [ ] Memgraph 性能监控（Cypher 查询分析、遍历深度自动调优）
- [ ] 可选：Memgraph HA/备份策略

---

## 五、与现有项目的关键协同点

1. **复用 Zvec**：`graph_chunks` 集合直接使用现有 `ZvecVectorStoreBackend`，零成本获得 BM25 + Dense 双路检索。**Memgraph 只负责图结构，向量仍由 Zvec 处理**，各司其职。

2. **复用 AgentScope 模式**：抽取 Agent 沿用 `extraction_agents.py` 的 `agentscope.agents` + Pydantic structured output 模式，与图数据库解耦。

3. **复用 Tool 注册机制**：`GraphRagSearch` 沿用 `ToolBase` → `check_permissions` → `__call__` 链路，完全兼容现有 Agent 框架。

4. **兼容现有 Wiki Vault**：`WikiVaultGraphEdge` 的共现数据可以批量导入 Memgraph，作为 Graph RAG 图谱的种子数据，无需废弃现有基础设施。

5. **Cypher 与项目兼容性**：Memgraph 使用 **openCypher**（与 Neo4j 兼容），未来如需迁移到 Neo4j/AuraDB 只需更换连接字符串，代码层几乎零改动。

---

## 附录：Memgraph 快速启动指南

### macOS 安装

```bash
# 方式一：Homebrew
brew install memgraph
memgraph                        # 启动（默认端口 7687）

# 方式二：Docker（推荐开发环境）
docker run -it --rm \
  -p 7687:7687 \
  -p 3000:3000 \
  -v $(pwd)/mg_data:/var/lib/memgraph \
  memgraph/memgraph:latest
```

### Python 驱动安装

```bash
pip install mgclient
# 或
pip install pymgclient
```

### 数据持久化配置

Memgraph 默认是纯内存模式（重启后数据丢失）。生产环境需启用持久化：

```bash
memgraph --data-recovery-on-startup=true \
         --storage-properties-on-edges=true \
         --snapshot-frequency-sec=300
```

### Lab UI（可视化）

启动后访问 `http://localhost:3000`，可直接在浏览器中编写和执行 Cypher 查询。

---

*本方案 v2.0 以 Memgraph 替换原 SQLite 图存储，利用 Cypher 原生图遍历能力提升检索性能和简洁性。如需展开某个模块的具体实现代码，可随时继续。*
