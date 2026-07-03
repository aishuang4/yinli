# Agent Workstation 项目进展与协作交接

> 更新时间：2026-07-03
>
> 本文只记录当前项目事实和协作入口。详细架构见 `docs/architecture-overview.md`，任务排期见 `docs/project-todo.md`。

## 当前定位

Agent Workstation 是基于 AgentScope 2.0 + Electron + React 的桌面多 Agent 协作工作台。当前核心链路已经收束为 Chat 中选择单 Agent 或 Team，通过 ReAct Runtime 执行任务，并用 RunState / DisplaySnapshot 驱动前端主展示。新记忆系统 MVP 已部分接入：运行前注入 Markdown 摘要和最近 30 天节点树，运行后后台提炼 Markdown 记忆并持久化长期节点记忆。

旧记忆模块、旧 Plan Runtime、旧 Subagent Runtime 已下线。`memory*`、`planConfig`、`planRuns`、`callableAgents`、`subagentCalls` 等字段只保留历史兼容语义，不能继续作为新功能实现入口。当前记忆能力只能沿 `docs/memory-system-design.md` 的新方案推进，不新增 `memory` runtime.kind。

## 已在线能力

- Agent CRUD、导入导出、模型配置、降级模型链、工具权限、执行后端配置。
- Team CRUD、模板、成员依赖、Worker 选择、Worker 独立 ReAct、Leader 汇总。
- Chat 单 Agent / Team 运行、历史会话、Markdown、多模态图片、HITL、工具 / Skill / Team 过程展示。
- 后台 Run：`/api/chat/send/start`、`/api/chat/resume/start`、Run Center、取消、重跑、SQLite 运行记录。
- RunState 主链路：`RUN_STATE` / `RUN_STATE_PATCH`、`displaySnapshot`、轻量工具摘要、刷新恢复。
- MCP、Skills、HTTP 工具管理与 Agent 注入。
- Trace：模型、工具、权限、Team、HITL、错误和 token/cache 事件审计。
- Electron 开发窗口、内测打包脚本、生产模式后端启动入口。
- Plan Mode middleware：单 Agent 下通过 `plan_enter` / `plan_write` / `todo_write` / `task_output` / `plan_exit` 生成计划；`plan_exit` 输出 finalized 计划作为最终答案，不走旧审批流。
- 记忆系统 MVP：`memory_nodes` SQLite 主表、`VectorStoreBackend` 抽象、`disabled` / `zvec` provider、`memory_node_tree` / `memory_node_query` 工具、user / agent / global Markdown 记忆文件、运行前记忆注入、运行后后台反思提炼和节点持久化。
- Skill 自进化雏形：已有 `SkillEvolutionAgent` 与 `skill_draft_propose` 草稿工具，但尚未完成 router gate、workflow evidence、颗粒度控制、变量流抽象、候选持久化和标准 Skill 注册。

## 核心入口

后端：

- `backend/src/server/main.py`：FastAPI 入口。
- `backend/src/server/routes/chat/handlers/send.py`：发送消息入口。
- `backend/src/server/routes/chat/readiness_gate.py`：运行前信息充足性判断。
- `backend/src/server/routes/chat/intent_router.py`：第一层 architecture 路由，当前只进入 `react`。
- `backend/src/server/routes/chat/react_runtime.py`：ReAct Runtime 执行入口。
- `backend/src/server/routes/chat/runtime_builder.py`：运行时规格组装。
- `backend/src/server/routes/chat/leader_stream.py`：AgentScope 流事件转换。
- `backend/src/server/routes/chat/background_runs.py`：后台 Run 与 RunState。
- `backend/src/server/routes/chat/event_hub/projector.py`：原始事件到 RunState / DisplaySnapshot 投影。
- `backend/src/server/routes/chat/run_context.py`：组装运行上下文，并在 readiness 通过后注入 Skill prompt 和记忆上下文。
- `backend/src/server/routes/chat/completion.py`：成功收尾后触发后台 Markdown 记忆提炼和长期节点记忆持久化。
- `backend/src/agents/factory.py`：AgentScope Agent / Model / Toolkit 唯一构造入口。
- `backend/src/storage/database.py`：SQLite schema 和存储访问。
- `backend/src/memory/retrieval.py`：生成运行前记忆注入文本。
- `backend/src/memory/reflection.py`：运行后 user / agent / global Markdown 记忆与 Skill 草稿提炼编排。
- `backend/src/memory/extraction_agents.py`：记忆提炼 Agent 和 SkillEvolutionAgent prompt / schema。
- `backend/src/memory/nodes.py`、`backend/src/memory/node_persist.py`：长期节点记忆服务和运行后持久化。
- `backend/src/memory/markdown_store.py`：Markdown 记忆文件存储。
- `backend/src/vector_store/*`：向量存储抽象、策略和 Zvec / disabled provider。
- `backend/src/tools/memory_nodes.py`、`backend/src/tools/memory_reflection.py`：记忆节点查询工具和内部反思工具。

前端：

- `frontend/src/App.tsx`：页面映射、主题和健康检查。
- `frontend/src/pages/ChatPage.tsx`：Chat 主页面。
- `frontend/src/hooks/useEventStream.ts`：RunState 订阅。
- `frontend/src/types/runState.ts`：RunState 类型。
- `frontend/src/types/displayEvents.ts`：DisplaySnapshot / DisplayEvent 类型。
- `frontend/src/features/chat/*`：Chat 展示组件。
- `frontend/src/pages/AgentTeamPage.tsx`、`MCPPage.tsx`、`SkillsPage.tsx`、`ToolsPage.tsx`、`TracePage.tsx`、`RunsPage.tsx`、`SettingsPage.tsx`：主要页面。

## 启动与验证

首次启动：

```bash
cp backend/.env.example backend/.env
# 填入 AGENT_WORKSTATION_DEEPSEEK_API_KEY
cd backend && uv sync && cd ..
cd frontend && npm install && cd ..
./scripts/dev.sh
```

常用验证：

```bash
cd backend && uv run pytest
cd frontend && npm run build
cd frontend && npm run lint
```

桌面开发：

```bash
cd frontend && npm run electron:dev
```

## 当前注意事项

- Chat SSE / RunState / DisplaySnapshot 新增字段前必须先更新 `docs/chat-sse-event-contract.md`。
- Team Runtime 不支持 Plan Mode。需要“先规划再 Team 执行”时，先用单 Agent 生成计划，再由用户下一轮选择 Team 普通执行。
- `resume.py` 仍有部分旧式分支，后续应继续复用 `leader_stream.py`、`completion.py`、`orchestration.py`。
- 旧记忆表可能存在于开发库中，但当前运行时不触发旧 memory summarize，也不应扩展旧记忆模块。新记忆数据使用 `memory_nodes`、Markdown 文件和 `VectorStoreBackend`。
- 默认 Markdown 记忆文件为 `backend/data/memory/users/user_memory.md`、`backend/data/memory/agents/agent_memory.md`、`backend/data/memory/global/global_memory.md`；非默认 owner 使用 `{owner_id}/user_memory.md`、`{agent_id}/agent_memory.md`、`{workspace_id}/global_memory.md`，不兼容读取旧 `default.md`。
- Skill 自进化下一步必须先实现 `SkillEvolutionRouterGate` 和 `WorkflowEvidenceBuilder`，再做颗粒度控制、变量流抽象和标准 Skill 注册。
- `CLAUDE.md` 只是兼容指针，当前事实以 `AGENTS.md` 为准。

## 文档入口

- `docs/README.md`：文档索引。
- `docs/architecture-overview.md`：当前架构总览。
- `docs/development-guidelines.md`：正式开发规范。
- `docs/chat-sse-event-contract.md`：稳定事件契约。
- `docs/project-todo.md`：后续任务。
- `docs/electron-packaging-plan.md`：正式打包后续。
- `docs/dynamic-workflows-plan.md`：动态编排概念评估。
- `docs/memory-system-design.md`：新记忆系统设计、当前实现边界和 Skill 自进化后续约束。
