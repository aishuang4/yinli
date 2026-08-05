# 引力 YINLI 项目进展与协作交接

> 更新时间：2026-07-31。本文只记录当前完成度、验证结果和已知缺口。

## 当前阶段

项目已从功能原型进入 Runtime 与产品化收口阶段。当前定位是本地优先的桌面 Agent 工作站：ReAct 是唯一在线 architecture，`agent` / `team` 是显式 executor mode。

当前综合成熟度约 79%：Runtime 内核、主要管理页面和 Workspace / Project Phase 0 至 Phase 3 可用，Artifact、自动化、评估和正式桌面发布仍未完成。

### AgentScope 2.0.5 基线

- Runtime 依赖已从 `2.0.3` 升级到 `2.0.5`，锁文件保持 MCP `1.27.2`，仅新增上游必需的 `tzdata`。
- 已删除覆盖 `Agent._reasoning_impl` 的 usage patch，改由原生 middleware hook 采集 provider prompt cache usage。
- Write / Edit 异步权限接口、Docker `host_workdir`、Reply `finished_reason` / `error` 和取消终态均已完成兼容适配。
- 2.0.5 默认 runtime state injection 在工厂中显式关闭，现有 prompt、Plan middleware 和 Event Stream 边界不变。
- 后端全量 `376 passed`，前端 lint/build、源码后端启动、OpenAPI、macOS arm64 onefile 构建和产物健康检查均通过。
- 本机未配置模型 API key且 Docker daemon 未运行；真实供应商矩阵与 Docker 执行仍是发布前门禁，详见 `docs/agentscope-2.0.5-upgrade-plan.md`。

### Workspace / Project

- 已建立 `Workspace -> Project -> Session -> Run` 归属模型、默认记录和历史数据回填。
- Chat 在创建 Agent 前解析不可变 `RuntimeContext`；Session 不能跨 Project 继续。
- Project root 与 Agent filesystem scope 取交集，不相交时拒绝运行。
- TeamRun、Subagent、Memory、Trace、Domain Event 和 Projection stream 已继承 Workspace / Project，并按当前 Project 隔离。
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
- 后台 Run、Event Stream V3、DisplaySnapshot V4、Runs 和 Trace。
- send / resume 共用 Leader 流处理和 completion 收口；顶层 Run 区分用户输入、权限审批和外部执行等待，终态不可逆。
- Plan Mode 通过 middleware 接入且仅支持单 Agent；已具备持久化 revision、人工审核、自然语言模型修订、确认后执行、重新规划和实时步骤投影。删除的是旧独立 Plan Runtime（`runtime.kind=plan`），不是当前 Plan Mode 能力。

### Event Stream

- Event Stream V3 已原子切换生产 Chat，只保留 `RUNTIME_EVENT: RuntimeFrame` 与 `DisplaySnapshot V4` 两个前端边界。
- 后端在线链路为 `SourceEvent -> ChatProjectionNormalizer -> DomainEventRepository -> ProjectionRunner -> per-kind projector -> BlockLifecycleCoordinator -> ProjectionStreamStore -> SSE`；同一 Run 使用全局 `stream_ordinal`、lease/fencing、周期 Snapshot + tail replay、retention、afterSeq fallback 和 completion barrier。
- `message/text/reasoning/thinking/tool/skill/approval/plan/artifact/error/subagent/team/team-member/team-task/team-activity/team-mail` 已注册独立 projector。Subagent 和 Team 的完整领域状态仍保留在各自存储，Chat 只消费嵌套 Block 摘要。
- 前端 Chat 与 Sandbox 已切换到严格 RuntimeFrame decoder、streamAdapter、Block Tree reducer、kind registry 和 Generic Block fallback。HITL renderer 只提交命令，状态由后端 Frame 回流。
- `event_hub`、RunState schema、DisplayEvent projector/reducer、旧 stream adapter、固定 Process/Plan 组件和工具结果反解析已删除；旧测试 fixture 已物理删除，不存在双读、双写或 compatibility fallback。
- SQLite 检测到旧 Chat 投影表或非 V4 Snapshot 时执行一次性运行域 reset，不转换旧数据；后续数据库只写 V4 Snapshot。
- Event Stream 专项测试现有 13 项，覆盖 aggregate transaction、跨 aggregate/并发 ordinal、非法生命周期、父子级联 close、零 Frame checkpoint、runner fencing、Snapshot + tail replay、retention fallback、clarification/HITL 和 terminal done。
- HITL 的 `RUN_WAITING` 只作为等待预告，实时订阅会持续到可操作的 approval Frame；resume 终态统一写回最终 V4 Snapshot、关闭订阅，并移除同一 Run 的旧 waiting 占位消息，避免刷新后重复显示进行中与完成两棵 Block Tree。

### TeamRun

- 每次 Team 请求创建独立 TeamRun，Leader 动态创建临时 Member 和任务。
- Member session 隔离，通过 Task List、Mailbox 和任务结果协作。
- 用户可向指定成员或全员发送 Mailbox 消息。
- Team 以 file-first 状态和 `events.jsonl` outbox 为事实源，不再给每条事件附加完整 snapshot；Chat 将 Run、Member、Task、Activity、Tool 和 Mail 分别投影为规范化 Block 子树。
- 前端 Team Console 只从 DisplaySnapshot V4 派生任务、成员、过程和 Mailbox，并支持通过 active TeamRun Mailbox API 向成员或全体发送干预消息；命令结果仍以 RuntimeFrame 回流为准。
- 持久 Team CRUD、template、固定成员列表、`/api/teams`、`teams` / `team_members` 表已经删除。

### Subagent

- Subagent 是 ReAct 内部 `DelegateTask` 委托；删除的是旧独立 Subagent Runtime（`runtime.kind=subagent`），不是当前委托能力。
- Markdown + YAML frontmatter 文件定义和项目级 / 用户级扫描。
- ReAct 内 `DelegateTask` attached fork、并行 fresh-context 子 Agent、`SubagentTaskWait` 和 `SubagentResultRead`。
- 工具、Skills、Memory 和 permission mode 按父权限上限裁剪。
- Session 内每个 invocation 使用 `task.json`、`status.json`、`result.md` 和 `transcript.jsonl` 作为文件事实源；SQLite 保存控制台查询索引。
- 父 Run Completion Barrier 强制所有 attached child 终态且 consumed；Chat 和 Subagents 控制台分别展示任务状态与结果消费状态。
- detached、嵌套、跨进程精确恢复、运行中消息和 worktree isolation 尚未开放。

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
backend pytest: 376 passed
frontend build: passed
frontend lint: passed
/api/teams: 404
legacy Agent Builder mode=team: 422
legacy teams / team_members tables: removed
```

2026-07-23 真实浏览器回归：

- 普通回答完成后 `streamDone=true`，afterSeq replay、Snapshot fallback 和刷新恢复正常。
- AskUser 在 `RUN_WAITING` 后正常显示可操作 approval；提交“开发”后 tool、approval、text 和 message 全部收口，刷新恢复后只有一棵完成态 Block Tree，无持续转圈或旧 waiting 占位。
- `DelegateTask(explorer)` 完成前父 Agent 保持等待，父 Agent 消费子结果后才输出最终回答；刷新后 `kind=subagent` 仍为单一完成态 Block。
- Subagents 页面可查看 completed invocation、结果、耗时和完整 transcript。

2026-07-27 Team 流式回归（AgentScope 2.0.3 基线）：

- Team、Member 和 Task 按运行进度逐步进入同一 DisplaySnapshot Block Tree，任务完成数从 `1/4` 连续更新到 `4/4`。
- 综合阶段逐条转发 `TEXT_BLOCK_DELTA`，最终答案从首个文本增量持续增长；`FINAL_ANSWER_REPLACE` 只做终态一致性校正。
- 后端完整测试 359 项、前端 lint/build 和浏览器真实运行均通过，最终 Chat Run 正确收口为 `completed`。

2026-07-31 AgentScope 2.0.5 升级验证：

- 新增 17 项升级专项回归并纳入全量测试，总计 `376 passed`。
- 前端 lint 和 production build 通过，最大主 chunk 仍约 410 KB。
- 源码后端和 macOS arm64 onefile 后端均成功启动，`/api/health` 返回 200；源码 OpenAPI 路由加载完整。
- onefile 产物由 PyInstaller 6.21.0 构建，AgentScope 内置工具脚本已被收集；Windows 真机仍按桌面发布计划单独验证。

前端已通过轻量 Design 适配入口移除 `@agentscope-ai/design` 完整 barrel 的隐式依赖，React runtime 单独分包；当前最大 chunk 约 410 KB，生产构建不再产生大 chunk 警告。

## 主要缺口

1. Team、Subagent、Plan、HITL 和刷新恢复缺少完整 Playwright 回归。
2. Artifact 还没有正式生命周期，大体积工具结果主要依赖 Trace 摘要。
3. Workspace / Project Phase 0 至 Phase 3 已完成；浏览器级跨 Project 自动化隔离和 Artifact ownership 尚未完成。
4. Agent / Subagent 配置版本、评估指标、自动化调度和 Secret 治理尚未形成平台能力。
5. Electron 正式签名、公证、Windows 真机、`userData` 迁移和自动更新尚未完成。

## 核心入口

- `backend/src/server/routes/chat/`：Chat、Event Stream V3、恢复和 TeamRun。
- `backend/src/agents/factory.py`：AgentScope Agent / Model / Toolkit 构造入口。
- `backend/src/subagents/`：Subagent 定义、权限、上下文和执行。
- `backend/src/memory/`：记忆准入、检索、提炼和 Wiki。
- `backend/src/storage/database.py`：SQLite schema 和存储访问。
- `frontend/src/pages/ChatPage.tsx`：Chat 主工作台。
- `frontend/src/pages/AgentsPage.tsx`：持久 Agent 管理。
- `frontend/src/hooks/useEventStream.ts`：RuntimeFrame SSE 订阅。
- `frontend/src/features/runtime-stream/`：Block Tree 协议、状态和 renderer。

完整文档分类见 `docs/README.md`，待办只维护在 `docs/project-todo.md`。
