# Agent Workstation 项目进展与协作交接

> 更新时间：2026-07-14。本文只记录当前完成度、验证结果和已知缺口。

## 当前阶段

项目已从功能原型进入 Runtime 与产品化收口阶段。当前定位是本地优先的桌面 Agent 工作站：ReAct 是唯一在线 architecture，`agent` / `team` 是显式 executor mode。

当前综合成熟度约 70%：Runtime 内核和主要管理页面可用，平台归属、Artifact、自动化、评估和正式桌面发布仍未完成。

## 已在线能力

### Chat 与运行状态

- Readiness Gate、Intent Router、ReAct Runtime 和 ToolScope。
- 单 Agent 流式执行、AskUser、HITL、取消、重跑和刷新恢复。
- 后台 Run、RunState v2、DisplaySnapshot v3、Runs 和 Trace。
- send / resume 共用 Leader 流处理和 completion 收口；顶层 Run 区分用户输入、权限审批和外部执行等待，终态不可逆。
- Plan Mode 通过 middleware 接入，仅支持单 Agent，不恢复旧 Plan Runtime。

### TeamRun

- 每次 Team 请求创建独立 TeamRun，Leader 动态创建临时 Member 和任务。
- Member session 隔离，通过 Task List、Mailbox 和任务结果协作。
- 用户可向指定成员或全员发送 Mailbox 消息。
- Team 使用独立 `TEAM_EVENT` / `teamSnapshot` / Team Console。
- 持久 Team CRUD、template、固定成员列表、`/api/teams`、`teams` / `team_members` 表已经删除。

### Subagent

- Markdown + YAML frontmatter 文件定义和项目级 / 用户级扫描。
- ReAct 内 `DelegateTask` 前台委托和 fresh-context 子 Agent。
- 工具、Skills、Memory 和 permission mode 按父权限上限裁剪。
- `subagent_invocations`、`SUBAGENT_*` 事件、Trace 和 HITL resume。
- M1 已完成，M2 部分完成；inline MCP、hooks、worktree isolation 等尚未完成。

### Memory

- user / agent / global Markdown 记忆。
- SQLite `memory_nodes`、`memory_node_tree` / `memory_node_query`。
- MemoryAdmissionGate、运行前注入、运行后提炼和审计。
- disabled / zvec provider、召回反馈、Memory Wiki 和 Skill 自进化 MVP。
- 当前方向是稳定和迁移验证，不继续横向扩张实验能力。

### 管理与桌面端

- Agent CRUD、智能创建、导入导出、模型、工具、Skills、MCP 和权限配置。
- Agents、Subagents、Tools、MCP、Skills、Memory、Runs、Trace、Sandbox、Workflow、Settings 页面。
- Electron 开发窗口、后端启动和 macOS / Windows 内测打包脚本。

## 当前验证

最近一次完整验证：

```text
backend pytest: 286 passed
frontend build: passed
frontend lint: passed
/api/teams: 404
legacy Agent Builder mode=team: 422
legacy teams / team_members tables: removed
```

Vite 仍有约 968 KB 的大 chunk 警告，不影响构建，但需要后续拆包。

## 主要缺口

1. Team、Subagent、Plan、HITL 和刷新恢复缺少完整 Playwright 回归。
2. Artifact 还没有正式生命周期，大体积工具结果主要依赖 Trace 摘要。
3. Workspace / Project 归属和资源绑定尚未实现，设计见 `docs/workspace-project-design.md`。
4. Agent / Subagent 配置版本、评估指标、自动化调度和 Secret 治理尚未形成平台能力。
5. Electron 正式签名、公证、Windows 真机、`userData` 迁移和自动更新尚未完成。

## 核心入口

- `backend/src/server/routes/chat/`：Chat、RunState、恢复和 TeamRun。
- `backend/src/agents/factory.py`：AgentScope Agent / Model / Toolkit 构造入口。
- `backend/src/subagents/`：Subagent 定义、权限、上下文和执行。
- `backend/src/memory/`：记忆准入、检索、提炼和 Wiki。
- `backend/src/storage/database.py`：SQLite schema 和存储访问。
- `frontend/src/pages/ChatPage.tsx`：Chat 主工作台。
- `frontend/src/pages/AgentsPage.tsx`：持久 Agent 管理。
- `frontend/src/hooks/useEventStream.ts`：RunState / SSE 订阅。
- `frontend/src/features/team-runtime/`：Team 前端状态投影。

完整文档分类见 `docs/README.md`，待办只维护在 `docs/project-todo.md`。
