# AGENTS.md

`CLAUDE.md` 导入本文件。不要往垫片里加规则。

## 先读

- [frontend/AGENTS.md](frontend/AGENTS.md) — React 应用、界面和 Chat 展示。
- [backend/AGENTS.md](backend/AGENTS.md) — FastAPI、Agent 运行时、存储和定时任务。

改某个目录前先读离它最近的文件。一条规则只写在一个文件里。其他地方用链接，不要抄过来。

## 这是什么

引力 YINLI 是本地优先的办公 AI 工作台。桌面壳是 Electron，界面是 React，API 是基于 AgentScope 的 FastAPI，数据在本机 SQLite。

用户在 Chat 里用一个 Agent，或用临时 Team。范围内有 MCP、Skills、HTTP 工具、权限、沙箱，以及当前的记忆路径。

## 地图

- `frontend/` — Vite React 应用。页面、功能和 Chat 流在这里。
- `backend/` — Python 包 `src`。路由、运行时、存储和工具在这里。
- `electron/` — 桌面主进程和 preload。开发模式加载 Vite。生产模式加载 `frontend/dist` 并拉起 API。
- `docs/` — 架构和契约。从 [docs/README.md](docs/README.md) 进入。
- `config/product-features.json` — 打包用的功能档。
- `scripts/dev.sh` — 本地 API 在 8765，Vite 在 5173。

## 依赖方向

- 前端只通过 `/api` 调用后端。Vite 把 `/api` 代理到 `localhost:8765`。
- Electron 渲染进程只用 preload 桥。保持 `contextIsolation` 开启、`nodeIntegration` 关闭。
- Chat 处理函数和 Team Runtime 不导入 `group_studio`。
- `group_studio` 和 `scheduler` 不导入 `src.server.routes.chat`。共用调用走 `src.runtime`。
- 新增的跨域 Chat 内部导入会让 `backend/test/test_import_boundaries.py` 失败。
- 不导入别的域里以下划线开头的私有函数。这类名字留在本域。
- `TODO(agents-md):` 还没有 import-linter 契约。上面的边界测试就是检查。

## 命令

在仓库根目录：

```bash
./scripts/dev.sh
./scripts/build-backend-desktop.sh
```

第一次跑 API 前，把 `backend/.env.example` 复制为 `backend/.env`，并设置 `AGENT_WORKSTATION_DEEPSEEK_API_KEY`。

需要 Node >= 22.12、Python >= 3.11 和 uv。`./scripts/dev.sh` 优先用 Node 24。

各包自己的命令写在 [frontend/AGENTS.md](frontend/AGENTS.md) 和 [backend/AGENTS.md](backend/AGENTS.md)。

## 共用规则

- 改了边界、公开导出、命令、配置键或目录时，同一次改动里更新最近的 `AGENTS.md`。
- 代码注释、docstring 和 TODO 文档用简体中文。标识符保持英文。
- 这些指引文件用简体中文。一条规则一行。路径、符号和命令放在反引号里。
- 长规范见 [docs/development-guidelines.md](docs/development-guidelines.md)。和本目录树不一致时，以和代码相符的那条为准。
- 不提交密钥、本地数据库、构建产物和日志。
- 提交说明用短英文前缀，例如 `fix:`、`feat:`、`docs:`。
- 提交前跑 `git diff --check`。改了后端就跑后端测试。改了前端就跑前端构建。
- 修过一次能复现的 bug，就补一个小的回归测试。
- 大功能先在 `docs/` 写设计。上线后留下仍然成立的决定，并更新 [docs/project-todo.md](docs/project-todo.md)。
- 被替换的计划不留在 `docs/`。
- 事件字段只写在对应契约里。其他文档链接到该契约。
- 改 Chat SSE 事件前先更新 [docs/chat-sse-event-contract.md](docs/chat-sse-event-contract.md)。前端只读这份契约里的事件。
- 不把通知写进 Chat SSE。
- 不恢复已删除的记忆模块、`runtime.kind=plan` 或 `runtime.kind=subagent`。
- Plan Mode 留在 ReAct middleware 上。Subagent 用 `DelegateTask` 文件任务，再用 `SubagentTaskWait` 和 `SubagentResultRead` 汇合。
- 不加 `memory` 运行时分支。记忆从 hooks、工具和运行后服务进入。
- `middleware` 按 AgentScope hooks 或 `MiddlewareBase` 理解。
- 产品档从 `config/product-features.json` 经 `GET /api/settings/product-features` 读取。不要写死 `OFFLINE_VIEWS`。
- `chat` 和 `settings` 保持开启。关掉页面只藏入口，并让对应管理 API 返回 404。
- 不因为页面被藏就关掉 `/api/chat/runs`、Agent / Skill / MCP 执行 API，或 `/api/settings/models`。
- 环境变量用 `AGENT_WORKSTATION_` 前缀。默认服务模式是 `desktop`。
- 缺少 `AGENT_WORKSTATION_KEY2API_SYNC_TOKEN` 时拒绝以托管模式启动。
- Web 和桌面不同步会话或文件。
- 业务代码里不放真实 token、私有地址或密码。

## 参考

- [docs/architecture-overview.md](docs/architecture-overview.md)
- [docs/development-guidelines.md](docs/development-guidelines.md)
- [docs/file-catalog-v1.md](docs/file-catalog-v1.md)
- [docs/context-storage-refactor.md](docs/context-storage-refactor.md)
