# Agent Workstation 当前架构总览

> 状态：2026-07-06 生效。本文替代早期大而全设计稿，只记录当前仍在线的架构事实。

## 项目定位

Agent Workstation 是基于 AgentScope 2.0 + Electron + React 的桌面多 Agent 协作工作台。用户在 Chat 中选择单个 Agent 或 Team 执行任务，运行时支持 MCP、Skills、HTTP 工具、权限控制、HITL、Trace、容器沙箱和新记忆系统 MVP。

旧记忆模块、旧 Plan Runtime、旧 Subagent Runtime 已下线。相关字段仅作为历史 SQLite / API 兼容保留，不能继续扩展旧链路。当前记忆能力是新方案：通过运行前上下文注入、运行后后台提炼、历史记忆（底层 `memory_nodes`）和 memory tools 接入 ReAct，不新增 `memory` runtime.kind。

## 技术栈

- 桌面壳：Electron
- 前端：React 19、TypeScript、Vite、Zustand、`@agentscope-ai/design`、`@agentscope-ai/chat`、Ant Design
- 后端：Python 3.11、FastAPI、AgentScope 2.0、SQLite
- 模型：DeepSeek 默认，运行时可切换 OpenAI、Anthropic、DashScope、Gemini、Ollama、自定义 OpenAI 兼容等供应商
- 执行环境：本地 workspace 或 AgentScope DockerWorkspace

## Chat 核心链路

1. `routes/chat/handlers/send.py` 接收用户消息、落库、创建后台 Run，并编排 SSE。
2. `readiness_gate.py` 判断本轮信息是否足够；不足时直接返回 clarification，不进入 Runtime。
3. `intent_router.py` 只决定第一层 architecture。当前在线值为 `react`。
4. 前端选择决定第二层 executor：单 Agent 为 `single_agent`，Team 为 `team`。
5. `runtime_dispatcher.py` 只按 architecture 分发；当前只分发到 ReAct。
6. `react_runtime.py` 按 executor 执行单 Agent 或 Team。
7. `runtime_builder.py` 组装模型配置、Team 上下文、Skill、ToolScope 和主 Agent runtime spec。
8. `run_context.py` 在 readiness 通过后注入 Skill prompt 和记忆上下文；记忆注入位于 Skill prompt 后、clarification / final answer 规则前。
9. `leader_stream.py` 消费 AgentScope `reply_stream()`，转换文本、工具、权限、Plan Mode、Skill 和 HITL 事件。
10. `completion.py` 完成消息落库、Trace、Team / Skill 状态和错误收尾，并后台触发 Markdown 记忆提炼和历史记忆持久化。
11. `background_runs.py` 将后台 Run 与前端 SSE 订阅解耦，并维护 RunState。

## Team Runtime

Team 当前语义收束为：

```text
Leader dispatch -> Worker isolated ReAct -> Worker report -> Leader synthesize
```

Worker 选择由 Team Runtime 内部处理：优先使用系统内置 `TeamWorkerSelector` 模型选择，解析失败或调用异常时使用全部 Worker 兜底；模型合法返回空数组时只保留 Leader。

## RunState 与前端主展示

Chat 主界面只依赖 RunState / DisplaySnapshot：

- `/api/chat/send/start` 创建后台 Run。
- `/api/chat/resume/start` 恢复 HITL Run。
- `/api/chat/runs/{run_id}/state` 返回完整快照。
- `/api/chat/runs/{run_id}/state/stream` 首包返回 `RUN_STATE`，后续返回 `RUN_STATE_PATCH`。

原始 SSE 事件保留给兼容流、Trace 和后端 projector，不作为 Chat 主界面长期状态来源。稳定契约见 `docs/chat-sse-event-contract.md`。

## Plan Mode

当前 Plan Mode 不是旧 `runtime.kind=plan`。它基于 AgentScope middleware hooks 接入 ReAct：

- 支持单 Agent。
- 不支持 Team Runtime。
- `plan_enter` 进入只读计划阶段。
- `plan_write` / `todo_write` 更新 markdown 和结构化任务。
- `task_output` 更新任务执行状态。
- `plan_exit` 将计划 finalized，并把计划 markdown 作为本轮最终答案输出。
- 前端不再显示旧 approve / deny 审批入口，用户下一条自然语言消息决定修改或执行计划。

## 记忆系统 MVP

当前在线的是新记忆系统首版，不是旧 memory summarize：

- **运行前注入**：`memory/retrieval.py` 读取 user / agent / global Markdown 摘要和最近 30 天节点树；`run_context.py` 在 readiness 通过后注入。
- **历史记忆**：底层 `memory_nodes` 只保存用户输入、Agent 最终输出和摘要索引，不保存工具调用过程；写入后同步到 `VectorStoreBackend` 的 `memory_nodes` collection。
- **Markdown 长期记忆**：user memory、agent memory、global preference memory 分别保存为 `user_memory.md`、`agent_memory.md`、`global_memory.md`，没有旧 `default.md` 兼容读取。
- **Memory 管理入口**：前端 Memory 页面首版已接入，用户记忆和全局记忆各只有一个管理入口；Agent 记忆按 Agent 选择，每个 Agent 对应一份 `agent_memory.md`；历史记忆按“Agent -> 时间节点 -> 具体记忆”三段式查看，底层使用 `memory_nodes`。Skill 自进化队列保留在 Memory 页面作为中间态管理入口，下一步补 UI。
- **记忆查询工具**：普通 Agent 默认注入 `memory_node_tree` 和 `memory_node_query`，需要详情时由 Agent 主动查询。
- **运行后提炼**：`completion.py` 成功收尾后后台并行调用 `UserMemoryReflectionAgent`、`AgentMemoryReflectionAgent`、`GlobalMemoryReflectionAgent`，通过绑定文件的 `memory_read` / `memory_add` / `memory_update` / `memory_delete` 操作 Markdown 记忆，不阻塞主对话。
- **Skill 自进化现状**：已有 `SkillEvolutionAgent`、`skill_draft_propose` 草稿工具、router gate、workflow evidence、颗粒度 gate、变量流抽象、候选持久化、标准 Skill 注册、相似 Skill 合并和用户显式请求生成 Skill 的手动触发流；自动生成 / 手动注册 Skill 会写入 `backend/data/skill_packages/{skill_name}/SKILL.md` 并支持按 Skill 名称导出 zip。当前仍缺注册失败原因可视化和候选队列管理。
- **向量存储**：全局 `VectorStoreBackend` 抽象已支持 `disabled` / `zvec`，`VECTOR_STORE_PROVIDER` 仅首次初始化生效，之后按持久化 `VectorStorePolicy` 启动。

## 存储

`backend/src/storage/database.py` 使用同步 `sqlite3 + RLock`。核心结构化表包括：

- `agents`
- `teams`
- `team_members`
- `mcp_servers`
- `skills`
- `http_tools`
- `chat_sessions`
- `chat_messages`
- `chat_runs`
- `chat_run_events`
- `chat_run_states`
- `run_events`
- `memory_nodes`

历史开发库可能仍有 `memory_facts`、`memory_events`、`memory_candidates`，但旧记忆模块已删除，不再代表当前运行时能力。

## 配置与权限

- 启动期静态配置在 `config/settings.py`，环境变量前缀为 `AGENT_WORKSTATION_`。
- 运行时模型设置在 `config/runtime_settings.py`，持久化到 `json_store`。
- Agent 运行时由 `agents/factory.py` 统一创建和缓存。
- 工具范围由 `RuntimeToolScope` 与 `tool_scope_resolver.py` 控制。
- 权限策略由 guardrails 归一化为统一权限模型，再映射到 AgentScope permission mode、工具注入和 Trace。

## 前端入口

- `frontend/src/App.tsx`：页面映射、主题同步和健康检查。
- `frontend/src/pages/ChatPage.tsx`：Chat 主页面。
- `frontend/src/hooks/useEventStream.ts`：RunState / SSE 订阅入口。
- `frontend/src/types/runState.ts`：RunState 主链路类型。
- `frontend/src/types/displayEvents.ts`：DisplaySnapshot / DisplayEvent 展示契约。
- `frontend/src/features/chat/*`：Chat 展示投影与渲染组件。

## 开发原则

- 新 Chat 事件先更新 `docs/chat-sse-event-contract.md`。
- 新运行时能力优先接入 ReAct / middleware / RunState，不恢复旧 Runtime 分支。
- 文档只保留当前事实、稳定契约和明确待办；实验记录完成后应合并进 TODO 或删除。
