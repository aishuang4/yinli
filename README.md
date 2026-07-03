# Agent Workstation

基于 AgentScope 2.0 + Electron + React 的桌面多 Agent 协作工作台。

## 快速开始

环境要求：

- Node.js >= 20
- Python >= 3.11
- uv

```bash
# 1. 配置后端环境变量
cp backend/.env.example backend/.env
# 编辑 backend/.env，填入 AGENT_WORKSTATION_DEEPSEEK_API_KEY

# 2. 安装依赖
cd backend && uv sync && cd ..
cd frontend && npm install && cd ..

# 3. 启动开发服务（后端 8765，前端 5173）
./scripts/dev.sh
```

桌面开发模式：

```bash
cd frontend
npm run electron:dev
```

## 常用命令

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
npm run electron:compile
npm run electron:pack
```

## 文档入口

- `AGENTS.md`：当前项目事实和代理协作规范。
- `docs/README.md`：文档索引。
- `docs/architecture-overview.md`：当前架构总览。
- `docs/chat-sse-event-contract.md`：Chat / RunState 稳定事件契约。
- `docs/development-guidelines.md`：开发规范。
- `docs/project-progress-handoff.md`：项目状态交接。
- `docs/project-todo.md`：后续任务。

旧记忆模块、旧 Plan Runtime、旧 Subagent Runtime 已下线。历史兼容字段仍可能存在，但不再作为当前运行时能力。

## License

Apache 2.0
