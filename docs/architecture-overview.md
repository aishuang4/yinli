# 引力 YINLI 当前架构总览

> 状态：2026-09-01 生效。本文替代早期大而全设计稿，只记录当前仍在线的架构事实。

## 项目定位

引力 YINLI 是基于 AgentScope 2.0 + Electron + React 的本地优先办公 AI 工作台。用户在 Chat 中选择单个 Agent 或 Team 执行文档、表格、文件和项目任务，运行时支持 MCP、Skills、HTTP 工具、权限控制、HITL、Trace、容器沙箱和新记忆系统 MVP。

旧记忆模块、旧独立 Plan Runtime（`runtime.kind=plan`）和旧独立 Subagent Runtime（`runtime.kind=subagent`）已下线，不能继续扩展旧链路。这里删除的是独立 architecture / 路由分支，不是当前能力：Plan Mode 仍通过 middleware 接入单 Agent ReAct，Subagent 仍通过 `DelegateTask` 接入 ReAct。旧 Subagent 字段、配置入口、事件兼容分支和前端入口不得作为当前 API / UI / Runtime 事实源保留；历史 SQLite 开发库只允许保留启动所需的最小兼容。当前记忆能力是新方案：通过运行前上下文注入、运行后 `MemoryAdmissionGate` 准入、后台提炼、历史记忆（底层 `memory_nodes`）和 memory tools 接入 ReAct，不新增 `memory` runtime.kind。

新版 Subagent 已作为 ReAct 内部 attached fork/join 能力上线：Subagent 定义来自 Markdown + YAML frontmatter 文件，主 Agent 通过 `DelegateTask` 并行 fork fresh-context 子任务，再用 `SubagentTaskWait` / `SubagentResultRead` 等待并消费文件结果。Completion Barrier 保证全部 child 终态且结果已消费后父 Run 才能结束；完整过程进入独立 transcript、Trace 和 `subagent_invocations` 审计，不新增 `runtime.kind=subagent`。Team executor 当前不注入 Subagent 工具。

## 技术栈

- 桌面壳：Electron
- 前端：React 19、TypeScript、Vite、Zustand、`@agentscope-ai/design`、`@agentscope-ai/chat`、Ant Design
- 后端：Python 3.11、FastAPI、AgentScope `2.0.5`、SQLite
- 模型：DeepSeek 默认，运行时可切换 OpenAI、Anthropic、DashScope、Gemini、Ollama、自定义 OpenAI 兼容等供应商
- 执行环境：本地 workspace 或 AgentScope DockerWorkspace

## AgentScope 运行时基线

当前依赖固定为 `agentscope==2.0.5`，MCP 保持 `1.27.2`。已删除覆盖 `Agent._reasoning_impl` 的 usage patch，改由 `ProviderCacheUsageMiddleware` 在 `on_model_call` 采集 provider prompt cache usage。`ScopedWrite` / `ScopedEdit` 使用异步 `match_rule()` / `generate_suggestions()`。`DockerWorkspace` 使用 `host_workdir`。工厂显式 `InjectionConfig(inject_runtime_state=False)`，避免默认注入时间/任务状态改变现有 prompt。`REPLY_END.finished_reason` 进入 Runtime payload；`interrupted`、`error`、`exceed_max_iters` 不得映射为成功收口。应用层取消是 Run 终态事实源。

真实供应商矩阵与 Docker 执行仍是发布前门禁，只记在 `docs/project-todo.md`。

## Chat 核心链路

1. `routes/chat/handlers/send.py` 接收用户消息，先按 `project_id` 解析 `RuntimeContext`，再落库、创建后台 Run 并编排 SSE。
2. `readiness_gate.py` 只做进入 Runtime 前的轻量启动判断；业务信息缺口交给主 Agent 在 ReAct 内通过 `AskUser` 外部工具暂停并恢复。
3. `intent_router.py` 只决定第一层 architecture。当前在线值为 `react`。
4. 请求中的显式 `mode` 决定第二层 executor：`agent` 为 `single_agent`，`team` 为 `team`。
5. `runtime_dispatcher.py` 只按 architecture 分发；当前只分发到 ReAct。
6. `react_runtime.py` 按 executor 执行单 Agent 或 Team。
7. `runtime_builder.py` 组装模型配置、Skill 包路径、ToolScope 和主 Agent runtime spec；Chat 请求不接收持久 Team 或固定成员上下文。
8. `run_context.py` 在 readiness 通过后按 Workspace、Project、Agent 和请求白名单裁剪可用 Skill catalog，并注入记忆上下文；完整 `SKILL.md` 不预注入 system prompt。
9. `leader_stream.py` 消费 AgentScope `reply_stream()`，转换文本、工具、权限、Plan Mode、Skill 和 HITL 事件。
10. `completion.py` 完成消息落库、Trace、Skill 状态和错误收尾，并在轻量 `MemoryAdmissionGate` 后触发 Markdown 记忆提炼和历史记忆持久化。
11. `background_runs.py` 将后台 Run 与前端 SSE 订阅解耦，把结构化 SourceEvent 送入 Domain Event 和 ProjectionRunner。
12. `projection/` 按 Run 内全局 ordinal 串行投影为 RuntimeFrame，并维护 DisplaySnapshot V4、tail replay、retention 和 terminal done。

### ReAct 上下文管理

主 Agent 发给模型的 messages 由三层组成，职责不得混淆：

- **system prompt（跨轮稳定）**：Agent profile 正文、Project / Session Workspace 说明、Runtime Context 规则、
  路由指令、Skill Run 提示、记忆摘要（受 `memory/retrieval.py` 预算约束，`# Summary` 存在时不重抄被摘要章节，
  并由 `memory_prompt_cache.py` 按会话冻结，避免每轮新增的历史记忆节点改写 prompt 打破供应商前缀 cache）、
  Clarification / Final Answer 契约。它不含时钟、不含会话历史、不含本轮附件；prompt 正文不进 Agent 缓存键，
  工厂在复用实例时直接刷新 `_system_prompt`。
- **AgentScope `state.context` / `state.summary`（会话记忆）**：这是在线对话记忆的唯一事实源，包含历史 user /
  assistant 消息、工具调用与结果，由 AgentScope `ContextConfig` 在接近上下文窗口时压缩。本轮附件说明作为用户
  消息的 `<attached_files>` 段进入上下文，后续轮次仍可见。不再把 `chat_messages` 扁平化拼进 system prompt。
- **HintBlock（运行时状态）**：时间和上下文长度由 AgentScope `InjectionConfig` 以 `<system-reminder>` 注入
  （上海时区、每 0.5h 最多刷新一次、`emit_hint_event=False` 不外发事件）。

Agent 实例生命周期在 `agents/factory.py`：`runtime_agent_id` 只含影响工具集 / 模型 / 权限 / Skill 包的稳定配置，
同一 `(agent_id, session_id)` 多轮默认复用同一实例；配置变化换实例时由 `agents/session_context.py` 把旧实例的
`context` / `summary` / `middle_context` 搬到新实例并释放旧实例；进程重启或实例被淘汰后，用 `ChatRunContext.session_history`
（`chat_messages` 最近 20 条正文）重建精简上下文。缓存有 LRU 上限与 6h 闲置淘汰，正在执行或等待用户回应
（AskUser / 审批）的实例不淘汰；用户不回应而直接发新消息时，工厂先补 `ToolResult` 关闭等待再复用。绑定本轮
Run 的能力（Plan Mode、DelegateTask manager）把 run id 写进 feature key，因此这类 Run 使用带上下文快照的新实例。
**Skill 正文的生命周期**遵循主流 Agent Skills 约定：未触发时 system prompt 只有 `<agent-skills>` 的 name /
description / dir；模型用 `Skill` 工具读过 SKILL.md 后正文留在会话里，后续 ReAct 步骤和后续几轮都可见；
`references/` 与脚本仍按需 Read / 执行。AgentScope 只把正文当一条 tool result，会被 `tool_result_limit` 截断、
被压缩摘要掉、在实例重建后丢失，因此 `routes/chat/skill_context_middleware.py` 的 `SkillContextMiddleware`
在 `on_acting` 捕获 `Skill` 工具的完整输出写入 `state.middle_context`（随实例搬运），在 `on_reply` /
刚读完后的 `on_reasoning` / 真正发生过压缩的 `on_compress_context` 用头尾指纹检查正文是否仍完整在 context
中，缺失时以 `<activated-skill>` HintBlock 补回；总预算 12000 字、单个 6000 字，超出淘汰最早启用的。进程
重启后 `react_runtime` 用 `skill_runs` 记录中最近 3 个仍在本轮 catalog 内的 Skill 恢复该集合。平台侧
`SkillRun` 仍每轮关闭，它记录"本轮用了哪个 Skill"，与"模型已读过哪些说明书"是两件事。
Readiness / IntentRouter 系统 Agent 是一次性任务，每次调用前清空上下文；当前 `INTENT_CATALOG` 只有 `react`
时不调用模型，readiness 只在规则发现模糊执行型请求时才调模型，是否调用与耗时写入 `READINESS_GATE` /
`INTENT_ROUTE` trace。`MODEL_CALL_START` trace 附带 `contextStats`（prompt / summary / context 字符数与消息数、
工具数），`TOOL_CALL_END` / `TOOL_RESULT_START` 附带脱敏后的 `inputPreview`。

## Workspace / Project

当前归属关系固定为 `Workspace -> Project -> Session -> Run`：Workspace 是资源和策略边界，Project 是目录、上下文和运行数据边界。

- 启动时创建 `default` Workspace / Project，并回填历史资源和运行记录。
- Chat 请求只传 `project_id`；Workspace 必须由 Project 反查，现有 Session 不允许跨 Project 继续。
- `runtime_context.py` 在 Runtime 创建前解析 Project root、instructions 和 ownership。
- Project root 与 Agent filesystem scope 取交集；不相交时拒绝执行。
- TeamRun、Subagent invocation、Memory、Trace、Domain Event 和 Projection stream 继承父运行归属。
- Project resource binding 已作为运行时白名单参与 Agent、Skill、MCP Server 和 HTTP Tool 裁剪；资源必须属于 Project 所在 Workspace，Project 只能收窄 Agent 配置。
- 已有 Project 升级时建立一次性资源基线；新建 Project 快照绑定创建时已有的 Workspace 资源，后续新增资源不会自动授权给既有 Project。
- 前端使用统一 Workspace / Project 上下文；Chat、Runs、Trace、Memory 和资源管理按当前上下文筛选，切换 Project 时清理 Chat 与 Team 运行投影。
- Workspace 与首个 Project 通过 bootstrap API 原子创建；正式 Artifact ownership 留在 Artifact Phase。
- Project 删除默认使用归档语义，不直接级联删除历史 Session、Run、Memory 或 Trace。

## AgentScope 原生 Skills

- 应用层继续负责安装、SQLite、Workspace / Project binding、Agent 配置、权限和 UI；AgentScope 负责运行时 catalog、渐进式披露和内置 `Skill` viewer。
- Toolkit 通过 `skills_or_loaders` 接收已裁剪的绝对 Skill 包目录；system prompt 只包含 `name`、`description` 和 `dir`，模型实际调用 `Skill` 后才读取完整指令。
- Skill 正文使用 `scripts/foo.py`、`references/bar.md` 等标准相对路径。模型根据 catalog 中的 `dir` 转成绝对路径后调用普通 Read、Grep、Glob 或 Bash；不使用 `$SKILL_DIR`，也不新增每脚本工具。
- Skill script 复用单一 `GuardedBash`；本地 execute 权限与 network 权限独立。Shell 黑名单仅是拦截明显误操作的提示级防护，不能抵抗命令混淆；真正的执行隔离依赖 DockerWorkspace 与权限审批。Skill 不突破 Agent 的 ToolScope、filesystem 或审批边界。
- 持久 DockerWorkspace 会把允许的 Skill 包 seed 到容器 `/workspace/skills`，catalog 披露容器路径；非持久 Docker sandbox 暂不支持 Skills，启动时返回明确错误。
- 单 Agent 和 Team Member 使用相同 Skill catalog；Team 协调、综合和恢复轮不开放业务 Skill。Subagent frontmatter `skills` 转换为裁剪后的 loader 路径，不再全文拼接。
- `SKILL_STARTED`、`SKILL_CONTEXT`、`SKILL_FAILED` 和 `SKILL_COMPLETED` 以真实 `Skill` viewer 工具调用为事实源。catalog 可见不创建 active `SkillRun`。
- zip 安装拒绝缺少 `name` / `description` 的 `SKILL.md`、路径穿越和符号链接；命名、目录名和未知 script 类型以 warning 展示。

## Team Runtime

Team Runtime 是一次性 `TeamRun`。用户在单 Agent 模式选择 Agent，切换到 Team 模式后沿用该 Agent 作为 Leader；Team 模式不显示 Team、Member 或 Leader 下拉框，也不读取持久 Team 配置。每轮运行在 `backend/data/team-runtime/runs/{team_run_id}/` 下生成全新 file-first 状态。

核心语义：

- Team Lead 是主协调会话，负责动态生成临时队友、拆任务和协调 Mailbox；最终答案由独立综合阶段生成。
- Teammate 是隔离 AgentScope Agent 会话，拥有独立上下文窗口，不继承 Lead 对话历史。
- 共享事实只允许通过 Task List、Mailbox、member directory、result artifacts 和 events/snapshots 表达。
- 用户可以向任意 Member 发送 mailbox 消息，作为本轮运行中的直接干预。
- Member 提交结果前必须清空未读 Mailbox；综合阶段使用完整 Mailbox，综合期间的新消息会触发重新综合。
- Team 领域状态仍以 file-first store 和领域事件为事实源；Chat 只读取 `kind=team` 投影 Block，不读取 Team 文件状态或顶层业务 SSE。

## 群聊工作室

群聊工作室是独立于 Chat Team Runtime 的跨多轮协作空间，不替换一次性 `TeamRun`。共用 Workspace / Project、Agent、Skill、MCP、HTTP Tool、沙箱、记忆注入、模型设置和 `src/notifications/`；不共用 `POST /api/chat/send`、Chat SSE、TeamRun / Mailbox / Team Block、`chat_messages`，也不把群事件写入 `run_events`。

### 隔离与入口

- 入口在 Chat 顶栏铃铛左侧和 `⌘⇧G` / `Ctrl+Shift+G`，520ms 重叠过渡切到独立 pane。不改 `WorkspaceMode`，不进 `ChatComposer` Agent/Team 分段，不是 TitleBar 第三 workspace。`TitleBar.MODE_OPTIONS` 保持 `workstation | workflow`。
- 后端前缀 `/api/group-studio`，领域包 `backend/src/group_studio/`。Chat handlers / Team Runtime 禁止 import `group_studio`；catalog 通过 `session_studio.py` 附加徽章。
- 开关 `json_store system_settings/group_studio`，默认开启；关闭后写/建接口 404 `group_studio_disabled`。关 flag 拒绝新 from-chat，已有 Run 继续到 pause/archive。
- SQLite `group_*` 表是领域事实源（含 `group_work_items`）。一 Chat session 对应一间未归档工作室；一群一张任务卡。产出在 `{project_root}/.agent-workstation/group-studios/{group_id}/`。
- 独立 SSE，契约见 `docs/group-studio-event-contract.md`。`GROUP_MESSAGE_DELTA` / `GROUP_ACTIVITY_DELTA` 不落 `group_studio_events`。禁止写入 Chat `run_events`、`RUNTIME_EVENT` 或 `PENDING_INTERACTIONS`。刷新恢复 = `GET /{id}` + `afterSeq`，不学 Workflow `replay=true`。
- Chat 活动 Run（含 `waiting_*`）禁止升级，后端 409 `chat_run_active`。切换带走 Workspace/Project、草稿、附件、选中 Agent，不搬气泡 / TeamRun / Plan / AskUser。
- 三个 ID 不得互冒：`chat_session_id`、`group_id`、`membership_id`。Host/Worker 工具上下文按 `group_id`（工人再加 `member_id`）单例；不要按阶段换 extra_tools 对象。
- 人数硬顶 6（含 Host）。静音切断上下文。Orchestrator / 黑板记录员是系统服务，不进群。Host 不进 Agents 页，不参与 MemoryAdmissionGate，`require_agent` 跳过 `group-studio-host` 与 `default`。

### 对象

| 对象 | 标识 | 职责 |
| --- | --- | --- |
| `GroupStudio` | `gstu_` | 一间工作室，绑定唯一未归档 `chat_session_id` |
| `TaskCard` | `gtask_` | Goal / 背景 / 约束 / DoD 真源 |
| `Membership` | `gmbr_` | Host 或 Worker 席位；Host 行 `agent_id` 恒为 `group-studio-host` |
| `Blackboard` | 1:1 `group_id` | 共识面；不含 DoD 副本，只投影 `taskCardId` |
| `GroupMessage` / `GroupRun` / `GroupApproval` / `GroupArtifact` | `gmsg_` / `grun_` / `gapv_` / `gart_` | 消息、一次串行生成、独立审批、共享文件 |
| `WorkItem` | `gwitem_` | 任务卡下可认领切片：`open` / `claimed` / `done` / `cancelled` |

P1 时间旅行对象不得命名为 `group_snapshots`，以免和 `GROUP_STUDIO_STATE` 口语撞车。

### 阶段机

```text
from-chat / 本地首句落库 → drafting（卡 draft，goal 空）
drafting → discussing：仅人类确认密度且 goal 非空
discussing → executing：group_start_execution
executing → reviewing：group_start_review
reviewing → closing → archived
确认后改 Goal/约束 → awaiting_human（restatement，同时清队列）
另有 paused / circuit_broken
```

- `from-chat` 一律 `drafting`；Chat 上一句是开场白，禁止写成 `TaskCard.goal`。写卡不切 `discussing`。`card.status=draft` 当且仅当 `studio.status=drafting`。
- `awaitingIntakeConfirm` 不是新 status，是 drafting 上的布尔。Host 可 `group_request_intake_confirm`，没有 `group_confirm_intake` 工具。离开 drafting 只有人类确认：`POST /confirm-intake` 或确认短语（去句末标点后 fullmatch：`你接手` / `开始` / `就这样` / `确认并开始` / `可以开始` / `就这么定` / `开始讨论` / `开始群聊` / `我们开始吧` / `就这么开始`）。短语但尚未提请 → 当普通消息派 Host。goal 空 → 422 `goal_required`。空 DoD 允许确认。paused 期间确认短语 resume 后不回溯执行。
- drafting 禁止 invite / assign / publish（409 `intake_not_confirmed`）；Worker 在 drafting 获 floor → `intake_worker_forbidden`。drafting 隐藏加人。drafting Host 轮次不计 `used_turns`。drafting 改卡不 restatement。confirm-intake 之后 `group_studio_has_runs` 恒为 true。
- 本地草稿 `gstu_local_*` 仅在用户说话或微调时落库；persist 禁止把 goal 当 `draftText` 传给 from-chat。
- 熔断只计 turn / 无进展；`max_cost_usd` 为 P1。空 DoD 可结项。进入执行必须调 `group_start_execution`，口头宣布无效。每轮注入最近 8 条可见消息。

### Floor、队列、认领

- Host floor：`drafting | discussing | executing | reviewing | awaiting_human`。Worker floor：`discussing | executing | reviewing`。`awaiting_human` 禁止派工人。讨论与执行全串行，`maxParallelWorkers=1`。没有 floor 不得 `reply()`。
- Host `group_assign_floor(memberIds[])` 写 FIFO；工人结束弹下一人，中间不回 Host。静音者不能被点名、不能认领。
- Host 可只发 `group_work_items`。认领轮成立 ⇔ Host 回合结束时队列为空且本回合发布了仍为 `open` 的未指派工作项。认领是原子 `UPDATE ... WHERE status='open'`。认领轮 `duty=claim` 任何终态不加 `no_progress_turns`。没有工作项、也不在队列/认领轮的工人不得 `run_worker_turn`。
- 人类插话 / pause / restatement：取消当前生成，清空队列与认领轮（已 claimed 未 done 保留）。
- `schedule_next` 只派工人。`after_host` 默认 stop；唯一 Host-after-Host 例外是本回合刚切 `executing`/`reviewing` 且未点名。工人队列与认领轮都空才发 `GROUP_SPEAKER_QUEUE kind=drained` 并派一轮综合 Host。
- Host：`include_default_tools=False` + 控场 allowlist。Worker：显式 allowlist，denylist `AskUser` / `FinalAnswerStart`。群内 Host/Worker 均 `PermissionMode.BYPASS`，不抄 Chat Agent 审批配置。`group_approvals` 仅在运行时仍打出 `REQUIRE_USER_CONFIRM` 时兜底。
- 通知 kind 前缀 `group_studio_`，点击打开工作室，不进 ResultModal。删除 Chat session 无论开关都归档绑定工作室。启动时对未收口 Run 按暂停收口。
- 前端群聊消息区与 Chat 共用 `ScrollToBottomButton` / `useMessageScroll`。真并行、时间旅行、群事件进 Trace 只写入 `docs/project-todo.md`。

## Event Stream 与前端主展示

Chat 主界面只依赖两个稳定产品边界：`RUNTIME_EVENT: RuntimeFrame` 与 DisplaySnapshot V4。

- `/api/chat/send/start` 创建后台 Run。
- `/api/chat/resume/start` 恢复 HITL / AskUser 外部执行暂停的原 Run。
- `/api/chat/runs/{run_id}/state` 返回 DisplaySnapshot V4。
- `/api/chat/runs/{run_id}/stream?afterSeq=N` 返回连续 tail；无法连续恢复时返回 Snapshot。

后端固定链路是 `SourceEvent -> DomainEvent -> ProjectionRunner -> per-kind projector -> BlockLifecycleCoordinator -> ProjectionStreamStore`。前端固定链路是 `decodeRuntimeFrame -> streamAdapter -> Block Tree reducer -> kind module/renderer`。同一 Run 使用全局 `stream_ordinal`、lease/fencing、周期 Snapshot + tail replay。

原则：

1. 前端通用层只理解 Block identity、父子关系、生命周期和 `seq`。
2. `Block.kind` 决定 reducer 与 renderer；`delta.patch` 合并语义属于对应 kind，不属于公共协议。
3. Message 本身也是 Block；Tool、Plan、Approval、Artifact、Subagent、Team、Context 都是嵌套 Block。`kind=context` 为 detached，Chat 顶栏读取占用与压缩阈值，消息树不渲染。
4. Snapshot、实时 Frame 和历史 replay 必须得到完全相同的 Block Tree。
5. 新增展示能力不修改 RuntimeFrame、传输层、通用 reducer 或恢复逻辑。
6. 线上不双写、不保留 fallback。heartbeat 使用 SSE comment，reset 直接发送新 Snapshot。

已删除：mask registry、`set / append / remove / clear` 公共字段协议、应用层 heartbeat/reset 事件、RunState v2、旧 `event_hub`、DisplayEvent、旧 adapter/reducer 和旧 Snapshot reader。SQLite 检测到旧 Chat 投影表或非 V4 Snapshot 时执行一次性运行域 reset，不转换旧数据。

稳定契约见 `docs/chat-sse-event-contract.md`。

## Plan Mode

当前 Plan Mode 不是旧 `runtime.kind=plan`。它基于 AgentScope middleware hooks 接入 ReAct：

- Chat Composer 可显式开启 Plan Mode；仅支持单 Agent，不进入 Team Runtime。
- `plan_enter` 创建持久化 Plan，会话由 `plans / plan_revisions / plan_steps / plan_events` 保存。
- `plan_write` / `plan_set_steps` 替换当前未提交 revision 的 Markdown 和结构化步骤。
- middleware 在起草、模型修订和重新规划期间只允许读取类工具与安全只读 Bash，拒绝变更型工具。
- `plan_request_review` 保存不可变 revision 并进入 `review_waiting`；底部 Composer 提供确认执行、自然语言修改和取消入口。
- 修改意见启动新的 ReAct continuation，由模型读取完整当前计划并生成下一版；前端不能直接编辑计划数据。
- 确认后新 Run 进入 `executing`，模型通过 `task_output` 实时更新步骤，全部步骤终止后调用 `plan_complete`。
- 执行中可用 `plan_request_replan` 回到只读规划，再次生成 revision 并等待审核。
- 每个 continuation 的首个 Plan 事件携带完整状态，刷新、重连和历史回放不依赖旧 Run 或进程内 Agent。

## 记忆系统 MVP

当前在线的是新记忆系统首版，不是旧 memory summarize：

- **运行前注入**：`memory/retrieval.py` 读取 user / agent / global Markdown 摘要和最近 30 天节点树；`run_context.py` 在 readiness 通过后注入主 Agent / Team Lead，隔离 Member session 使用本轮解析后的相同记忆上下文，并单独注册裁剪后的 AgentScope Skill catalog。
- **Subagent 注入**：`subagents/runtime_context.py` 把 frontmatter `skills` 指定的已安装启用 Skill 转换为 AgentScope loader 路径，不注入完整 Skill 正文；同时用 `subagent:{name}` 作为 agent memory owner 注入长期记忆摘要。`subagents/permission_policy.py` 执行 frontmatter `permissionMode` 并按父 Agent guardrails 上限收窄；`subagents/tool_policy.py` 基于父运行时真实工具池执行 `tools` / `disallowedTools` 裁剪，并同步限制传给子 Agent 的 MCP server 与知识库工具；审计通过 `SUBAGENT_CONTEXT` Runtime Event 记录 loadedSkills、missingSkillRefs、memoryAgentId、memoryInjected 和 permission requested / effective 摘要。
- **历史记忆**：底层 `memory_nodes` 只保存用户输入、Agent 最终输出和摘要索引，不保存工具调用过程；写入后同步到 `VectorStoreBackend` 的 `memory_nodes` collection。
- **Markdown 长期记忆**：user memory、agent memory、global preference memory 分别保存为 `user_memory.md`、`agent_memory.md`、`global_memory.md`，没有旧 `default.md` 兼容读取。
- **Memory 管理入口**：前端 Memory 页面首版已接入，用户记忆和全局记忆各只有一个管理入口；Agent 记忆按 Agent 选择，每个 Agent 对应一份 `agent_memory.md`；历史记忆按“Agent -> 时间节点 -> 具体记忆”三段式查看，底层使用 `memory_nodes`。页面已支持历史记忆禁用 / 删除 / 恢复、Skill 自进化队列操作、注册失败原因可视化、设置快照、召回质量摘要、召回命中相关性反馈和 `MEMORY_AUDIT` 审计详情。
- **Memory Wiki**：`MemoryWikiVaultSyncer` 可从三类 Markdown 记忆、`memory_nodes` 和已注册 Skills 手动生成 `backend/data/memory/wiki/` 只读 Obsidian-compatible 视图，并生成 `entities/` 实体页与 `entities/graph.md` 共现图；`/api/memory/wiki/graph` 提供结构化实体图谱给前端展示。Memory 页面可同步、查看图谱和生成结果、打开文件夹或通过 Obsidian 深链打开 vault。当前不作为运行时事实源，也不反向写回权威存储。
- **记忆查询工具**：普通 Agent 默认注入 `memory_node_tree` 和 `memory_node_query`，需要详情时由 Agent 主动查询；工具召回会写入 `MEMORY_AUDIT`，记录 query、filters、resultCount、命中 nodeId 和 retrievalQuality 质量指标。
- **运行后提炼**：`completion.py` 成功收尾后后台并行调用 `UserMemoryReflectionAgent`、`AgentMemoryReflectionAgent`、`GlobalMemoryReflectionAgent`，通过绑定文件的 `memory_read` / `memory_add` / `memory_update` / `memory_delete` 操作 Markdown 记忆，不阻塞主对话。
- **准入、记忆与召回质量**：`MemoryAdmissionGate` 当前是规则评分 + 可选模型评分增强，会输出 history / markdown / skill 三类评分、阈值、signals、模型评分、失败原因、脱敏摘要和脱敏类型统计；Memory 设置页可编辑准入阈值、模型评分开关和 Markdown 质量参数。Markdown 写入会做敏感信息过滤、近似去重、冲突合并、失败重试和 `# Summary` 压缩摘要；历史记忆查询会合并向量候选和 SQLite 关键词结果，再按查询词命中、向量分数、结构化反馈分、置顶、置信度、使用次数和日期做轻量重排，用户对命中 node 的相关 / 不相关反馈写入 `memory_retrieval_feedback` 表并同步写 `MEMORY_AUDIT` 追溯，Agent 工具查询会进入召回审计并带质量指标，运行前只注入 20 条摘要索引。
- **Skill 自进化现状**：已有 `SkillEvolutionAgent`、`skill_draft_propose` 草稿工具、router gate、workflow evidence、颗粒度 gate、变量流抽象、候选持久化、标准 Skill 注册、相似 Skill 合并和用户显式请求生成 Skill 的手动触发流；自动生成 / 手动注册 Skill 会写入运行时 `skills/{skill_name}/SKILL.md` 并支持按 Skill 名称导出 zip。Memory 页面已能查看和操作候选队列；注册后不自动绑定 Agent。
- **向量存储**：全局 `VectorStoreBackend` 抽象已支持 `disabled` / `zvec`，`VECTOR_STORE_PROVIDER` 仅首次初始化生效，之后按持久化 `VectorStorePolicy` 启动。

## 文件 Catalog

持久 Agent 定义的权威是 Workspace 目录 `<USER_DATA_ROOT>/workspaces/<id>/.agents/profiles/<id>.md`，不是 `agents` 表。系统 Agent 仍由代码提供，用户覆盖写在 `workspaces/_system/.agents/profiles/`。Skill 正文只来自包内 `SKILL.md`（sqlite `content` 不再保存正文）；项目 `.agents/skills` 默认可运行。系统包 `yinli-catalog` / `yinli-charts` / `word-documents` seed 到运行时 skills 目录。MCP / HTTP 工具定义是 Workspace/Project 下的 YAML，密钥只走 `secret_ref` / secret_store，禁止写进 YAML。Job 定义是 `.agents/jobs/<id>.md`；`scheduled_jobs` 的权威实例字段是 status / next_run_at / 连续失败 / 能力合同等，定义列只是派生缓存，读路径以文件覆盖。模型通过 Read/Edit 这些文件操作系统，不再注入 `ScheduleJob` / `PauseJob` 平行工具；Chat 会生成项目 `.agents/INDEX.md`。规格见 `docs/file-catalog-v1.md`。

## 存储

`backend/src/storage/database.py` 使用同步 `sqlite3 + RLock`。核心结构化表包括：

- `agents`
- `mcp_servers`
- `skills`
- `http_tools`
- `chat_sessions`
- `chat_messages`
- `chat_runs`
- `chat_run_events`
- `chat_run_states`
- `run_events`
- `memory_nodes`
- `workspaces`
- `projects`
- `project_resource_bindings`
- `subagent_invocations`
- `user_notifications`

历史开发库可能仍有 `memory_facts`、`memory_events`、`memory_candidates`，但旧记忆模块已删除，不再代表当前运行时能力。

桌面安装版把 Electron `userData` 固定为 `agent-workstation-desktop`。`config/runtime_paths.py` 是所有运行时可写路径的唯一入口；SQLite、Markdown / Wiki Memory、向量库、TeamRun、Skill 包、用户级 Subagent、持久沙盒、日志和默认 Workspace 都必须位于该根目录。macOS 对应 `~/Library/Application Support/agent-workstation-desktop/`，Windows 对应 `%APPDATA%\\agent-workstation-desktop\\`。安装目录和 PyInstaller `_MEI...` 临时目录只允许读取代码资源，不能保存用户数据。

## 配置与权限

- 启动期静态配置在 `config/settings.py`，环境变量前缀为 `AGENT_WORKSTATION_`。
- 模型配置和 Key 统一存于 `USER_DATA_ROOT/models.json`，由 `config/model_catalog.py` 校验、脱敏和原子保存。SQLite / Keychain / DPAPI / 环境变量仅在文件首次初始化时迁移，之后不再作为在线补充来源。Key 在本地文件中为明文，macOS 使用 0600，Windows 使用受限当前用户 ACL；不进入前端响应、Agent 导出或安装包。
- Chat/Sandbox 使用模型配置 ID；会话记住选择。Agent 仅保存 ReAct 上限。新 Run 固定完整供应商连接、模型参数和降级链；Team、Subagent、审批恢复和后处理继承该快照。定时任务按自己的 `modelConfigId` 或每次触发时的全局默认，工作室创建时固定模型配置 ID。
- Agent 运行时由 `agents/factory.py` 统一创建和缓存。
- 工具范围由 `RuntimeToolScope` 与 `tool_scope_resolver.py` 控制。
- 权限策略由 guardrails 归一化为统一权限模型，再映射到 AgentScope permission mode、工具注入和 Trace。
- `WebFetch` 是独立的只读公开网页工具：只接收已知 HTTP(S) URL，逐次校验重定向目标，拒绝私网、本机、保留地址、URL 凭据、超大响应和非文本内容，并把网页正文标记为不可信外部数据。它不复用 `HttpRequest` 的通用 API 语义，也不提供 WebSearch。

## 定时任务 Job Agent

定时任务写接口受 `system_settings/scheduled_jobs.enabled` 控制；开发库默认关闭。桌面安装版（`AGENT_WORKSTATION_DATA_DIR`）首次启动若从未写过该键，会默认打开调度和能力门禁。设置页可随时开关。

控制面与执行面拆开：Job 定义由 catalog 文件权威，Jobs 页 / REST 是同一棵文件树的门面；sqlite `scheduled_jobs` 保存运行实例。到点走领域 API `start_scheduled_chat_run()`，每次 firing 新建 `kind=scheduled_job` 的 Chat session，禁止复用登记会话。firing 去掉 AskUser / Plan Mode / DelegateTask。单 Agent Chat 不再注入 `ScheduleJob` 工具组。控制面默认 `notify`；预览发送、收件人白名单、`auto_send` 仍是后续阶段。

定时任务的到期执行者必须是 `role=job` 的 Job Agent，不能使用 Chat 助手。Job Agent 在配置里拆成必注入与可选注入两层工具：必注入到期一定带上，`CheckJobFeasibility` 只认领可选层。系统 `default` 助手不能改成 Job Agent；Job Agent 不出现在 Chat 选择器里。能力合同与创建前门禁见 `docs/scheduled-jobs-v2-agent-activation.md`。

系统启动时会为新旧数据库幂等补齐稳定 ID `system-websearch-job`（名称 `WebSearch`）作为默认 Job Agent。其主模型为 `deepseek-v4-flash`，必注入 `WebFetch` 与 `HttpRequest`，固定开启网络并使用 trusted tools 无人值守权限。该 Agent 跨 Workspace 可见且不依赖普通 Agent Project binding，不可删除或切换成 Chat 角色；它绑定的外部 Skill、MCP、HTTP Tool 仍必须满足当前 Project resource binding。名称不代表真正的开放式搜索能力，`WebFetch` 仍只读取已知公开 URL。

## 消息通知

通知是独立于 Chat Runtime 的通道抽象：调度器在 firing 终态构造 `NotificationMessage`，由 `src/notifications/dispatcher.py` 分发给已启用通道。

- 应用内通道写入 `user_notifications`，并通过 `/api/notifications/stream` 推送到 Chat 顶栏铃铛；不进入 Chat SSE。任务完成时 Web 弹出右上角提醒框，Electron 走系统通知。
- 通道目录与开关存在 `json_store` / `system_settings` / `notifications`。应用内通道始终开启。
- 邮箱、微信、飞书已登记占位通道，设置页可预填配置，但 `implemented=false` 时不能启用。
- 新增通道只需实现 `NotificationChannel` 并登记，不要把投递逻辑写进 `scheduler/service.py` 或 Chat handler。

## 前端入口

- `frontend/src/App.tsx`：页面映射、主题同步和健康检查。
- `frontend/src/pages/ChatPage.tsx`：Chat 主页面。
- `frontend/src/hooks/useEventStream.ts`：RuntimeFrame SSE 订阅入口。
- `frontend/src/features/runtime-stream/`：协议 decoder、stream adapter、Block Tree reducer、kind registry 和 renderer。
- `frontend/src/features/chat/*`：Chat 输入、消息布局和 HITL 命令组件。

## 开发原则

- 新 Chat 事件先更新 `docs/chat-sse-event-contract.md`。
- 新运行时能力优先接入 ReAct / middleware，并通过新的 per-kind projector 接入 Block Tree，不恢复旧 Runtime 或 RunState 分支。
- 文档只保留当前事实、稳定契约和明确待办；实验记录完成后应合并进 TODO 或删除。
