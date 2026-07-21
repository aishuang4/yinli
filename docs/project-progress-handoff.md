# Agent Workstation 项目进展与协作交接

> 更新时间：2026-07-20。本文只记录当前完成度、验证结果和已知缺口。

## 当前阶段

项目已从功能原型进入 Runtime 与产品化收口阶段。当前定位是本地优先的桌面 Agent 工作站：ReAct 是唯一在线 architecture，`agent` / `team` 是显式 executor mode。

当前综合成熟度约 79%：Runtime 内核、主要管理页面和 Workspace / Project Phase 0 至 Phase 3 可用，Artifact、自动化、评估和正式桌面发布仍未完成。

### Workspace / Project

- 已建立 `Workspace -> Project -> Session -> Run` 归属模型、默认记录和历史数据回填。
- Chat 在创建 Agent 前解析不可变 `RuntimeContext`；Session 不能跨 Project 继续。
- Project root 与 Agent filesystem scope 取交集，不相交时拒绝运行。
- TeamRun、Subagent、Memory、Trace、RunState 已继承 Workspace / Project，并按当前 Project 隔离。
- Project resource binding 已作为持久资源白名单参与 Runtime：Agent 必须绑定，Skill、MCP 和 HTTP Tool 取 Agent 配置与 enabled binding 的交集。
- 已有 Project 在升级时建立一次性资源基线，新建 Project 快照绑定创建时的 Workspace 资源；后续新增资源不会自动授权。
- Chat 历史区按 Workspace / Project 分类，点击 Session 自动恢复其归属；新对话在首次输入前选择或创建工作上下文。管理侧栏保留资源上下文切换入口。
- Session、Runs、Trace、Memory 与 Agent、Skill、MCP、HTTP Tool 管理已读取当前上下文；新建持久资源会显式绑定当前 Project。
- `Workspace + Project` 使用 bootstrap 组合 API，Project root 校验失败不会残留空 Workspace。
- 正式 Artifact ownership 与浏览器级跨 Project 自动化回归仍待实现。

## 已在线能力

### Chat 与运行状态

- Readiness Gate、Intent Router、ReAct Runtime 和 ToolScope。
- 单 Agent 流式执行、AskUser、HITL、取消、重跑和刷新恢复。
- 后台 Run、RunState v2、DisplaySnapshot v3、Runs 和 Trace。
- send / resume 共用 Leader 流处理和 completion 收口；顶层 Run 区分用户输入、权限审批和外部执行等待，终态不可逆。
- Plan Mode 通过 middleware 接入，仅支持单 Agent；删除的是旧独立 Plan Runtime（`runtime.kind=plan`），不是当前 Plan Mode 能力。

### TeamRun

- 每次 Team 请求创建独立 TeamRun，Leader 动态创建临时 Member 和任务。
- Member session 隔离，通过 Task List、Mailbox 和任务结果协作。
- 用户可向指定成员或全员发送 Mailbox 消息。
- Team 使用独立 `TEAM_EVENT` / `teamSnapshot` / Team Console。
- 持久 Team CRUD、template、固定成员列表、`/api/teams`、`teams` / `team_members` 表已经删除。

### Subagent

- Subagent 是 ReAct 内部 `DelegateTask` 委托；删除的是旧独立 Subagent Runtime（`runtime.kind=subagent`），不是当前委托能力。
- Markdown + YAML frontmatter 文件定义和项目级 / 用户级扫描。
- ReAct 内 `DelegateTask` 前台委托和 fresh-context 子 Agent。
- 工具、Skills、Memory 和 permission mode 按父权限上限裁剪。
- `subagent_invocations`、`SUBAGENT_*` 事件、Trace 和 HITL resume。
- M1 已完成，M2 部分完成；inline MCP、hooks、worktree isolation 等尚未完成。

### Skills

- Chat Runtime 已从关键词预选和完整 `SKILL.md` 注入迁移到 AgentScope 原生 `skills_or_loaders` 与内置 `Skill` viewer。
- Workspace / Project binding、Agent 配置和请求显式 `skillIds` 继续作为 catalog 白名单；Agent cache key 包含包路径和 `SKILL.md` 修改时间。
- 单 Agent、Team 临时 Member 和 `DelegateTask` Subagent 已统一传递 Skill 包路径；Team 协调、综合和恢复轮继续禁用 Skills。
- Skill 生命周期以真实 viewer 调用为准，viewer 不重复显示普通工具卡片；本地 Bash 与网络权限已解耦。
- Skill zip 安装已增加标准 frontmatter、符号链接和兼容 warning 诊断，详情页展示非阻断提示。
- 持久 Docker sandbox 会 seed Skill 到 `/workspace/skills`；非持久 Docker + Skills 当前明确拒绝启动，避免宿主路径在容器中静默失效。
- 尚需用至少两个模型供应商做 Sandbox 行为验证，重点检查 Skill 相对路径转绝对路径和 Office script 调用稳定性。

2026-07-20 真实模型 smoke test：

- DeepSeek `deepseek-v4-flash` 已完成 `Skill -> Read references/ -> Bash scripts/ -> FinalAnswerStart` 全链路，最终结果和 SkillRun 均正确收口。
- 测试 Agent 关闭 network 后仍可运行本地 Bash，验证 execute 与 network 权限已解耦。
- macOS 当前运行环境提供 `python3`，不提供 `python`；Skill 示例和后续 Office Skills 应显式使用 `python3`，或在脚本前探测解释器。
- DashScope `qwen-plus` 已到达真实供应商接口，但返回 `401 invalid_api_key`，因此第二供应商行为验证仍未完成。
- 真实 Run 发现 Runtime Event 路径仍把 `Skill` viewer 写入 DisplaySnapshot 普通工具项；已在 projector 层过滤并增加回归测试。
- 真实 Run 中一次 Bash 失败后成功恢复，Trace 摘要仍被历史 failed 事件锁死；已改为按最后一个顶层终态事件判定，并保留中间失败事件用于审计。

### Memory

- user / agent / global Markdown 记忆。
- SQLite `memory_nodes`、`memory_node_tree` / `memory_node_query`。
- MemoryAdmissionGate、运行前注入、运行后提炼和审计。
- disabled / zvec provider、召回反馈、Memory Wiki 和 Skill 自进化 MVP。
- 当前方向是稳定和迁移验证，不继续横向扩张实验能力。

### 网络工具

- `HttpRequest` 用于显式 API 请求；`WebFetch` 用于读取已知公开网页并提取正文，两者使用独立 ToolScope。
- `WebFetch` 已接入主 Agent、Team Member、Subagent 和 Plan Mode，并包含私网、重定向、响应大小、非文本内容和外部 prompt injection 提醒。
- WebSearch 尚未实现，不能把 WebFetch 描述成网络搜索能力。

### 管理与桌面端

- Agent CRUD、智能创建、导入导出、模型、工具、Skills、MCP 和权限配置。
- 系统 `default` Agent 已持久化并在管理页单独展示，可配置模型、系统提示词、Skills、MCP、HTTP 工具和 Guardrails；该 Agent 跨 Workspace 可见且禁止删除。
- Agents、Subagents、Tools、MCP、Skills、Memory、Runs、Trace、Sandbox、Workflow、Settings 页面。
- Electron 开发窗口、后端启动和 macOS / Windows 内测打包脚本。

## 当前验证

最近一次完整验证：

```text
backend pytest: 329 passed
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
3. Workspace / Project Phase 0 至 Phase 3 已完成；浏览器级跨 Project 自动化隔离和 Artifact ownership 尚未完成。
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
