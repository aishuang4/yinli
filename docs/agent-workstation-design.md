# Agent Workstation 设计文档

> 基于 AgentScope 2.0 + Electron + Spark Design 的开源桌面 Agent 工作台
>
> 创建时间：2026-06-06
> 状态：需求收集完成，待实现

---

## 1. 项目概述

### 1.1 产品定位

一个类似 Codex 的桌面 Agent 工作台，用户可以在聊天界面中选择不同的 Agent 来协作完成任务。支持多 Agent Team 编排、MCP 工具集成、Skills 技能加载、三层记忆系统，以及容器化的安全执行环境。

### 1.2 核心特征

- **对话驱动**：打开应用就是 Chat 界面，通过 @选择 Agent 来完成任务
- **多 Agent 协作**：Agent Team 管理，Leader-Worker 模式，动态子 Agent 调用
- **工具丰富**：默认工具自动注入 + MCP 工具市场 + Skills 技能库
- **智能记忆**：三层记忆架构（Agent 记忆 / 用户记忆 / 会话记忆）
- **安全可控**：权限系统 + 人工审批 + 容器沙箱
- **开源社区**：面向开发者和 AI 爱好者

---

## 2. 技术架构

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                  Electron Desktop App                    │
│                                                          │
│  ┌────────────────────────────────────────────────────┐  │
│  │            Frontend (React + TypeScript)            │  │
│  │                                                     │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐           │  │
│  │  │  Chat UI │ │ Agent    │ │ Memory   │           │  │
│  │  │          │ │ Team Mgr │ │ Panel    │           │  │
│  │  └──────────┘ └──────────┘ └──────────┘           │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐           │  │
│  │  │ MCP      │ │ Skills   │ │ Observ-  │           │  │
│  │  │ Browser  │ │ Browser  │ │ ability  │           │  │
│  │  └──────────┘ └──────────┘ └──────────┘           │  │
│  │                                                     │  │
│  │  UI: @agentscope-ai/design + @agentscope-ai/chat   │  │
│  └──────────────────────┬─────────────────────────────┘  │
│                         │ Event Stream (SSE/WebSocket)   │
│  ┌──────────────────────┴─────────────────────────────┐  │
│  │         Backend (Python + AgentScope 2.0)           │  │
│  │                                                     │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐           │  │
│  │  │ Agent    │ │ Agent    │ │ Model    │           │  │
│  │  │ Engine   │ │ Team     │ │ Manager  │           │  │
│  │  └──────────┘ └──────────┘ └──────────┘           │  │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐           │  │
│  │  │ Tool     │ │ Memory   │ │ Perm-    │           │  │
│  │  │ System   │ │ System   │ │ ission   │           │  │
│  │  └──────────┘ └──────────┘ └──────────┘           │  │
│  │                                                     │  │
│  │  Model: 百炼 DashScope  |  Container: Docker       │  │
│  │  Storage: SQLite (+ Milvus 预留)                    │  │
│  └────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### 2.2 技术栈明细

| 层 | 技术 | 版本/备注 |
|---|---|---|
| 桌面壳 | Electron | 最新稳定版 |
| 前端框架 | React + TypeScript | |
| UI 组件 | `@agentscope-ai/design` | 基于 Ant Design，阿里飞天实验室 |
| 聊天组件 | `@agentscope-ai/chat` | ChatAnywhere, Bubble, Sender, Markdown, Mermaid |
| Agent 框架 | AgentScope 2.0 | Python 3.11+，Apache 2.0 |
| 后端服务 | AgentScope Agent Service | FastAPI，多租户多会话 |
| 模型 | 阿里百炼 DashScope | `DashScopeChatModel` |
| 通信协议 | Event Stream | SSE/WebSocket，TEXT_BLOCK_DELTA 等 |
| 容器 | AgentScope Workspace | Docker 优先 |
| 存储 | SQLite | MVP 主力存储 |
| 向量存储 | Milvus | 接口预留，后期扩展 |
| Python 管理 | uv | init / add / sync / run |

### 2.3 开发/部署模式

| 阶段 | 前端 | 后端 | 通信 |
|---|---|---|---|
| 开发 | Electron dev server (热更新) | 独立 FastAPI 进程 | HTTP localhost |
| 打包 | 打包进 Electron | Python 作为子进程启动 | 进程间通信 |

---

## 3. 前端模块设计

### 3.1 Chat 界面（主入口）

用户打开应用的第一界面，核心交互：

- 对话窗口：复用 `@agentscope-ai/chat` 的 `ChatAnywhere` 组件
- Agent 选择：在输入框 @ 或从侧边栏切换当前 Agent
- 实时事件流：显示 Agent 思考过程（thinking）、工具调用、子 Agent 调度
- 工具调用可视化：每次工具调用显示为独立 Block，可展开查看详情
- 人工审批：高风险操作弹出确认对话框

### 3.2 Agent Team 管理界面

- Agent 列表：卡片式展示所有 Agent（头像 + 名称 + 描述）
- 创建 Agent：表单配置（详见第 5 节 Agent 配置模型）
- 编辑/删除 Agent
- Agent 关系图：可视化 Agent 间的调用关系（Leader-Worker 树）
- Team 模板：预设常用组合（代码审查 Team、数据分析 Team 等）

### 3.3 MCP 浏览器

- 浏览可用 MCP Server 列表
- 每个 MCP Server 展示：名称、描述、提供的工具列表
- 创建 Agent 时勾选需要的 MCP 工具
- MCP Server 安装/卸载管理

### 3.4 Skills 浏览器

- 浏览可用 Skills 列表
- 每个 Skill 展示：名称、描述、触发条件
- 创建 Agent 时勾选需要的 Skills

### 3.5 记忆面板

三层记忆可视化，可切换查看：

```
┌─────────────────────────────────────┐
│  [Agent 记忆] [用户记忆] [会话记忆]    │  ← Tab 切换
├─────────────────────────────────────┤
│                                     │
│  记忆条目列表                        │
│  ├── 记忆内容摘要                    │
│  ├── 创建时间                        │
│  ├── 来源（哪次对话/哪个操作）        │
│  └── 操作：编辑 / 删除 / 固定        │
│                                     │
└─────────────────────────────────────┘
```

### 3.6 可观测性面板

- 思考链展示：实时显示 Agent 推理过程
- 工具调用日志：输入/输出/耗时
- Agent 间消息追踪：调用链可视化
- Token 用量统计：按 Agent / 按会话 / 总计

### 3.7 主题系统

- 亮色/暗色一键切换
- 多主题支持（可扩展）
- 基于 Spark Design 的 Ant Design 主题配置

---

## 4. 后端模块设计

### 4.1 Agent 引擎

基于 AgentScope 2.0 的 `Agent` 类：

```python
from agentscope.agent import Agent
from agentscope.tool import Toolkit, Bash, Grep, Glob, Read, Write, Edit
from agentscope.model import DashScopeChatModel
from agentscope.credential import DashScopeCredential

# 每个 Agent 实例化时注入默认工具 + 用户选装工具
agent = Agent(
    name="Friday",
    system_prompt="...",
    model=model,
    toolkit=Toolkit(tools=[
        # 默认工具（自动注入）
        Bash(), Grep(), Glob(), Read(), Write(), Edit(),
        # 用户选装的 MCP 工具和 Skills
        # ...
    ]),
)
```

### 4.2 多 Agent Team

基于 AgentScope 2.0 的 Agent Team 机制：

- **Leader Agent**：接收用户任务，拆解并分配给 Worker
- **Worker Agents**：执行具体子任务
- **动态子 Agent**：Leader 可以在运行时动态创建和调度子 Agent
- **消息中心 (MsgHub)**：Agent 间的消息传递和协调

### 4.3 Event 系统

AgentScope 2.0 的事件流驱动前端：

| 事件类型 | 含义 | 前端表现 |
|---------|------|---------|
| `REPLY_START` | Agent 开始回复 | 显示加载状态 |
| `MODEL_CALL_START` | 开始调用模型 | 状态栏"正在思考..." |
| `TEXT_BLOCK_START` | 文本块开始 | 开始渲染消息气泡 |
| `TEXT_BLOCK_DELTA` | 文本增量 | 流式打字效果 |
| `TEXT_BLOCK_END` | 文本块结束 | 完成渲染 |
| `TOOL_CALL_START` | 工具调用开始 | 显示工具调用 Block |
| `TOOL_CALL_END` | 工具调用结束 | 展示结果 |

### 4.4 工具系统

#### 默认工具（自动注入，不可关闭）

| 工具 | AgentScope 类 | 功能 |
|------|---------------|------|
| Read | `agentscope.tool.Read` | 读取文件内容 |
| Write | `agentscope.tool.Write` | 写入/创建文件 |
| Edit | `agentscope.tool.Edit` | 局部编辑文件 |
| Grep | `agentscope.tool.Grep` | 搜索文件内容 |
| Glob | `agentscope.tool.Glob` | 按模式查找文件 |
| Bash | `agentscope.tool.Bash` | 执行 Shell 命令 |

#### 可选工具（用户勾选）

- MCP 工具（从 MCP 浏览器中选择）
- Skills（从 Skills 浏览器中选择）
- 高级工具（网络请求、数据库操作等）

### 4.5 权限系统

基于 AgentScope 2.0 的 Permission System：

- **文件系统范围**：限制 Agent 可操作的目录
- **Shell 命令控制**：白名单/黑名单
- **网络权限**：可配置的网络访问策略
- **人工审批 (Human-in-the-Loop)**：高风险操作需用户确认
- **执行超时**：单个工具调用的最大执行时间

### 4.6 Workspace / Sandbox

基于 AgentScope 2.0 的 Workspace：

- **Docker 模式**（推荐）：完全隔离的容器环境
- **本地模式**：直接在本机执行（开发调试用）
- **E2B 模式**：云端沙箱（未来支持）

---

## 5. Agent 配置模型

### 5.1 完整配置结构

```yaml
agent_config:
  # === 身份 (Identity) ===
  identity:
    name: "CodeReviewer"              # Agent 名称
    avatar: "avatars/code-review.png" # 头像路径
    description: "专业的代码审查助手"   # 一句话描述（其他 Agent 参考）
    system_prompt: |                   # 系统提示词
      你是一个专业的代码审查助手...
    tags: ["code", "review"]          # 标签

  # === 模型 (Model) ===
  model:
    primary: "qwen-max"               # 主模型
    fallback:                         # Fallback 模型链（有序）
      - "qwen-plus"
      - "qwen-turbo"
      - "qwen-long"
    parameters:                        # 模型参数
      temperature: 0.3
      top_p: 0.9
      max_tokens: 4096
    retry:                             # 重试策略
      max_retries: 3
      backoff: "exponential"           # 1s → 2s → 4s
      retryable_errors: [429, 500, 502, 503, 504]
      json_auto_fix: true              # JSON 解析失败时自动修复重试

  # === 工具 (Tools) ===
  tools:
    defaults:                          # 默认工具（自动注入，锁定）
      - Read
      - Write
      - Edit
      - Grep
      - Glob
      - Bash
    mcp_tools:                         # MCP 工具（用户勾选）
      - "github:pr_review"
      - "github:issue_search"
    skills:                            # Skills（用户勾选）
      - "code-review"
      - "test-generation"
    advanced:                          # 高级工具（用户勾选）
      - "http_request"
      - "database_query"

  # === 安全边界 (Guardrails) ===
  guardrails:
    filesystem_scope: "/workspace"     # 可操作的文件系统范围
    shell_blacklist:                   # Shell 命令黑名单
      - "rm -rf /"
      - "sudo"
    shell_whitelist: []                # Shell 命令白名单（空=不限制）
    network:
      enabled: true                    # 是否允许网络访问
      allowed_domains: []              # 允许的域名（空=不限制）
    human_approval:                    # 人工审批规则
      enabled: true
      triggers:                        # 触发审批的操作
        - "file_delete"
        - "shell_sudo"
        - "network_external"
    timeout:
      tool_call: 300                   # 工具调用超时（秒）
      agent_reply: 600                 # Agent 回复超时（秒）

  # === 协作 (Collaboration) ===
  collaboration:
    callable_agents:                   # 可调用的子 Agent 列表
      - "TestRunner"
      - "DocWriter"
    max_depth: 2                       # 最大调用深度
    max_concurrent: 3                  # 最大并发子 Agent 数
    failure_strategy: "retry_then_escalate"  # 失败处理：重试 → 换人 → 上报

  # === 记忆 (Memory) ===
  memory:
    write_trigger: "auto"              # 写入触发：auto / manual / per_turn
    capacity: 1000                     # Agent 记忆容量上限
    eviction: "importance_lru"         # 淘汰策略
    retrieval: "keyword"               # 检索方式：keyword / vector（Milvus 后期）
    visible_to_user: true              # 记忆对用户可见

  # === 可观测性 (Observability) ===
  observability:
    thinking_chain: true               # 是否展示思考链
    log_level: "info"                  # 日志级别
    token_tracking: true               # Token 用量追踪
```

---

## 6. 模型调用降级重试策略

### 6.1 故障分类

```
模型调用失败
│
├── 可重试（自动重试，用户无感知）
│   ├── 限流 (429 Rate Limit)    → 指数退避重试，最多 3 次
│   ├── 网络超时                  → 立即重试 1 次，再指数退避
│   ├── 服务端错误 (500/502/503)  → 指数退避重试
│   └── JSON 格式解析错误         → 自动修复提示词后重试 1 次
│
├── 可降级（切换备用方案）
│   ├── 敏感词拦截 (Content Filter) → 降级到 Fallback 模型重试
│   ├── 上下文超长 (Context Overflow) → 自动截断/压缩上下文后重试
│   └── 模型不可用                 → 切换到 Fallback 模型
│
└── 不可恢复（通知用户）
    ├── API Key 无效/过期          → 提示用户更新凭证
    ├── 账户余额不足               → 提示用户充值
    └── 重试次数耗尽               → 展示完整错误日志
```

### 6.2 Fallback 模型链

```
主模型 (qwen-max)
  → 1st Fallback (qwen-plus)
    → 2nd Fallback (qwen-turbo)
      → 3rd Fallback (qwen-long)
        → 最终失败，通知用户
```

### 6.3 前端反馈

| 状态 | 用户感知 |
|------|---------|
| 自动重试中 | 状态栏："模型繁忙，正在重试 (2/3)..." |
| 降级切换 | 通知气泡："已切换到 qwen-plus（主模型暂时不可用）" |
| 最终失败 | 错误详情弹窗 + 日志 + 操作建议 |
| 正常恢复 | 无感知，静默恢复 |

---

## 7. 三层记忆系统

### 7.1 架构

```
┌─────────────────────────────────────────┐
│         会话记忆 (Session Memory)        │
│         作用域：当前对话                  │
│         共享性：所有 Agent 共享           │
│         内容：对话历史、临时变量、任务上下文 │
│         持久性：会话结束后可选保留/清除     │
├─────────────────────────────────────────┤
│         用户记忆 (User Memory)           │
│         作用域：全局                      │
│         共享性：所有 Agent 共享           │
│         内容：用户偏好、习惯、项目知识      │
│         示例："用户喜欢简洁回复"           │
│         持久性：永久，用户可手动管理       │
├─────────────────────────────────────────┤
│         Agent 记忆 (Agent Memory)        │
│         作用域：单个 Agent                │
│         共享性：仅该 Agent 可访问          │
│         内容：学习成果、任务经验、专业知识   │
│         示例："上次这个 bug 的修复方式"     │
│         持久性：跟随 Agent 生命周期        │
└─────────────────────────────────────────┘
```

### 7.2 记忆操作

| 操作 | 触发方式 | 说明 |
|------|---------|------|
| 写入 | 自动提取 / 用户指令 / 每轮对话 | 根据 `write_trigger` 配置 |
| 读取 | Agent 回复前自动检索相关记忆 | 注入到 Agent 上下文 |
| 更新 | 新信息覆盖旧记忆 | 去重 + 合并 |
| 删除 | 用户手动 / 自动淘汰 | 容量超限时按策略淘汰 |
| 固定 | 用户手动 | 固定的记忆不被淘汰 |

### 7.3 检索策略

- **MVP（SQLite）**：关键词匹配 + TF-IDF 排序
- **后期（Milvus）**：向量语义检索，支持模糊匹配和语义相似度

---

## 8. 数据存储设计

### 8.1 SQLite 表结构

```sql
-- Agent 配置表
CREATE TABLE agents (
    id          TEXT PRIMARY KEY,       -- UUID
    name        TEXT NOT NULL,
    avatar      TEXT,
    description TEXT,
    system_prompt TEXT,
    model_config TEXT,                  -- JSON: 模型配置
    tool_config  TEXT,                  -- JSON: 工具配置
    guardrails   TEXT,                  -- JSON: 安全边界
    collab_config TEXT,                 -- JSON: 协作配置
    memory_config TEXT,                 -- JSON: 记忆配置
    observability TEXT,                 -- JSON: 可观测性配置
    tags        TEXT,                   -- JSON: 标签数组
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Agent Team 关系表
CREATE TABLE agent_teams (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    leader_id   TEXT REFERENCES agents(id),
    description TEXT,
    config      TEXT,                   -- JSON: Team 配置
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Agent Team 成员表
CREATE TABLE team_members (
    team_id     TEXT REFERENCES agent_teams(id),
    agent_id    TEXT REFERENCES agents(id),
    role        TEXT DEFAULT 'worker',  -- leader / worker
    PRIMARY KEY (team_id, agent_id)
);

-- 会话表
CREATE TABLE sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT,
    agent_id    TEXT REFERENCES agents(id),
    team_id     TEXT REFERENCES agent_teams(id),
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 对话消息表
CREATE TABLE messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT REFERENCES sessions(id),
    role        TEXT NOT NULL,          -- user / assistant / system / tool
    content     TEXT,
    agent_id    TEXT REFERENCES agents(id),
    metadata    TEXT,                   -- JSON: 工具调用详情、Token 用量等
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Agent 记忆表
CREATE TABLE agent_memories (
    id          TEXT PRIMARY KEY,
    agent_id    TEXT REFERENCES agents(id),
    content     TEXT NOT NULL,
    source      TEXT,                   -- 来源（哪次对话/操作）
    importance  REAL DEFAULT 0.5,       -- 重要性评分
    pinned      BOOLEAN DEFAULT FALSE,  -- 是否固定
    metadata    TEXT,                   -- JSON
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 用户记忆表
CREATE TABLE user_memories (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    category    TEXT,                   -- 分类（偏好/习惯/项目知识）
    source      TEXT,
    importance  REAL DEFAULT 0.5,
    pinned      BOOLEAN DEFAULT FALSE,
    metadata    TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 会话记忆表
CREATE TABLE session_memories (
    id          TEXT PRIMARY KEY,
    session_id  TEXT REFERENCES sessions(id),
    content     TEXT NOT NULL,
    source      TEXT,
    metadata    TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- MCP Server 注册表
CREATE TABLE mcp_servers (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    endpoint    TEXT,                   -- MCP Server 地址
    tools       TEXT,                   -- JSON: 提供的工具列表
    installed   BOOLEAN DEFAULT FALSE,
    config      TEXT,                   -- JSON: 连接配置
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Skills 注册表
CREATE TABLE skills (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    trigger     TEXT,                   -- 触发条件
    content     TEXT,                   -- Skill 内容
    installed   BOOLEAN DEFAULT FALSE,
    metadata    TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Agent-工具关联表
CREATE TABLE agent_tools (
    agent_id    TEXT REFERENCES agents(id),
    tool_type   TEXT NOT NULL,          -- default / mcp / skill / advanced
    tool_id     TEXT NOT NULL,          -- 工具标识
    config      TEXT,                   -- JSON: 工具参数配置
    PRIMARY KEY (agent_id, tool_type, tool_id)
);

-- Token 用量统计表
CREATE TABLE token_usage (
    id          TEXT PRIMARY KEY,
    session_id  TEXT REFERENCES sessions(id),
    agent_id    TEXT REFERENCES agents(id),
    model       TEXT NOT NULL,
    prompt_tokens   INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    cost        REAL DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX idx_messages_session ON messages(session_id, created_at);
CREATE INDEX idx_agent_memories ON agent_memories(agent_id, importance DESC);
CREATE INDEX idx_user_memories ON user_memories(importance DESC);
CREATE INDEX idx_session_memories ON session_memories(session_id);
CREATE INDEX idx_token_usage_session ON token_usage(session_id);
CREATE INDEX idx_token_usage_agent ON token_usage(agent_id);
```

### 8.2 Milvus 接口预留

```python
# memory/vector_store.py — 接口定义，后期用 Milvus 实现

from abc import ABC, abstractmethod
from typing import Optional

class VectorStoreInterface(ABC):
    """向量存储抽象接口，MVP 用 SQLite FTS5 实现，后期切换 Milvus"""

    @abstractmethod
    async def upsert(self, collection: str, doc_id: str, text: str, metadata: dict) -> None:
        """插入或更新向量"""
        pass

    @abstractmethod
    async def search(self, collection: str, query: str, top_k: int = 5) -> list[dict]:
        """语义检索"""
        pass

    @abstractmethod
    async def delete(self, collection: str, doc_id: str) -> None:
        """删除向量"""
        pass

    @abstractmethod
    async def list(self, collection: str, limit: int = 50, offset: int = 0) -> list[dict]:
        """列出所有向量"""
        pass


class SQLiteFTS5Store(VectorStoreInterface):
    """MVP 实现：SQLite FTS5 全文检索"""
    pass


# 后期实现：
# class MilvusStore(VectorStoreInterface):
#     """Milvus 向量存储实现"""
#     pass
```

---

## 9. 项目目录结构

```
agent-workstation/
├── README.md
├── LICENSE                          # Apache 2.0
│
├── electron/                        # Electron 主进程
│   ├── main.ts                      # 主进程入口
│   ├── preload.ts                   # 预加载脚本
│   └── python-bridge.ts             # Python 子进程管理
│
├── frontend/                        # React 前端
│   ├── package.json
│   ├── tsconfig.json
│   ├── src/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   ├── components/
│   │   │   ├── chat/                # Chat 界面组件
│   │   │   │   ├── ChatView.tsx
│   │   │   │   ├── MessageBubble.tsx
│   │   │   │   ├── AgentSelector.tsx
│   │   │   │   └── ToolCallBlock.tsx
│   │   │   ├── agent/               # Agent 管理组件
│   │   │   │   ├── AgentList.tsx
│   │   │   │   ├── AgentEditor.tsx
│   │   │   │   ├── AgentTeamView.tsx
│   │   │   │   └── AgentRelationGraph.tsx
│   │   │   ├── memory/              # 记忆面板组件
│   │   │   │   ├── MemoryPanel.tsx
│   │   │   │   ├── MemoryList.tsx
│   │   │   │   └── MemoryEditor.tsx
│   │   │   ├── tools/               # MCP/Skills 浏览器
│   │   │   │   ├── MCPBrowser.tsx
│   │   │   │   ├── SkillsBrowser.tsx
│   │   │   │   └── ToolSelector.tsx
│   │   │   ├── observability/       # 可观测性面板
│   │   │   │   ├── ThinkingChain.tsx
│   │   │   │   ├── ToolCallLog.tsx
│   │   │   │   └── TokenStats.tsx
│   │   │   └── common/              # 通用组件
│   │   │       ├── ThemeSwitcher.tsx
│   │   │       └── Settings.tsx
│   │   ├── hooks/                   # 自定义 Hooks
│   │   │   ├── useAgent.ts
│   │   │   ├── useChat.ts
│   │   │   ├── useMemory.ts
│   │   │   └── useEventStream.ts
│   │   ├── services/                # API 服务层
│   │   │   ├── api.ts               # HTTP API 封装
│   │   │   └── eventStream.ts       # Event Stream 客户端
│   │   ├── stores/                  # 状态管理 (Zustand/Redux)
│   │   │   ├── agentStore.ts
│   │   │   ├── chatStore.ts
│   │   │   └── settingsStore.ts
│   │   ├── themes/                  # 主题配置
│   │   │   ├── light.ts
│   │   │   ├── dark.ts
│   │   │   └── index.ts
│   │   └── types/                   # TypeScript 类型定义
│   │       ├── agent.ts
│   │       ├── chat.ts
│   │       └── memory.ts
│   └── public/
│
├── backend/                         # Python 后端
│   ├── pyproject.toml               # uv 项目配置
│   ├── uv.lock
│   ├── src/
│   │   ├── server/                  # FastAPI 服务
│   │   │   ├── __init__.py
│   │   │   ├── main.py              # FastAPI 入口
│   │   │   ├── routes/              # API 路由
│   │   │   │   ├── agents.py        # Agent CRUD
│   │   │   │   ├── teams.py         # Agent Team 管理
│   │   │   │   ├── chat.py          # 对话接口
│   │   │   │   ├── memory.py        # 记忆管理
│   │   │   │   ├── mcp.py           # MCP 管理
│   │   │   │   ├── skills.py        # Skills 管理
│   │   │   │   └── observability.py # 可观测性数据
│   │   │   └── middleware/          # 中间件
│   │   │       ├── cors.py
│   │   │       └── error_handler.py
│   │   ├── agents/                  # Agent 业务逻辑
│   │   │   ├── __init__.py
│   │   │   ├── factory.py           # Agent 工厂（从配置创建 Agent）
│   │   │   ├── team.py              # Agent Team 管理
│   │   │   └── lifecycle.py         # Agent 生命周期管理
│   │   ├── model/                   # 模型管理
│   │   │   ├── __init__.py
│   │   │   ├── manager.py           # 模型管理器
│   │   │   └── fallback.py          # 降级重试策略
│   │   ├── memory/                  # 记忆系统
│   │   │   ├── __init__.py
│   │   │   ├── three_layer.py       # 三层记忆实现
│   │   │   ├── vector_store.py      # 向量存储接口（Milvus 预留）
│   │   │   └── retrieval.py         # 记忆检索
│   │   ├── tools/                   # 工具管理
│   │   │   ├── __init__.py
│   │   │   ├── defaults.py          # 默认工具注册
│   │   │   ├── mcp_manager.py       # MCP 工具管理
│   │   │   └── skill_manager.py     # Skills 管理
│   │   ├── storage/                 # 数据存储
│   │   │   ├── __init__.py
│   │   │   ├── database.py          # SQLite 连接管理
│   │   │   ├── models.py            # ORM 模型
│   │   │   └── migrations/          # 数据库迁移
│   │   └── config/                  # 配置
│   │       ├── __init__.py
│   │       ├── settings.py          # 应用配置
│   │       └── defaults.py          # 默认值
│   └── tests/
│       ├── test_agents.py
│       ├── test_memory.py
│       ├── test_fallback.py
│       └── test_tools.py
│
├── docker/                          # Docker 配置
│   ├── Dockerfile.backend           # 后端镜像
│   └── docker-compose.yml           # 开发环境编排
│
├── scripts/                         # 辅助脚本
│   ├── dev.sh                       # 开发启动脚本
│   └── build.sh                     # 打包脚本
│
└── docs/                            # 文档
    ├── architecture.md
    ├── api.md
    └── contributing.md
```

---

## 10. 前后端通信协议

### 10.1 REST API

```
# Agent 管理
GET    /api/agents                   # 获取所有 Agent
POST   /api/agents                   # 创建 Agent
GET    /api/agents/:id               # 获取 Agent 详情
PUT    /api/agents/:id               # 更新 Agent
DELETE /api/agents/:id               # 删除 Agent

# Agent Team
GET    /api/teams                    # 获取所有 Team
POST   /api/teams                    # 创建 Team
PUT    /api/teams/:id                # 更新 Team
DELETE /api/teams/:id                # 删除 Team

# 对话
POST   /api/chat/send                # 发送消息（返回 session_id）
GET    /api/chat/sessions            # 获取所有会话
GET    /api/chat/sessions/:id/messages  # 获取会话消息

# 记忆
GET    /api/memory/agent/:agent_id   # 获取 Agent 记忆
GET    /api/memory/user              # 获取用户记忆
GET    /api/memory/session/:session_id  # 获取会话记忆
POST   /api/memory                   # 写入记忆
DELETE /api/memory/:id               # 删除记忆
PUT    /api/memory/:id               # 更新记忆

# MCP
GET    /api/mcp/servers              # 获取可用 MCP Server 列表
POST   /api/mcp/servers/:id/install  # 安装 MCP Server
DELETE /api/mcp/servers/:id          # 卸载 MCP Server

# Skills
GET    /api/skills                   # 获取可用 Skills 列表
POST   /api/skills/:id/install       # 安装 Skill
DELETE /api/skills/:id               # 卸载 Skill

# 可观测性
GET    /api/observability/tokens     # Token 用量统计
GET    /api/observability/logs       # 工具调用日志

# 系统
GET    /api/health                   # 健康检查
GET    /api/config                   # 获取系统配置
PUT    /api/config                   # 更新系统配置
```

### 10.2 Event Stream

对话过程中的实时事件通过 SSE (Server-Sent Events) 推送：

```
POST /api/chat/stream

Request:
{
  "session_id": "xxx",
  "agent_id": "yyy",
  "message": "帮我审查这段代码"
}

Response (SSE):
event: reply_start
data: {"agent_id": "yyy", "agent_name": "CodeReviewer"}

event: model_call_start
data: {"model": "qwen-max"}

event: text_block_delta
data: {"content": "好的，我来帮你"}

event: text_block_delta
data: {"content": "审查这段代码..."}

event: tool_call_start
data: {"tool": "Read", "input": {"path": "src/main.py"}}

event: tool_call_end
data: {"tool": "Read", "output": "...", "duration_ms": 120}

event: text_block_delta
data: {"content": "我发现以下问题..."}

event: reply_end
data: {"token_usage": {"prompt": 1200, "completion": 800}}
```

---

## 11. 优先级路线图

### P0 — MVP（第一版）

- [ ] Electron 壳 + 前端框架搭建
- [ ] Chat 界面（复用 Spark Chat）
- [ ] Agent 创建/管理
- [ ] 默认工具自动注入
- [ ] AgentScope 后端集成
- [ ] 百炼模型接入 + 降级重试
- [ ] SQLite 数据存储
- [ ] 基础安全边界（权限 + 人工审批）
- [ ] 亮/暗主题切换

### P1 — 核心功能完善

- [ ] 多 Agent Team 编排
- [ ] 动态子 Agent 调用
- [ ] MCP 浏览器 + 工具选择
- [ ] Skills 浏览器 + 技能选择
- [ ] 三层记忆系统
- [ ] 可观测性面板（思考链 + 工具日志 + Token 统计）
- [ ] Docker 容器沙箱

### P2 — 体验优化

- [ ] Agent 配置导入/导出
- [ ] Agent Team 预设模板
- [ ] 多主题扩展（不只是亮/暗）
- [ ] 模型参数精细调节
- [ ] 记忆面板交互优化（编辑/删除/固定）

### P3 — 远期

- [ ] Milvus 向量检索集成
- [ ] 社区 Agent 市场
- [ ] 记忆衰减和自动整理
- [ ] Agent 协作竞标模式
- [ ] 多语言支持（i18n）
- [ ] 自动更新机制

---

## 12. 代码规范

- **所有代码必须写注释**，包括：
  - 文件/模块级文档字符串
  - 类/函数/方法的 JSDoc (前端) 或 Docstring (后端)
  - 关键逻辑的行内注释
  - 复杂算法的注释说明
- 前端：TypeScript strict mode
- 后端：Python type hints + ruff linting
- Git 提交信息规范化 (Conventional Commits)
