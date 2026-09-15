# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository.

## 项目概述

引力 YINLI 是基于 AgentScope 2.0 + Electron + React 的本地优先办公 AI 工作台。用户在 Chat 界面选择单个 Agent 或 Team 协作完成文档、表格、文件和项目任务，支持 MCP 工具、Skills 技能、HTTP 知识库工具、权限控制、容器沙箱执行和新记忆系统 MVP。旧记忆模块已删除，当前记忆能力按新方案通过 hooks / tools / 后处理服务接入，不恢复旧 memory runtime。

技术栈：Electron 壳 + React 19/TypeScript/Vite 前端 + Python/FastAPI/AgentScope 2.0 后端 + SQLite 存储。模型默认接 DeepSeek，运行时可切换至 OpenAI、Anthropic、DashScope、Gemini、Ollama、自定义 OpenAI 兼容等十余个供应商。

## 常用命令

```bash
# 一键启动前后端开发服务（后端 8765，前端 5173）
./scripts/dev.sh

# 后端（在 backend/ 目录）
uv sync                                                          # 安装依赖
uv run python -m uvicorn src.server.main:app --reload --port 8765  # 启动 API
uv run pytest                                                    # 运行全部测试
uv run pytest test/test_secret_store.py::test_memory_secret_store_round_trip  # 运行单个测试

# 前端（在 frontend/ 目录）
npm install
npm run dev      # Vite dev server
npm run electron:dev      # 开发模式启动 Electron 桌面窗口
npm run electron:compile  # 编译 Electron 主进程和 preload
npm run electron:pack     # 当前平台 Electron 内测打包
npm run electron:build:mac  # 生成 macOS 内测包
npm run electron:build:win  # 生成 Windows 内测包
npm run build    # tsc -b && vite build
npm run lint     # eslint
npm test         # vitest 单测

# 后端桌面端可执行文件（在目标平台分别执行）
./scripts/build-backend-desktop.sh
```

首次运行需 `cp backend/.env.example backend/.env` 并填入 `AGENT_WORKSTATION_DEEPSEEK_API_KEY`。环境要求：Node >= 20（dev.sh 优先用 nvm 切到 24）、Python >= 3.11、uv。

## 架构要点

### 后端请求流（核心链路）

`backend/src/server/main.py` 注册所有路由，前缀均为 `/api/*`。关键路径是对话流：

1. **`routes/chat/handlers/send.py`** — `POST /api/chat/send` 是 Chat SSE 统一入口，只负责请求读取、用户消息写入、后台 Run 启动、前置错误处理和 SSE 编排。已删除的是旧独立 Plan Runtime（`runtime.kind=plan`）和旧独立 Subagent Runtime（`runtime.kind=subagent`）；当前 Plan Mode 通过 middleware 接入单 Agent ReAct，当前 Subagent 由 `DelegateTask` fork attached 文件任务，通过 wait/read 工具消费 `result.md`，全部 child 终态且 consumed 后父 Run 才能结束。后续不要在旧路由、旧字段或旧兼容事件上继续补丁式开发。

2. **`routes/chat/readiness_gate.py`** — Chat 前置信息充足性判断。系统内置 `ReadinessAgent` 模型优先、规则兜底，只判断当前输入是否可以启动 Runtime；业务信息缺口交给主 Agent 在 ReAct 内通过 `AskUser` 外部工具暂停并恢复。ReadinessAgent 不选择架构、不选择 Team、不选择 Skill、不决定 ToolScope。

3. **`routes/chat/intent_router.py`** — Chat 第一层架构路由。仅在 readiness 通过后运行，模型语义路由优先、规则路由兜底；当前只决定本轮进入 `react`，旧 `runtime.kind=plan` 与 `runtime.kind=subagent` 已删除，不再作为路由结果。Plan Mode 和 Subagent 委托都是 ReAct 内部增强，不是 architecture。`clarification` 不是 runtime.kind，也不是新的运行时分支。它不选择 Team、不选择 Member、不选择 Skill、不决定 ToolScope。第二层 `executor` 由请求的显式 `mode` 机械决定：`mode=agent` 为 `single_agent`，`mode=team` 为 `team`。Team 模式仍选择一个 Agent 作为本轮 Leader，不选择持久 Team。路由结果写入 `INTENT_ROUTE` trace。

4. **`routes/chat/runtime_dispatcher.py` / `routes/chat/react_runtime.py`** — Chat Runtime 分发与 ReAct 执行入口。`runtime_dispatcher.execute_chat_runtime()` 只按第一层 `runtime.kind` 分发；当前仅 `react` 在线，信息不足时由主 Agent 在 Runtime 内调用 `AskUser`，通过 AgentScope `RequireExternalExecutionEvent` 暂停。`react_runtime.execute_react_runtime()` 再按第二层 `executor.kind` 执行：`single_agent` 直接运行主 Agent，`team` 委托全新的 `TeamRunService`，由 Leader 动态组建临时团队、Member 隔离执行、Mailbox 协作并独立综合最终答案。

5. **`routes/chat/runtime_builder.py`** — IntentRoute 之后的运行时组装层。负责合成本轮模型配置、主 Agent runtime cache key、`MainAgentRuntimeSpec`，并通过 `create_main_agent_from_spec()` 委托工厂创建/复用 Agent。Chat 请求不包含持久 Team 或固定成员上下文。Skill 选择由 `skill_selector.py` 处理；工具范围由 `tool_scope_resolver.py` 处理。

6. **`routes/chat/team_runtime/`** — 一次性 Team Runtime。每轮由 `TeamRunService` 创建全新文件状态，Leader 使用 Team 控制工具动态创建临时 Member 和共享任务，`TeamCoordinator` 按依赖并发运行隔离 Member session，全部任务成功后进入无工具综合阶段。Team 文件状态和 `events.jsonl` 是领域事实源；展示通过内部 `TEAM_DOMAIN_EVENT` 归一化为 Domain Event，再统一投影为 `RUNTIME_EVENT` 和 `DisplaySnapshot V4` 中的 `team/team-member/team-task/team-activity/team-mail` Block，不得恢复顶层 `TEAM_EVENT`、独立 `teamSnapshot` 或 `TeamConsoleState`。用户可通过 active TeamRun Mailbox API 向任意成员投递干预消息；Member 存在未读消息时不得提交结果，综合期间收到新消息必须重新综合。
   Chat 与 Sandbox 在单 Agent 模式选择 Agent；切换到 Team 模式后沿用该 Agent 作为 Leader，并隐藏选择器。禁止恢复 Team、Member 或 Leader 下拉框，也禁止向 Chat Runtime 传入 `team_id`、`team_name`、`team_members`。

7. **`routes/chat/leader_stream.py`** — Leader/单 Agent 事件流处理。消费 `agent.reply_stream()`，维护 `LeaderStreamState`，处理模型 cache、trace、permission decision、tool artifacts、HITL 早停、流式文本/思考累计、Skill end 和模型输出后的 clarification 兜底。

8. **`routes/chat/completion.py`** — 单 Agent/综合结果成功或失败收尾。负责 Skill 状态、assistant message、trace 和 `ERROR` 等 SSE。Team 领域状态由 `team_runtime` 自己收口，不经 completion 投影。成功运行后会触发新记忆后处理：后台并行提炼 user / agent / global Markdown 记忆，调用 SkillEvolutionAgent 生成 Skill 候选，并把本轮用户输入和 Agent 最终输出持久化为历史记忆（底层 `memory_nodes`）。

9. **`runtime_context.py` / `project_resources.py`** — Workspace / Project Phase 1 + Phase 2 的统一运行归属和资源白名单。Chat 请求只传 `project_id`，后端由 Project 反查 Workspace；现有 Session 不得跨 Project 继续。`RuntimeContext` 在 Agent 创建前解析，Project root 与 Agent filesystem scope 取交集，并传递给 TeamRun、Subagent、Memory、Trace 和 RunState。Project binding 已按 Workspace 归属裁剪持久 Agent、Skill、MCP 和 HTTP Tool；Agent 配置仍是能力上限。Phase 3 已提供统一前端 Workspace / Project 上下文，Chat、Runs、Trace、Memory 和资源管理按当前上下文隔离；Artifact ownership 仍留在 Artifact Phase。

10. **`agents/factory.py`** — 唯一的 AgentScope 运行时构造入口。`get_or_create_agent()` 按 runtime `agent_id` 缓存实例（缓存键不含 system prompt 正文，复用时刷新 `_system_prompt`；同一 `session_context_key` 换实例时由 `agents/session_context.py` 搬运 AgentScope `state.context`，LRU 48 / 闲置 6h 淘汰，忙碌或等待用户回应的实例不淘汰）；会话历史只存在于 AgentScope `state.context`，进程重启后由 `chat_messages` 重建，禁止再把历史扁平拼进 system prompt；时间由 `InjectionConfig` HintBlock 注入，system prompt 不得写时钟；模型读过的 SKILL.md 正文由 `routes/chat/skill_context_middleware.py` 保留在会话里，截断 / 压缩 / 重建后以 `<activated-skill>` HintBlock 补回，不预注入 system prompt，也不因 `SkillRun` 每轮关闭而丢失；`_create_model()` 按 `adapter` 字段分发到不同 ChatModel 类，`context_size` 取 `ModelEntry.contextSize`（0 = adapter 默认表），不得回退到 AgentScope 默认 32768；`FallbackChainModel` 是应用层多模型降级链（主模型失败逐级回退并发 `attempt`/`success`/`failure` 事件，`context_size` 取链上最小）。上下文治理在 `routes/chat/context_middleware.py`：`ContextHygieneMiddleware` 每轮开始把早于最近 2 轮的大段工具输出 / 旧图片存根化（不动 `Skill` 正文），`ContextCompressionMiddleware` 记录 AgentScope 真正压缩事件并写 `CONTEXT_COMPRESSED` trace；`ContextConfig` 为 trigger 0.75 / reserve 0.2 / tool_result_limit 4000，summary schema 只声明模板渲染的五段且 `additionalProperties=True`。`state.summary` + `middle_context` 每轮写入 SQLite `session_context_state`，实例重建时恢复。`CONTEXT_USAGE` 的 `estimatedTokens` 必须与 AgentScope `count_tokens` 同源（bytes/4 含工具 schema），不要再引入第二套字符折算。工具集在 `_create_default_toolkit()` 注入：普通 Agent 默认拥有 Read/Grep/Glob/AskUser/FinalAnswerStart，Write/Edit 受权限控制；Bash/HttpRequest/WebFetch/知识库工具/MCP 受 `RuntimeToolScope` 裁剪。`WebFetch` 只读取已知公开 URL，不提供 WebSearch，必须保持私网地址、URL 凭据、重定向、响应大小和非文本内容防护。系统基础设施 Agent 可通过 `include_default_tools=False` 创建完全无工具实例。`GuardedBash` 强制执行 shell 黑名单和超时。执行环境为 `LocalWorkspace`（默认工作目录是项目根目录，避免 Glob/Grep 扫描用户主目录）或 `DockerWorkspace`。改 Agent 配置后必须调 `invalidate_agent()` 让缓存失效。

11. **`agents/agentscope_usage_patch.py`** — 启动时 `patch_agentscope_usage_events()` 给 AgentScope 打补丁以捕获 usage 事件。

12. **群聊工作室（Group Chat Studio）** — 独立于 Chat Team Runtime 的跨多轮协作产品，不替换、不增强一次性 `TeamRun`。入口是 Chat 顶栏按钮 / `⌘⇧G`，520ms 重叠过渡切到独立 pane，不是 Agent/Team 分段，也不是 TitleBar 第三 workspace。领域包 `backend/src/group_studio/`，路由 `/api/group-studio`，独立 SSE（`docs/group-studio-event-contract.md`，`GROUP_*` 事件；DELTA 不落库）。SQLite `group_*` 表是领域事实源；一 Chat session 对应一间未归档工作室；一群一张任务卡。Host 哨兵 `group-studio-host`（`include_default_tools=False`、控场工具 allowlist、`PermissionMode.BYPASS`）；选中 Chat Agent 是第一 Worker。讨论与执行全串行，`maxParallelWorkers=1`。Orchestrator / 黑板记录员是系统服务，不进群。群内 Host/Worker 工具权限由工作室自有：Worker 同样 `human_approval=False` + `trusted_tools` → `PermissionMode.BYPASS`，不抄 Chat Agent profile；`group_approvals` 仅兜底，禁止写入 Chat `PENDING_INTERACTIONS`。通知走 `src/notifications/`，kind 前缀 `group_studio_`，点击 `openGroupStudio`，不进 Chat SSE / ResultModal。`require_agent` 跳过 `default` 与 `group-studio-host`。Chat handlers / Team Runtime 禁止 import `group_studio`；catalog 通过 `session_studio.py` 附加徽章。开关在 `json_store system_settings/group_studio`，默认 `enabled=true`。`drafting` 是 intake 对谈：只允许人类 ↔ Host，写卡不切 `discussing`，from-chat 上一句是开场白不是 Goal，离开 drafting 只有人类确认（`POST /confirm-intake` 或确认短语）。`group_assign_floor` 支持 `memberIds[]` FIFO 队列，工人结束弹下一人中间不回 Host。工作项表 `group_work_items` + 原子认领 + 串行认领轮已落地。真并行仍是 P1。阶段机详见 `docs/architecture-overview.md`。

### 持久化

`backend/src/storage/database.py` 用同步 `sqlite3`（线程锁 `RLock`）封装所有存储，尽管依赖里有 `aiosqlite`。两类存储模式：

- **结构化表**：`workspaces`、`projects`、`project_resource_bindings`、`agents`（第一期后用户正文已迁出，表可为空）、`mcp_servers`、`skills`、`chat_sessions`、`chat_messages`、`chat_runs`、`run_events`、`session_context_state`（会话级压缩摘要 + middleware 跨轮数据）、`memory_nodes`、`subagent_invocations`、`group_studios` / `group_task_cards` / `group_blackboards` / `group_memberships` / `group_messages` / `group_runs` / `group_approvals` / `group_artifacts` / `group_work_items` / `group_studio_events`。启动时创建稳定的 `default` Workspace / Project 并回填历史空归属。持久 Team 的 `teams` / `team_members` 表在启动迁移中删除；历史库中可能仍残留 `memory_facts` / `memory_events` / `memory_candidates` 表，但旧记忆模块已删除，不再作为当前运行时能力。群聊工作室不恢复持久 `teams` 表。
- **Markdown 记忆文件**：新记忆 MVP 使用 `backend/data/memory/users/user_memory.md`、`backend/data/memory/agents/agent_memory.md`、`backend/data/memory/global/global_memory.md` 作为默认文件；非默认 owner 使用 `users/{user_id}/user_memory.md`、`agents/{agent_id}/agent_memory.md`、`global/{workspace_id}/global_memory.md`。不兼容读取旧 `default.md`。
- **向量存储**：全局 `VectorStoreBackend` 抽象支持 `disabled` 和 `zvec` provider。`VECTOR_STORE_PROVIDER` 只在首次初始化时生效，之后读取持久化 `VectorStorePolicy`；向量库只索引历史记忆底层表 `memory_nodes`，Markdown 记忆不进入向量库。
- **`json_store` 表**：`(namespace, key) -> JSON`，用于系统设置等 Pydantic 文档。模型配置已迁移到 `USER_DATA_ROOT/models.json`；旧 `system_settings/model` 仅在首次迁移时读取。

Schema 演进用 `_ensure_column()` / `ALTER TABLE` 原地升级开发库，不用迁移框架。早期 JSON 文件（如 `data/chat_sessions.json`）通过 `migrate_json_*_file()` 一次性导入 SQLite。

### 配置分层

- `config/settings.py` — `pydantic-settings`，环境变量前缀 `AGENT_WORKSTATION_`，从 `.env` 加载。属启动期静态配置（端口、DB URL、CORS、默认 DeepSeek key）。
- `config/model_catalog.py` — `models.json` 唯一配置源，供应商实例与模型条目使用独立 ID；Key 按用户要求保存在本地文件，API 只返回已配置标记。保存采用进程锁、内容版本校验和原子替换。`config/runtime_settings.py` 在新 Run 开始时解析完整连接，并用不可变快照贯穿 Team、Subagent、审批恢复和后台后处理。Chat/Sandbox 选择模型；Agent 只保留 ReAct 上限，不选择模型或采样参数。见 `docs/chat-model-selection-v1.md`。

### 前端

- `frontend/src/App.tsx` — 用 `appStore.currentView` 通过 `VIEW_PAGES` 映射表（非 switch）懒加载页面；主题用 `useLayoutEffect` 同步到 `data-theme`；每 10s 轮询 `/api/health`。
- 状态用 Zustand（`stores/appStore.ts`、`stores/settingsStore.ts`）。
- API 调用与 SSE 事件流封装在 `hooks/`（`useEventStream.ts` 等）。页面在 `pages/`，对应后端各路由域（Chat/Trace/Sandbox/Agents/Subagents/MCP/Skills/Settings）；群聊工作室是 Chat 上的独立 pane（`features/group-studio/`），不是 TitleBar 第三 workspace。消息区滚到底部按钮与滚动钩子在 `components/ScrollToBottomButton.tsx` / `components/useMessageScroll.ts`，Chat 与群聊共用。
- Vite 把 `/api` 代理到 `localhost:8765`；`@/` 别名指向 `src/`。
- UI 库为 `@agentscope-ai/design` + `@agentscope-ai/chat` + Ant Design，主题用 carbonTheme/carbonDarkTheme。

### Electron

`electron/main.ts` 开发模式连接 Vite dev server，生产模式加载 `frontend/dist` 并 spawn Python 后端子进程。`contextIsolation: true` + `nodeIntegration: false`，通过 `preload.ts` 桥接 IPC。

## 约定

- 开发必须遵循 `docs/development-guidelines.md`。该文件是正式开发规范，覆盖模块化、React/TypeScript、Python/FastAPI、SQLite、Agent Runtime、测试、Git 和文档要求。
- 如果 `AGENTS.md` / `CLAUDE.md` 与 `docs/development-guidelines.md` 冲突，以更具体、更严格、更新的规范为准；不确定时先按 `docs/development-guidelines.md` 执行。
- `AGENTS.md` 是开发代理记忆和项目协作规范的唯一真实来源；`CLAUDE.md` 只作为兼容入口，避免多份记忆漂移。
- 当前已删除/下线的旧设计：旧记忆模块、旧独立 Plan Runtime（`runtime.kind=plan`）、旧独立 Subagent Runtime（`runtime.kind=subagent`）。当前在线能力是 ReAct 内的 Plan middleware 和 attached `DelegateTask` Subagent 文件任务；不得把“删除旧独立 Runtime”误解为删除这两项当前能力。Subagent definition 继续来自 YAML/Markdown，`DelegateTask` 只 fork，父 Agent 通过 `SubagentTaskWait` / `SubagentResultRead` join 和消费。后续不要在旧字段、旧事件或旧工具链上继续补丁式开发；记忆能力只能沿当前新方案推进，通过 hooks / tools / 后处理服务接入，不新增 `memory` Runtime 分支。
- 新记忆系统当前状态：运行前在 readiness 通过后给主 Agent / Team Leader / 临时 Member session 注入 user / agent / global Markdown 摘要和最近 30 天历史记忆索引；普通 Agent 默认有 `memory_node_tree` / `memory_node_query`，查询会合并向量候选与 SQLite 关键词结果，按查询词命中、向量分数、结构化反馈分、置顶、置信度、使用次数和日期轻量重排，并写入 `MEMORY_AUDIT`，记录 query、filters、resultCount、命中 nodeId 和 retrievalQuality 质量指标；召回命中相关性反馈写入 `memory_retrieval_feedback` 表并同步写 `MEMORY_AUDIT` 追溯。运行后先经过 `MemoryAdmissionGate`，再后台提炼 Markdown 记忆并持久化历史记忆。`MemoryAdmissionGate` 已支持规则评分、可选模型评分、持久化阈值、可编辑设置、审计 payload、脱敏摘要和脱敏类型统计；Markdown 写入已支持近似去重、敏感信息过滤、冲突合并、失败重试和 `# Summary` 压缩摘要。Skill 自进化已有 router gate、workflow evidence、颗粒度控制、变量流抽象、候选持久化、标准 Skill 注册、相似 Skill 合并和手动触发流；Memory Wiki 同步器可手动生成 `backend/data/memory/wiki/` 只读 Obsidian-compatible 视图，并生成 `entities/` 实体索引和 `entities/graph.md` 共现图；Memory 页面已支持 Markdown 记忆、历史记忆禁用 / 删除 / 恢复、Skill 自进化队列操作、注册失败原因可视化、Wiki 同步入口、设置区和 MEMORY_AUDIT 审计详情。
- 在本项目 AgentScope 语境中，`middleware` 按 AgentScope hooks / `MiddlewareBase` 理解；讨论 Plan Mode 等运行时增强时，优先考虑通过 middleware hooks（如 `on_system_prompt`、`on_acting`）接入，而不是恢复旧 Runtime 分支。
- 推进 Plan Mode 任务进度能力时，固定顺序为：先核对 SQLite/Pydantic/前端 TypeScript 数据结构，再补 `todo_write` / `task_output` / `task_list` 等工具与 Runtime Event，最后和前端流式展示、审批入口、刷新恢复一起联调。
- 代码注释和文档用中文，技术术语和标识符保持英文。
- TODO 文档和待办事项统一用中文编写，便于团队协作和后续交接。
- Chat SSE 事件新增或变更前必须先更新 `docs/chat-sse-event-contract.md`，前端只依赖该契约中的稳定事件。
- 文件均带中文 docstring 说明职责，遵循既有风格。
- 前端遵循若干约定（见 `App.tsx` 注释）：派生状态不用 effect、对象映射表代替 switch、初始化只做一次。
- 文档入口见 `docs/README.md`；当前架构见 `docs/architecture-overview.md`；文件 catalog 见 `docs/file-catalog-v1.md`；记忆模块重设计见 `docs/memory-system-design.md`；通知通道见 `docs/notifications.md`；项目现状与待办见 `docs/project-progress-handoff.md` 和 `docs/project-todo.md`；开发规范见 `docs/development-guidelines.md`。
- 持久 Agent 定义权威是 `<USER_DATA_ROOT>/workspaces/<workspace_id>/.agents/profiles/<id>.md`。`get_registered_agent` 读 catalog；禁止再把用户 Agent 正文 upsert 进 sqlite。Skill 正文只来自 `SKILL.md`；项目 `.agents/skills` 免 Project binding，但和系统包 `word-documents` / `yinli-charts` 一样必须出现在 Agent 配置里才注入 Runtime，只有 `yinli-catalog` 说明书无条件注入。MCP/HTTP 定义是 `.agents/mcp/*.yaml` 与 `.agents/tools/*.yaml`，密钥只引用 `secret:`，不进 YAML。Job 定义是 `.agents/jobs/<id>.md`（Workspace catalog 或项目目录）；`scheduled_jobs` 表只保留运行实例。模型只通过文件工具改这些定义，不新增系统 CRUD 工具，也不再向单 Agent 注入 `ScheduleJob` 平行入口。
- 消息通知走 `src/notifications/` 通道抽象：定时任务终态由 `scheduler/notifications.py` 适配后交给分发器。当前只实现应用内 inbox（Chat 顶栏设置左侧铃铛 + `/api/notifications/stream`）。任务完成时 Web 弹出右上角提醒框，Electron 客户端走系统通知。邮箱 / 微信 / 飞书是占位通道，不得假装已投递。禁止把通知写入 Chat SSE。`kind` 以 `group_studio_` 开头的通知点击后 `openGroupStudio`，不进 ResultModal。
- 群聊工作室是独立产品，不是 Chat Team Runtime 的第三 mode。后续不要把工作室能力补丁回 `POST /api/chat/send`、`RUNTIME_EVENT` 或持久 `teams` 表。阶段机、intake、FIFO 队列和工作项认领以 `docs/architecture-overview.md` 为准，事件字段以 `docs/group-studio-event-contract.md` 为准。真并行 Worker、时间旅行快照、群事件进 Trace 页等只写入 `docs/project-todo.md`。
- 定时任务执行者必须是 Job Agent（`role=job`），不能在 Chat 里选用。Job Agent 有必注入 / 可选注入两层工具；`CheckJobFeasibility` 只认领可选层，必注入到期一定带上。系统 `default` 助手不能改成 Job Agent。系统内置 `system-websearch-job`（名称 `WebSearch`）作为跨 Workspace 可用的默认 Job Agent，必注入 `WebFetch` / `HttpRequest`，不可删除或改为 Chat Agent；只豁免 Agent 自身的 Project binding，外部 Skill / MCP / HTTP Tool 仍按 Project 白名单裁剪。`WebFetch` 只读取已知公开 URL，不等同于真正的 WebSearch。
