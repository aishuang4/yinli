# 引力 YINLI

引力 YINLI 是面向办公场景的本地优先 AI 工作台，基于 AgentScope 2.0、Electron 和 React 构建。它以 Chat 为主入口，让用户在一个受控桌面环境中读取、整理和产出文档、表格与项目文件，并提供单 Agent 执行、临时 Team 协作、Subagent 委托、Plan Mode、工具权限、长期记忆、运行恢复和 Trace 审计。

项目面向需要反复执行真实任务的本地工作流，不把多 Agent 简化成固定角色聊天。运行过程、任务状态、工具调用和最终成果都有明确的隔离、持久化与恢复边界。

## 升级版本说明

当前版本是引力的架构升级版本，不是对旧 Runtime 的兼容性修补。本次升级重新收束了 Chat 执行链路和多 Agent 协作语义：

- Team 升级为一次性 `TeamRun`，由 Leader 动态创建临时成员，通过 Task List 和 Mailbox 协作，不再选择持久 Team 或固定成员。
- Subagent 升级为 ReAct 内部 `DelegateTask` 委托，使用独立上下文和文件定义，不恢复旧独立 Subagent Runtime（`runtime.kind=subagent`）。
- Plan Mode 升级为 AgentScope middleware 能力，不再使用旧独立 Plan Runtime（`runtime.kind=plan`）或旧审批链路。
- Memory 升级为运行前注入、工具查询和运行后提炼组合，不恢复旧 memory runtime。
- Chat 前端升级为 RunState / DisplaySnapshot 主链路，Team 状态使用独立 `TEAM_EVENT` / `teamSnapshot`。

旧开发库中可能仍有少量历史字段，但它们只用于最低限度的启动或数据读取，不代表当前功能，也不能作为后续实现入口。

## 核心能力

### 单 Agent

- 在 Chat 中选择 Agent，使用其模型、Prompt、Skills、MCP、知识库工具和权限配置执行任务。
- 支持流式输出、工具调用、AskUser、人工确认、取消和刷新恢复。
- RunState 与 DisplaySnapshot 为前端提供稳定的运行状态，不依赖原始 SSE 重放恢复界面。

### 一次性 TeamRun

- 用户先选择 Agent，再切换到 Team 模式；该 Agent 成为本轮 Leader。
- Leader 根据任务动态创建临时 Member 和共享任务，不读取持久 Team 或固定成员列表。
- 每个 Member 使用隔离的 AgentScope session，通过 Task List、Mailbox 和任务结果协作。
- 用户可以向指定成员或全员发送 Mailbox 消息，直接干预本轮成果。
- Team 过程只进入独立 Team Console，Chat 主回答区只展示最终综合结果。

### Subagent 委托

- Subagent 使用 Markdown + YAML frontmatter 定义，支持项目级和用户级目录。
- 主 Agent 或 Team Leader 可在 ReAct 内通过 `DelegateTask` 委托独立上下文任务。
- 工具、Skills、记忆和 permission mode 按父 Agent 权限上限裁剪，不能扩大权限。
- 委托过程进入 invocation、Runtime Event 和 Trace 审计，并支持人工交互后的恢复。

### Plan Mode

- Plan Mode 通过 AgentScope middleware 接入 ReAct，不是独立 Runtime。
- 单 Agent 可使用 `plan_enter`、`plan_write`、`todo_write`、`task_output` 和 `plan_exit` 维护计划。
- Team Runtime 不注入 Plan Mode 工具；需要先规划再协作时，应分成两轮执行。

### 工具、权限与记忆

- 支持 Read、Grep、Glob、Write、Edit、Bash、HTTP、公开网页 WebFetch、MCP、知识库工具和 Skills；WebSearch 尚未接入。
- LocalWorkspace 与 DockerWorkspace 提供本地或容器执行环境。
- 新记忆系统通过运行前注入、memory tools、运行后准入和后台提炼接入 ReAct。
- 历史记忆使用 SQLite `memory_nodes`，长期偏好使用 user / agent / global Markdown 文件，向量检索支持 `disabled` 和 `zvec` provider。

### 工作台与审计

- Chat、Runs、Trace、Agents、Subagents、Tools、MCP、Skills、Memory、Sandbox 和 Workflow 使用独立页面。
- Team 状态使用独立 `TEAM_EVENT` / `teamSnapshot`，不污染单 Agent DisplaySnapshot。
- Dynamic Workflow 当前是独立实验工作区，后续按 Script Review 和 Runtime Cockpit 路线演进，不进入 Chat Runtime。
- Electron 支持开发运行和 macOS / Windows 内测打包流程。

## 技术栈

- 桌面端：Electron
- 前端：React 19、TypeScript、Vite、Zustand、AgentScope Design、Ant Design
- 后端：Python 3.11、FastAPI、AgentScope 2.0
- 存储：SQLite、Markdown、file-first TeamRun、可选 zvec
- 模型：DeepSeek 默认，也支持 OpenAI、Anthropic、DashScope、Gemini、Ollama 和自定义 OpenAI-compatible provider

## 快速开始

环境要求：

- Node.js >= 20
- Python >= 3.11
- [uv](https://docs.astral.sh/uv/)

```bash
# 1. 配置后端
cp backend/.env.example backend/.env
# 编辑 backend/.env，至少配置一个可用模型供应商

# 2. 安装依赖
cd backend && uv sync && cd ..
cd frontend && npm install && cd ..

# 3. 启动后端 8765 和前端 5173
./scripts/dev.sh
```

浏览器访问 [http://127.0.0.1:5173](http://127.0.0.1:5173)。模型供应商也可以在应用 Settings 中配置和切换。

桌面开发模式：

```bash
cd frontend
npm run electron:dev
```

## 开发命令

```bash
# 后端
cd backend
uv run python -m uvicorn src.server.main:app --reload --port 8765
uv run pytest

# 前端
cd frontend
npm run dev
npm run build
npm run lint

# Electron
npm run electron:compile
npm run electron:pack
npm run electron:build:mac
npm run electron:build:win
```

## 目录结构

```text
backend/src/
  agents/               AgentScope Agent 与模型工厂
  memory/               记忆准入、检索、提炼和 Wiki
  server/routes/chat/   Chat、RunState、TeamRun 和恢复链路
  subagents/            Subagent 定义、权限和执行
  storage/              SQLite 持久化
  tools/                Agent 工具
frontend/src/
  features/             领域组件和状态投影
  hooks/                API 与事件流
  pages/                工作台页面
electron/               Electron main / preload
docs/                    当前架构、稳定契约和路线文档
scripts/                 开发、打包和后端构建脚本
```

## 架构边界

- Chat 当前只有 `react` architecture；`agent` / `team` 是显式 executor mode。
- Team 每次请求创建新的 TeamRun，不选择持久 Team，不复用上轮成员或 Mailbox。
- Subagent 是 ReAct 内部工具委托，不恢复旧 `runtime.kind=subagent`。
- Plan Mode 是 middleware 能力，不恢复旧独立 Plan Runtime（`runtime.kind=plan`）或 approve / deny 链路。
- 旧记忆模块已删除；记忆能力只能沿当前 hooks、tools 和后处理方案演进。
- 新增或变更 Chat SSE、RunState、DisplaySnapshot 前，必须先更新稳定事件契约。

## 文档

- [`AGENTS.md`](AGENTS.md)：当前项目事实和开发代理规范。
- [`docs/README.md`](docs/README.md)：完整文档索引。
- [`docs/architecture-overview.md`](docs/architecture-overview.md)：在线架构总览。
- [`docs/chat-sse-event-contract.md`](docs/chat-sse-event-contract.md)：Chat / RunState / DisplaySnapshot 稳定契约。
- [`docs/team-runtime-redesign.md`](docs/team-runtime-redesign.md)：一次性 TeamRun、Task List 与 Mailbox。
- [`docs/subagent-delegation-design.md`](docs/subagent-delegation-design.md)：Subagent 定义和委托模型。
- [`docs/workspace-project-design.md`](docs/workspace-project-design.md)：Workspace / Project 平台归属与上下文切换设计，Phase 0 至 Phase 3 已实现。
- [`docs/development-guidelines.md`](docs/development-guidelines.md)：开发规范。
- [`docs/project-todo.md`](docs/project-todo.md)：当前待办。

## License

[Apache License 2.0](LICENSE)
