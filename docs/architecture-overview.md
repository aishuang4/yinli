# Agent Workstation 当前架构总览

> 状态：2026-07-14 生效。本文替代早期大而全设计稿，只记录当前仍在线的架构事实。

## 项目定位

Agent Workstation 是基于 AgentScope 2.0 + Electron + React 的桌面多 Agent 协作工作台。用户在 Chat 中选择单个 Agent 或 Team 执行任务，运行时支持 MCP、Skills、HTTP 工具、权限控制、HITL、Trace、容器沙箱和新记忆系统 MVP。

旧记忆模块、旧 Plan Runtime、旧 Subagent Runtime 已下线，不能继续扩展旧链路。旧 Subagent 字段、配置入口、事件兼容分支和前端入口不得作为当前 API / UI / Runtime 事实源保留；历史 SQLite 开发库只允许保留启动所需的最小兼容。当前记忆能力是新方案：通过运行前上下文注入、运行后 `MemoryAdmissionGate` 准入、后台提炼、历史记忆（底层 `memory_nodes`）和 memory tools 接入 ReAct，不新增 `memory` runtime.kind。

新版 Subagent M1 和部分 M2 已作为 ReAct 内部委托能力上线：Subagent 定义来自 Markdown + YAML frontmatter 文件，主 Agent / Team Leader 通过 `DelegateTask` 前台委托 fresh-context 子 Agent，结果摘要回到主 Agent，完整过程进入 Trace 和 `subagent_invocations` 审计，不新增 `runtime.kind=subagent`。

## 技术栈

- 桌面壳：Electron
- 前端：React 19、TypeScript、Vite、Zustand、`@agentscope-ai/design`、`@agentscope-ai/chat`、Ant Design
- 后端：Python 3.11、FastAPI、AgentScope 2.0、SQLite
- 模型：DeepSeek 默认，运行时可切换 OpenAI、Anthropic、DashScope、Gemini、Ollama、自定义 OpenAI 兼容等供应商
- 执行环境：本地 workspace 或 AgentScope DockerWorkspace

## Chat 核心链路

1. `routes/chat/handlers/send.py` 接收用户消息、落库、创建后台 Run，并编排 SSE。
2. `readiness_gate.py` 只做进入 Runtime 前的轻量启动判断；业务信息缺口交给主 Agent 在 ReAct 内通过 `AskUser` 外部工具暂停并恢复。
3. `intent_router.py` 只决定第一层 architecture。当前在线值为 `react`。
4. 请求中的显式 `mode` 决定第二层 executor：`agent` 为 `single_agent`，`team` 为 `team`。
5. `runtime_dispatcher.py` 只按 architecture 分发；当前只分发到 ReAct。
6. `react_runtime.py` 按 executor 执行单 Agent 或 Team。
7. `runtime_builder.py` 组装模型配置、Skill、ToolScope 和主 Agent runtime spec；Chat 请求不接收持久 Team 或固定成员上下文。
8. `run_context.py` 在 readiness 通过后注入 Skill prompt 和记忆上下文；记忆注入位于 Skill prompt 后、AskUser clarification / final answer 规则前。
9. `leader_stream.py` 消费 AgentScope `reply_stream()`，转换文本、工具、权限、Plan Mode、Skill 和 HITL 事件。
10. `completion.py` 完成消息落库、Trace、Skill 状态和错误收尾，并在轻量 `MemoryAdmissionGate` 后触发 Markdown 记忆提炼和历史记忆持久化。
11. `background_runs.py` 将后台 Run 与前端 SSE 订阅解耦，并维护 RunState。

## Team Runtime

Team Runtime 是一次性 `TeamRun`。用户在单 Agent 模式选择 Agent，切换到 Team 模式后沿用该 Agent 作为 Leader；Team 模式不显示 Team、Member 或 Leader 下拉框，也不读取持久 Team 配置。每轮运行在 `backend/data/team-runtime/runs/{team_run_id}/` 下生成全新 file-first 状态。

核心语义：

- Team Lead 是主协调会话，负责动态生成临时队友、拆任务和协调 Mailbox；最终答案由独立综合阶段生成。
- Teammate 是隔离 AgentScope Agent 会话，拥有独立上下文窗口，不继承 Lead 对话历史。
- 共享事实只允许通过 Task List、Mailbox、member directory、result artifacts 和 events/snapshots 表达。
- 用户可以向任意 Member 发送 mailbox 消息，作为本轮运行中的直接干预。
- Member 提交结果前必须清空未读 Mailbox；综合阶段使用完整 Mailbox，综合期间的新消息会触发重新综合。
- Team 只使用顶层 `TEAM_EVENT`；旧事件、旧历史投影和静态成员选择链路不读取、不兼容。

## RunState 与前端主展示

Chat 主界面只依赖 RunState / DisplaySnapshot：

- `/api/chat/send/start` 创建后台 Run。
- `/api/chat/resume/start` 恢复 HITL / AskUser 外部执行暂停的原 Run。
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

- **运行前注入**：`memory/retrieval.py` 读取 user / agent / global Markdown 摘要和最近 30 天节点树；`run_context.py` 在 readiness 通过后注入主 Agent / Team Lead，隔离 Member session 使用本轮解析后的相同记忆与 Skill 上下文。
- **Subagent 注入**：`subagents/runtime_context.py` 为子 Agent 注入 frontmatter `skills` 指定的已安装启用 Skill，并用 `subagent:{name}` 作为 agent memory owner 注入长期记忆摘要；`subagents/permission_policy.py` 执行 frontmatter `permissionMode` 并按父 Agent guardrails 上限收窄；`subagents/tool_policy.py` 基于父运行时真实工具池执行 `tools` / `disallowedTools` 裁剪，并同步限制传给子 Agent 的 MCP server 与知识库工具；审计通过 `SUBAGENT_CONTEXT` Runtime Event 记录 loadedSkills、missingSkillRefs、memoryAgentId、memoryInjected 和 permission requested / effective 摘要。
- **历史记忆**：底层 `memory_nodes` 只保存用户输入、Agent 最终输出和摘要索引，不保存工具调用过程；写入后同步到 `VectorStoreBackend` 的 `memory_nodes` collection。
- **Markdown 长期记忆**：user memory、agent memory、global preference memory 分别保存为 `user_memory.md`、`agent_memory.md`、`global_memory.md`，没有旧 `default.md` 兼容读取。
- **Memory 管理入口**：前端 Memory 页面首版已接入，用户记忆和全局记忆各只有一个管理入口；Agent 记忆按 Agent 选择，每个 Agent 对应一份 `agent_memory.md`；历史记忆按“Agent -> 时间节点 -> 具体记忆”三段式查看，底层使用 `memory_nodes`。页面已支持历史记忆禁用 / 删除 / 恢复、Skill 自进化队列操作、注册失败原因可视化、设置快照、召回质量摘要、召回命中相关性反馈和 `MEMORY_AUDIT` 审计详情。
- **Memory Wiki**：`MemoryWikiVaultSyncer` 可从三类 Markdown 记忆、`memory_nodes` 和已注册 Skills 手动生成 `backend/data/memory/wiki/` 只读 Obsidian-compatible 视图，并生成 `entities/` 实体页与 `entities/graph.md` 共现图；`/api/memory/wiki/graph` 提供结构化实体图谱给前端展示。Memory 页面可同步、查看图谱和生成结果、打开文件夹或通过 Obsidian 深链打开 vault。当前不作为运行时事实源，也不反向写回权威存储。
- **记忆查询工具**：普通 Agent 默认注入 `memory_node_tree` 和 `memory_node_query`，需要详情时由 Agent 主动查询；工具召回会写入 `MEMORY_AUDIT`，记录 query、filters、resultCount、命中 nodeId 和 retrievalQuality 质量指标。
- **运行后提炼**：`completion.py` 成功收尾后后台并行调用 `UserMemoryReflectionAgent`、`AgentMemoryReflectionAgent`、`GlobalMemoryReflectionAgent`，通过绑定文件的 `memory_read` / `memory_add` / `memory_update` / `memory_delete` 操作 Markdown 记忆，不阻塞主对话。
- **准入、记忆与召回质量**：`MemoryAdmissionGate` 当前是规则评分 + 可选模型评分增强，会输出 history / markdown / skill 三类评分、阈值、signals、模型评分、失败原因、脱敏摘要和脱敏类型统计；Memory 设置页可编辑准入阈值、模型评分开关和 Markdown 质量参数。Markdown 写入会做敏感信息过滤、近似去重、冲突合并、失败重试和 `# Summary` 压缩摘要；历史记忆查询会合并向量候选和 SQLite 关键词结果，再按查询词命中、向量分数、结构化反馈分、置顶、置信度、使用次数和日期做轻量重排，用户对命中 node 的相关 / 不相关反馈写入 `memory_retrieval_feedback` 表并同步写 `MEMORY_AUDIT` 追溯，Agent 工具查询会进入召回审计并带质量指标，运行前只注入 20 条摘要索引。
- **Skill 自进化现状**：已有 `SkillEvolutionAgent`、`skill_draft_propose` 草稿工具、router gate、workflow evidence、颗粒度 gate、变量流抽象、候选持久化、标准 Skill 注册、相似 Skill 合并和用户显式请求生成 Skill 的手动触发流；自动生成 / 手动注册 Skill 会写入 `backend/data/skill_packages/{skill_name}/SKILL.md` 并支持按 Skill 名称导出 zip。Memory 页面已能查看和操作候选队列；注册后不自动绑定 Agent。
- **向量存储**：全局 `VectorStoreBackend` 抽象已支持 `disabled` / `zvec`，`VECTOR_STORE_PROVIDER` 仅首次初始化生效，之后按持久化 `VectorStorePolicy` 启动。

## 存储

`backend/src/storage/database.py` 使用同步 `sqlite3 + RLock`。核心结构化表包括：

- `agents`
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
