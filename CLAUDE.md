# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Agent Workstation 是基于 AgentScope 2.0 + Electron + React 的桌面多 Agent 协作工作台。用户在 Chat 界面选择单个 Agent 或 Team 协作完成任务，支持 MCP 工具、Skills 技能、HTTP 知识库工具、三层记忆系统、权限控制与容器沙箱执行。

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

1. **`routes/chat.py`** — `POST /api/chat/send` 通过 SSE 返回 AgentScope 2.0 事件流（`REPLY_START`/`MODEL_CALL_*`/`TEXT_BLOCK_*`/`THINKING_BLOCK_*`/`TOOL_CALL_*`/`TOOL_RESULT_*`）。它聚合 Agent 配置、记忆系统提示、Skills 选择、MCP 运行时服务器、知识库工具，再调用工厂创建 Agent 并消费事件流。同时处理 HITL（`REQUIRE_USER_CONFIRM`/`REQUIRE_EXTERNAL_EXECUTION`）和 Team 协作。

2. **`agents/factory.py`** — 唯一的 AgentScope 运行时构造入口。`get_or_create_agent()` 按 `agent_id` 缓存实例；`_create_model()` 按 `adapter` 字段分发到不同 ChatModel 类；`FallbackChainModel` 是应用层多模型降级链（主模型失败逐级回退并发 `attempt`/`success`/`failure` 事件）。工具集在 `_create_default_toolkit()` 注入：Read/Grep/Glob 始终可用，Write/Edit/Bash/HttpRequest/知识库工具受 `tool_permission_profile`（如 `read_only`）和 `network_enabled` 控制。`GuardedBash` 强制执行 shell 黑名单和超时。执行环境为 `LocalWorkspace`（默认工作目录是用户主目录 `~`）或 `DockerWorkspace`。改 Agent 配置后必须调 `invalidate_agent()` 让缓存失效。

3. **`agents/agentscope_usage_patch.py`** — 启动时 `patch_agentscope_usage_events()` 给 AgentScope 打补丁以捕获 usage 事件。

### 持久化

`backend/src/storage/database.py` 用同步 `sqlite3`（线程锁 `RLock`）封装所有存储，尽管依赖里有 `aiosqlite`。两类存储模式：

- **结构化表**：`agents`、`teams`、`team_members`、`mcp_servers`、`skills`、`chat_sessions`、`chat_messages`、`run_events`、`memory_facts`/`memory_events`/`memory_candidates`（三层记忆）。
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

- 代码注释和文档用中文，技术术语和标识符保持英文。
- 文件均带中文 docstring 说明职责，遵循既有风格。
- 前端遵循若干约定（见 `App.tsx` 注释）：派生状态不用 effect、对象映射表代替 switch、初始化只做一次。
- 项目现状与待办见 `docs/project-progress-handoff.md` 和 `docs/project-todo.md`；设计文档见 `docs/agent-workstation-design.md` 与 `docs/agent-workstation-frontend-design.md`。
