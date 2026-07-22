# 引力 YINLI 当前架构总览

> 状态：2026-07-20 生效。本文替代早期大而全设计稿，只记录当前仍在线的架构事实。

## 项目定位

引力 YINLI 是基于 AgentScope 2.0 + Electron + React 的本地优先办公 AI 工作台。用户在 Chat 中选择单个 Agent 或 Team 执行文档、表格、文件和项目任务，运行时支持 MCP、Skills、HTTP 工具、权限控制、HITL、Trace、容器沙箱和新记忆系统 MVP。

旧记忆模块、旧独立 Plan Runtime（`runtime.kind=plan`）和旧独立 Subagent Runtime（`runtime.kind=subagent`）已下线，不能继续扩展旧链路。这里删除的是独立 architecture / 路由分支，不是当前能力：Plan Mode 仍通过 middleware 接入单 Agent ReAct，Subagent 仍通过 `DelegateTask` 接入 ReAct。旧 Subagent 字段、配置入口、事件兼容分支和前端入口不得作为当前 API / UI / Runtime 事实源保留；历史 SQLite 开发库只允许保留启动所需的最小兼容。当前记忆能力是新方案：通过运行前上下文注入、运行后 `MemoryAdmissionGate` 准入、后台提炼、历史记忆（底层 `memory_nodes`）和 memory tools 接入 ReAct，不新增 `memory` runtime.kind。

新版 Subagent M1 和部分 M2 已作为 ReAct 内部委托能力上线：Subagent 定义来自 Markdown + YAML frontmatter 文件，主 Agent / Team Leader 通过 `DelegateTask` 前台委托 fresh-context 子 Agent，结果摘要回到主 Agent，完整过程进入 Trace 和 `subagent_invocations` 审计，不新增 `runtime.kind=subagent`。

## 技术栈

- 桌面壳：Electron
- 前端：React 19、TypeScript、Vite、Zustand、`@agentscope-ai/design`、`@agentscope-ai/chat`、Ant Design
- 后端：Python 3.11、FastAPI、AgentScope 2.0、SQLite
- 模型：DeepSeek 默认，运行时可切换 OpenAI、Anthropic、DashScope、Gemini、Ollama、自定义 OpenAI 兼容等供应商
- 执行环境：本地 workspace 或 AgentScope DockerWorkspace

## Chat 核心链路

1. `routes/chat/handlers/send.py` 接收用户消息，先按 `project_id` 解析 `RuntimeContext`，再落库、创建后台 Run 并编排 SSE。
2. `readiness_gate.py` 只做进入 Runtime 前的轻量启动判断；业务信息缺口交给主 Agent 在 ReAct 内通过 `AskUser` 外部工具暂停并恢复。
3. `intent_router.py` 只决定第一层 architecture。当前在线值为 `react`。
4. 请求中的显式 `mode` 决定第二层 executor：`agent` 为 `single_agent`，`team` 为 `team`。
5. `runtime_dispatcher.py` 只按 architecture 分发；当前只分发到 ReAct。
6. `react_runtime.py` 按 executor 执行单 Agent 或 Team。
7. `runtime_builder.py` 组装模型配置、Skill 包路径、ToolScope 和主 Agent runtime spec；Chat 请求不接收持久 Team 或固定成员上下文。
8. `run_context.py` 在 readiness 通过后按 Workspace、Project、Agent 和请求白名单裁剪可用 Skill catalog，并注入记忆上下文；完整 `SKILL.md` 不预注入 system prompt。
9. `leader_stream.py` 消费 AgentScope `reply_stream()`，转换文本、工具、权限、Plan Mode、Skill 和 HITL 事件。
10. `completion.py` 完成消息落库、Trace、Skill 状态和错误收尾，并在轻量 `MemoryAdmissionGate` 后触发 Markdown 记忆提炼和历史记忆持久化。
11. `background_runs.py` 将后台 Run 与前端 SSE 订阅解耦，并维护 RunState。

## Workspace / Project

当前归属关系固定为 `Workspace -> Project -> Session -> Run`：Workspace 是资源和策略边界，Project 是目录、上下文和运行数据边界。

- 启动时创建 `default` Workspace / Project，并回填历史资源和运行记录。
- Chat 请求只传 `project_id`；Workspace 必须由 Project 反查，现有 Session 不允许跨 Project 继续。
- `runtime_context.py` 在 Runtime 创建前解析 Project root、instructions 和 ownership。
- Project root 与 Agent filesystem scope 取交集；不相交时拒绝执行。
- TeamRun、Subagent invocation、Memory、Trace 和 RunState 继承父运行归属。
- Project resource binding 已作为运行时白名单参与 Agent、Skill、MCP Server 和 HTTP Tool 裁剪；资源必须属于 Project 所在 Workspace，Project 只能收窄 Agent 配置。
- 前端使用统一 Workspace / Project 上下文；Chat、Runs、Trace、Memory 和资源管理按当前上下文筛选，切换 Project 时清理 Chat 与 Team 运行投影。
- Workspace 与首个 Project 通过 bootstrap API 原子创建；正式 Artifact ownership 留在 Artifact Phase。

## AgentScope 原生 Skills

- 应用层继续负责安装、SQLite、Workspace / Project binding、Agent 配置、权限和 UI；AgentScope 负责运行时 catalog、渐进式披露和内置 `Skill` viewer。
- Toolkit 通过 `skills_or_loaders` 接收已裁剪的绝对 Skill 包目录；system prompt 只包含 `name`、`description` 和 `dir`，模型实际调用 `Skill` 后才读取完整指令。
- Skill 正文使用 `scripts/foo.py`、`references/bar.md` 等标准相对路径。模型根据 catalog 中的 `dir` 转成绝对路径后调用普通 Read、Grep、Glob 或 Bash；不使用 `$SKILL_DIR`，也不新增每脚本工具。
- Skill script 复用单一 `GuardedBash`；本地 execute 权限与 network 权限独立。Shell 黑名单仅是拦截明显误操作的提示级防护，不能抵抗命令混淆；真正的执行隔离依赖 DockerWorkspace 与权限审批。Skill 不突破 Agent 的 ToolScope、filesystem 或审批边界。
- 持久 DockerWorkspace 会把允许的 Skill 包 seed 到容器 `/workspace/skills`，catalog 披露容器路径；非持久 Docker sandbox 暂不支持 Skills，启动时返回明确错误。
- 单 Agent 和 Team Member 使用相同 Skill catalog；Team 协调、综合和恢复轮不开放业务 Skill。Subagent frontmatter `skills` 转换为裁剪后的 loader 路径，不再全文拼接。
- `SKILL_STARTED`、`SKILL_CONTEXT`、`SKILL_FAILED` 和 `SKILL_COMPLETED` 以真实 `Skill` viewer 工具调用为事实源。catalog 可见不创建 active `SkillRun`。
- zip 安装拒绝缺少 `name` / `description` 的 `SKILL.md`、路径穿越和符号链接；命名、目录名和未知 script 类型以 warning 展示。

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

- **运行前注入**：`memory/retrieval.py` 读取 user / agent / global Markdown 摘要和最近 30 天节点树；`run_context.py` 在 readiness 通过后注入主 Agent / Team Lead，隔离 Member session 使用本轮解析后的相同记忆上下文，并单独注册裁剪后的 AgentScope Skill catalog。
- **Subagent 注入**：`subagents/runtime_context.py` 把 frontmatter `skills` 指定的已安装启用 Skill 转换为 AgentScope loader 路径，不注入完整 Skill 正文；同时用 `subagent:{name}` 作为 agent memory owner 注入长期记忆摘要。`subagents/permission_policy.py` 执行 frontmatter `permissionMode` 并按父 Agent guardrails 上限收窄；`subagents/tool_policy.py` 基于父运行时真实工具池执行 `tools` / `disallowedTools` 裁剪，并同步限制传给子 Agent 的 MCP server 与知识库工具；审计通过 `SUBAGENT_CONTEXT` Runtime Event 记录 loadedSkills、missingSkillRefs、memoryAgentId、memoryInjected 和 permission requested / effective 摘要。
- **历史记忆**：底层 `memory_nodes` 只保存用户输入、Agent 最终输出和摘要索引，不保存工具调用过程；写入后同步到 `VectorStoreBackend` 的 `memory_nodes` collection。
- **Markdown 长期记忆**：user memory、agent memory、global preference memory 分别保存为 `user_memory.md`、`agent_memory.md`、`global_memory.md`，没有旧 `default.md` 兼容读取。
- **Memory 管理入口**：前端 Memory 页面首版已接入，用户记忆和全局记忆各只有一个管理入口；Agent 记忆按 Agent 选择，每个 Agent 对应一份 `agent_memory.md`；历史记忆按“Agent -> 时间节点 -> 具体记忆”三段式查看，底层使用 `memory_nodes`。页面已支持历史记忆禁用 / 删除 / 恢复、Skill 自进化队列操作、注册失败原因可视化、设置快照、召回质量摘要、召回命中相关性反馈和 `MEMORY_AUDIT` 审计详情。
- **Memory Wiki**：`MemoryWikiVaultSyncer` 可从三类 Markdown 记忆、`memory_nodes` 和已注册 Skills 手动生成 `backend/data/memory/wiki/` 只读 Obsidian-compatible 视图，并生成 `entities/` 实体页与 `entities/graph.md` 共现图；`/api/memory/wiki/graph` 提供结构化实体图谱给前端展示。Memory 页面可同步、查看图谱和生成结果、打开文件夹或通过 Obsidian 深链打开 vault。当前不作为运行时事实源，也不反向写回权威存储。
- **记忆查询工具**：普通 Agent 默认注入 `memory_node_tree` 和 `memory_node_query`，需要详情时由 Agent 主动查询；工具召回会写入 `MEMORY_AUDIT`，记录 query、filters、resultCount、命中 nodeId 和 retrievalQuality 质量指标。
- **运行后提炼**：`completion.py` 成功收尾后后台并行调用 `UserMemoryReflectionAgent`、`AgentMemoryReflectionAgent`、`GlobalMemoryReflectionAgent`，通过绑定文件的 `memory_read` / `memory_add` / `memory_update` / `memory_delete` 操作 Markdown 记忆，不阻塞主对话。
- **准入、记忆与召回质量**：`MemoryAdmissionGate` 当前是规则评分 + 可选模型评分增强，会输出 history / markdown / skill 三类评分、阈值、signals、模型评分、失败原因、脱敏摘要和脱敏类型统计；Memory 设置页可编辑准入阈值、模型评分开关和 Markdown 质量参数。Markdown 写入会做敏感信息过滤、近似去重、冲突合并、失败重试和 `# Summary` 压缩摘要；历史记忆查询会合并向量候选和 SQLite 关键词结果，再按查询词命中、向量分数、结构化反馈分、置顶、置信度、使用次数和日期做轻量重排，用户对命中 node 的相关 / 不相关反馈写入 `memory_retrieval_feedback` 表并同步写 `MEMORY_AUDIT` 追溯，Agent 工具查询会进入召回审计并带质量指标，运行前只注入 20 条摘要索引。
- **Skill 自进化现状**：已有 `SkillEvolutionAgent`、`skill_draft_propose` 草稿工具、router gate、workflow evidence、颗粒度 gate、变量流抽象、候选持久化、标准 Skill 注册、相似 Skill 合并和用户显式请求生成 Skill 的手动触发流；自动生成 / 手动注册 Skill 会写入运行时 `data/skill_packages/{skill_name}/SKILL.md` 并支持按 Skill 名称导出 zip。Memory 页面已能查看和操作候选队列；注册后不自动绑定 Agent。
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
- `workspaces`
- `projects`
- `project_resource_bindings`
- `subagent_invocations`

历史开发库可能仍有 `memory_facts`、`memory_events`、`memory_candidates`，但旧记忆模块已删除，不再代表当前运行时能力。

桌面安装版把 Electron `userData` 固定为 `agent-workstation-desktop`。`config/runtime_paths.py` 是所有运行时可写路径的唯一入口；SQLite、Markdown / Wiki Memory、向量库、TeamRun、Skill 包、用户级 Subagent、持久沙盒、日志和默认 Workspace 都必须位于该根目录。macOS 对应 `~/Library/Application Support/agent-workstation-desktop/`，Windows 对应 `%APPDATA%\\agent-workstation-desktop\\`。安装目录和 PyInstaller `_MEI...` 临时目录只允许读取代码资源，不能保存用户数据。

## 配置与权限

- 启动期静态配置在 `config/settings.py`，环境变量前缀为 `AGENT_WORKSTATION_`。
- 运行时模型设置在 `config/runtime_settings.py`，非敏感配置持久化到 `json_store`；模型 API Key 不进入 SQLite，macOS 使用 Keychain，Windows 使用当前用户 DPAPI 加密并将密文保存在统一 `userData/data/secrets/` 目录。旧版 SQLite 明文 Key 会在安全写入成功后自动迁移并删除原字段。
- Agent 运行时由 `agents/factory.py` 统一创建和缓存。
- 工具范围由 `RuntimeToolScope` 与 `tool_scope_resolver.py` 控制。
- 权限策略由 guardrails 归一化为统一权限模型，再映射到 AgentScope permission mode、工具注入和 Trace。
- `WebFetch` 是独立的只读公开网页工具：只接收已知 HTTP(S) URL，逐次校验重定向目标，拒绝私网、本机、保留地址、URL 凭据、超大响应和非文本内容，并把网页正文标记为不可信外部数据。它不复用 `HttpRequest` 的通用 API 语义，也不提供 WebSearch。

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
