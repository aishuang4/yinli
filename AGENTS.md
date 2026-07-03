# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository.

## 项目概述

Agent Workstation 是基于 AgentScope 2.0 + Electron + React 的桌面多 Agent 协作工作台。用户在 Chat 界面选择单个 Agent 或 Team 协作完成任务，支持 MCP 工具、Skills 技能、HTTP 知识库工具、权限控制与容器沙箱执行。旧记忆模块已删除，后续需要重新设计后再接入。

技术栈：Electron 壳 + React 19/TypeScript/Vite 前端 + Python/FastAPI/AgentScope 2.0 后端 + SQLite 存储。模型默认接 DeepSeek，运行时可切换至 OpenAI、Anthropic、DashScope、Gemini、Ollama、自定义 OpenAI 兼容等十余个供应商。

## 常用命令

```bash
# 一键启动前后端开发服务（后端 8765，前端 5173）
./scripts/dev.sh

# 后端（在 backend/ 目录）
uv sync                                                          # 安装依赖
uv run python -m uvicorn src.server.main:app --reload --port 8765  # 启动 API
uv run pytest                                                    # 运行全部测试
uv run pytest test/test_example.py::test_always_passes           # 运行单个测试

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

# 后端桌面端可执行文件（在目标平台分别执行）
./scripts/build-backend-desktop.sh
```

首次运行需 `cp backend/.env.example backend/.env` 并填入 `AGENT_WORKSTATION_DEEPSEEK_API_KEY`。环境要求：Node >= 20（dev.sh 优先用 nvm 切到 24）、Python >= 3.11、uv。

## 架构要点

### 后端请求流（核心链路）

`backend/src/server/main.py` 注册所有路由，前缀均为 `/api/*`。关键路径是对话流：

1. **`routes/chat/handlers/send.py`** — `POST /api/chat/send` 是 Chat SSE 统一入口，只负责请求读取、用户消息写入、后台 Run 启动、前置错误处理和 SSE 编排。Plan Runtime 与 Subagent Runtime 已下线，后续重构前不要在旧链路上继续补丁式开发。

2. **`routes/chat/readiness_gate.py`** — Chat 前置信息充足性判断。系统内置 `ReadinessAgent` 模型优先、规则兜底，只判断当前用户输入是否足够开始执行；信息不足时返回 clarification，不进入 Runtime。ReadinessAgent 不选择架构、不选择 Team、不选择 Skill、不决定 ToolScope。

3. **`routes/chat/intent_router.py`** — Chat 第一层架构路由。仅在 readiness 通过后运行，模型语义路由优先、规则路由兜底；当前只决定本轮进入 `react`，Plan Runtime 与 Subagent Runtime 已砍掉，不再作为路由结果。`clarification` 不是 runtime.kind，只是 readiness 未通过后的等待状态。它不选择 Team、不裁剪 Worker、不选择 Skill、不决定 ToolScope。第二层 `executor` 由前端选择机械决定：选择单 Agent 时为 `single_agent`，选择 Team 时为 `team`。路由结果写入 `INTENT_ROUTE` trace，用于排查本轮为何进入某条架构线路。

4. **`routes/chat/runtime_dispatcher.py` / `routes/chat/react_runtime.py`** — Chat Runtime 分发与 ReAct 执行入口。`runtime_dispatcher.execute_chat_runtime()` 只按第一层 `runtime.kind` 分发；当前仅 `react` 在线，信息不足时已经在进入 Runtime 前由 send 直接返回反问事件。`react_runtime.execute_react_runtime()` 再按第二层 `executor.kind` 执行：`single_agent` 直接运行主 Agent，`team` 先运行 Team Worker，再把 Worker report 注入 Leader 上下文后运行 Leader。

5. **`routes/chat/runtime_builder.py`** — IntentRoute 之后的运行时组装层。负责合成本轮模型配置、完整 Team 上下文、主 Agent runtime cache key、`MainAgentRuntimeSpec`，并通过 `create_main_agent_from_spec()` 委托工厂创建/复用 Agent。它不再裁剪 Team 成员，不再从 IntentRoute 读取 Skill/ToolScope。Skill 选择由 `skill_selector.py` 处理；工具范围由 `tool_scope_resolver.py` 处理。

6. **`routes/chat/team_runtime.py` / `routes/chat/team.py` / `routes/chat/team_worker_runtime.py`** — Team Runtime。当前是 AgentScope-style 适配层，保留本项目 SQLite Team 配置和前端活动面板，但执行语义收束为 `Leader dispatch -> Worker isolated ReAct -> Worker report -> Leader synthesize`。Worker 选择属于 Team Runtime 内部策略：优先使用系统内置 `TeamWorkerSelector` 模型选择本轮 Worker；模型输出解析失败或调用异常时使用全部 Worker 兜底；模型合法返回空数组时只保留 Leader，未参与 Worker 标记为 `skipped`。Worker 依赖调度纯逻辑在 `team_scheduler.py`，单个 Worker 的独立 ReAct 在 `team_worker_runtime.py`。

7. **`routes/chat/team_worker_selector.py` / `routes/team_defaults.py`** — Team Worker Selector 系统 Agent。Selector 是内置基础设施 Agent，不是普通业务 Agent：无默认工具、无 MCP、无 Skills、无 Bash/HTTP/知识库工具，只输出结构化 JSON。每个 Team 有可持久化字段 `workerSelectorPrompt`，前端 Team 设置页可编辑，运行时动态注入 Selector Agent；默认提示词在 `routes/team_defaults.py`，避免路由之间循环 import。

8. **`routes/chat/leader_stream.py`** — Leader/单 Agent 事件流处理。消费 `agent.reply_stream()`，维护 `LeaderStreamState`，处理模型 cache、trace、permission decision、tool artifacts、HITL 早停、流式文本/思考累计、Skill end 和模型输出后的 clarification 兜底。

9. **`routes/chat/completion.py`** — Leader run 成功/失败收尾。负责完成 Skill/Team 状态、写 assistant message、写 trace，并输出 `TEAM_ACTIVITY` / `ERROR` 等后续 SSE。旧记忆模块已删除，当前不再触发 memory summarize。

10. **`agents/factory.py`** — 唯一的 AgentScope 运行时构造入口。`get_or_create_agent()` 按 `agent_id` 缓存实例；`_create_model()` 按 `adapter` 字段分发到不同 ChatModel 类；`FallbackChainModel` 是应用层多模型降级链（主模型失败逐级回退并发 `attempt`/`success`/`failure` 事件）。工具集在 `_create_default_toolkit()` 注入：普通 Agent 默认拥有 Read/Grep/Glob/AskUser/FinalAnswerStart，Write/Edit 受权限控制；Bash/HttpRequest/知识库工具/MCP 受 `RuntimeToolScope` 裁剪。系统基础设施 Agent 可通过 `include_default_tools=False` 创建完全无工具实例。`GuardedBash` 强制执行 shell 黑名单和超时。执行环境为 `LocalWorkspace`（默认工作目录是项目根目录，避免 Glob/Grep 扫描用户主目录）或 `DockerWorkspace`。改 Agent 配置后必须调 `invalidate_agent()` 让缓存失效。

11. **`agents/agentscope_usage_patch.py`** — 启动时 `patch_agentscope_usage_events()` 给 AgentScope 打补丁以捕获 usage 事件。

### 持久化

`backend/src/storage/database.py` 用同步 `sqlite3`（线程锁 `RLock`）封装所有存储，尽管依赖里有 `aiosqlite`。两类存储模式：

- **结构化表**：`agents`、`teams`、`team_members`、`mcp_servers`、`skills`、`chat_sessions`、`chat_messages`、`run_events`。历史库中可能仍残留 `memory_facts` / `memory_events` / `memory_candidates` 表，但旧记忆模块已删除，不再作为当前运行时能力。
- **`json_store` 表**：`(namespace, key) -> JSON`，用于系统设置等 Pydantic 文档。运行时模型设置存于 namespace `system_settings`、key `model`，由 `config/runtime_settings.py` 读写。

Schema 演进用 `_ensure_column()` / `ALTER TABLE` 原地升级开发库，不用迁移框架。早期 JSON 文件（如 `data/chat_sessions.json`）通过 `migrate_json_*_file()` 一次性导入 SQLite。

### 配置分层

- `config/settings.py` — `pydantic-settings`，环境变量前缀 `AGENT_WORKSTATION_`，从 `.env` 加载。属启动期静态配置（端口、DB URL、CORS、默认 DeepSeek key）。
- `config/runtime_settings.py` — 运行时可变的模型供应商配置，持久化在 `json_store`。`MODEL_PROVIDERS` 定义各供应商的 adapter/默认 model/base_url。`get_effective_model_settings()` 是工厂取当前生效模型的入口；公开给前端时 API key 用 `_mask_secret()` 脱敏。

### 前端

- `frontend/src/App.tsx` — 用 `appStore.currentView` 通过 `VIEW_PAGES` 映射表（非 switch）懒加载页面；主题用 `useLayoutEffect` 同步到 `data-theme`；每 10s 轮询 `/api/health`。
- 状态用 Zustand（`stores/appStore.ts`、`stores/settingsStore.ts`）。
- API 调用与 SSE 事件流封装在 `hooks/`（`useEventStream.ts` 等）。页面在 `pages/`，对应后端各路由域（Chat/Trace/Sandbox/AgentTeam/MCP/Skills/Settings）。
- Vite 把 `/api` 代理到 `localhost:8765`；`@/` 别名指向 `src/`。
- UI 库为 `@agentscope-ai/design` + `@agentscope-ai/chat` + Ant Design，主题用 carbonTheme/carbonDarkTheme。

### Electron

`electron/main.ts` 开发模式连接 Vite dev server，生产模式加载 `frontend/dist` 并 spawn Python 后端子进程。`contextIsolation: true` + `nodeIntegration: false`，通过 `preload.ts` 桥接 IPC。

## 约定

- 开发必须遵循 `docs/development-guidelines.md`。该文件是正式开发规范，覆盖模块化、React/TypeScript、Python/FastAPI、SQLite、Agent Runtime、测试、Git 和文档要求。
- 如果 `AGENTS.md` / `CLAUDE.md` 与 `docs/development-guidelines.md` 冲突，以更具体、更严格、更新的规范为准；不确定时先按 `docs/development-guidelines.md` 执行。
- `AGENTS.md` 是开发代理记忆和项目协作规范的唯一真实来源；`CLAUDE.md` 只作为兼容入口，避免多份记忆漂移。
- 当前已删除/下线的旧设计：记忆模块、Plan Runtime、Subagent Runtime。后续不要在旧字段、旧事件或旧工具链上继续补丁式开发，只能重新设计后按新方案接入。
- 在本项目 AgentScope 语境中，`middleware` 按 AgentScope hooks / `MiddlewareBase` 理解；讨论 Plan Mode 等运行时增强时，优先考虑通过 middleware hooks（如 `on_system_prompt`、`on_acting`）接入，而不是恢复旧 Runtime 分支。
- 推进 Plan Mode 任务进度能力时，固定顺序为：先核对 SQLite/Pydantic/前端 TypeScript 数据结构，再补 `todo_write` / `task_output` / `task_list` 等工具与 Runtime Event，最后和前端流式展示、审批入口、刷新恢复一起联调。
- 代码注释和文档用中文，技术术语和标识符保持英文。
- TODO 文档和待办事项统一用中文编写，便于团队协作和后续交接。
- Chat SSE 事件新增或变更前必须先更新 `docs/chat-sse-event-contract.md`，前端只依赖该契约中的稳定事件。
- 文件均带中文 docstring 说明职责，遵循既有风格。
- 前端遵循若干约定（见 `App.tsx` 注释）：派生状态不用 effect、对象映射表代替 switch、初始化只做一次。
- 文档入口见 `docs/README.md`；当前架构见 `docs/architecture-overview.md`；记忆模块重设计见 `docs/memory-system-design.md`；项目现状与待办见 `docs/project-progress-handoff.md` 和 `docs/project-todo.md`；开发规范见 `docs/development-guidelines.md`。
