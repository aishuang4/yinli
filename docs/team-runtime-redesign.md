# Team Runtime 重设计

> 状态：设计草案。本文用于约束新版 Team Runtime 的产品语义、运行时抽象、文件状态、Task List、Mailbox 和分期边界。旧 Team Worker Selector / Assignment Planner / batch worker report 链路不得作为新版 Team 的实现基础。

## 1. 设计目标

新版 Team Runtime 的目标不是把固定 Worker 列表并行跑完后交给 Leader 汇总，而是提供一个会话级协作空间：

- Team Lead 是团队主会话，负责生成队友、维护任务、协调通信并汇总结果。
- Teammate 是本轮 TeamRun 临时实例化的独立 Agent session，有自己的上下文窗口、工具权限、技能和消息收件箱。
- Task List 是所有成员共享的任务状态源，支持依赖、自动解锁和原子领取。
- Mailbox 是团队内部即时通信系统，支持指定队友消息、广播、结果汇报和空闲通知。
- Team 状态优先落在本地文件中，SQLite 只作为可重建索引、RunState 投影和历史查询加速。
- Team 是一次性的运行时协作空间。每次 Team 模式启动都会创建新的 TeamRun；TeamRun 结束后团队实例关闭，只保留文件记录、历史投影和可选沉淀出的 teammate preset，不保留长期在线团队。
- 用户可以主动和 TeamRun 中的任一 teammate 对话。用户消息作为 `from=user` 的 mailbox message 写入 TeamRun，并按收件人即时投递和唤醒，不要求 Team Lead 代为转述。
- 成员 session 必须相互隔离。成员之间不能共享完整对话历史或私有上下文，只能通过 Mailbox、Task List、成员目录和成果文件交换信息。

## 2. 非目标

新版 Team 不继续以下旧思路：

- 不在前端发送前做 Worker 预选确认。
- 不使用 `TeamWorkerSelector` 裁剪成员。
- 不使用 `TeamAssignmentPlanner` 一次性分派所有任务。
- 不把静态 `team_members` 当成本轮运行时 Team。
- 不把 Worker 输出汇总当成 Team 协作的唯一语义。
- 不继续扩展 `TEAM_ACTIVITY` / `TEAM_MEMBER_EVENT` 作为新 Team 主事件。

静态 Agent / Team 配置后续可以降级为 teammate preset / role template，只提供可复用角色定义，不代表运行时团队实例。

## 3. 核心组件

### 3.1 TeamRun

TeamRun 是一次会话级团队运行，绑定 Chat session / background run。TeamRun 是一次性的 runtime 状态，不等同于现有 `teams` 表里的静态 Team，也不作为长期团队配置复用。继续同一 Chat session 发起新的 Team 协作时，也应创建新的 TeamRun，而不是复用上一轮团队实例。

关键字段：

```json
{
  "teamRunId": "teamrun_123",
  "sessionId": "chat_456",
  "chatRunId": "run_789",
  "goal": "评估 Team Runtime 重构方案",
  "status": "running",
  "leadTeammateId": "lead",
  "sourceTemplateId": null,
  "createdAt": "2026-07-08T00:00:00+08:00",
  "updatedAt": "2026-07-08T00:00:00+08:00",
  "policy": {
    "maxTeammates": 4,
    "coordinationOnlyLead": true,
    "broadcastRequiresApproval": true,
    "maxAutoWakePerTick": 2
  }
}
```

### 3.2 Team Lead

Team Lead 是 TeamRun 的主控 Agent。它可以执行任务，但默认推荐支持 `coordinationOnlyLead`，让 Lead 专注于协调和综合。

Lead 职责：

- 根据用户目标判断是否需要 teammate。
- 创建 teammate 或从 preset 实例化 teammate。
- 创建任务、设置依赖、显式分配任务。
- 通过 Mailbox 追问、广播、审查结果。
- 读取任务状态和消息，决定继续协调或最终回答。

Lead 可用工具：

```text
team_create_teammate
team_list_teammates
team_create_task
team_assign_task
team_read_tasks
team_send_message
team_broadcast
team_read_mailbox
team_review_task
team_complete_team_run
```

### 3.3 Teammates

Teammate 是每次 TeamRun 临时创建的独立 Agent session。长期保存的是 teammate preset / Agent 定义；运行时参与协作的是 TeammateSession。

Teammate 创建来源：

- 基于现有 Agent：只读取能力画像、默认模型、工具权限、skills、MCP、知识库配置等能力边界；不直接继承完整 system prompt。
- 基于 teammate preset：只读取预定义角色说明、适用场景和能力边界；不把 preset prompt 原样作为本轮 teammate prompt。
- Lead 临时生成：根据当前 TeamRun goal、任务拆解和协作需要生成本轮 teammate 的 runtime prompt，只在本轮 TeamRun 内存在，结束后可由用户选择是否沉淀为 preset。

Teammate runtime prompt 必须动态生成。现有 Agent / preset 只提供材料和约束，Lead 创建 teammate 时需要输出本轮专用的 `rolePrompt` / `startupPrompt`：

```text
runtimePrompt = 当前 TeamRun goal
  + Lead 对该 teammate 的本轮职责定义
  + 可用工具和权限边界
  + 必须遵守的协作规则
  + 初始任务或 mailbox 指令
```

这样可以避免把长期 Agent 的宽泛系统提示词、历史角色假设或不相关任务偏好带入本轮协作。

Teammate 上下文规则：

- 加载项目上下文、AGENTS.md、MCP servers、enabled skills、记忆摘要和本轮动态 runtime prompt。
- 加载 TeamRun goal、自己的任务、未读 mailbox、相关 task/thread 摘要。
- 不继承 Team Lead 的完整对话历史。
- 每个 teammate 维护独立 session key、HITL 状态、工具调用历史和消息阅读状态。

#### 隔离与共享边界

Team 内每个成员的 session 必须隔离：

- 私有上下文：成员自己的对话历史、思考过程、工具调用细节、HITL 中间状态。
- 共享状态：TeamRun goal、成员目录、Task List、Mailbox、任务 brief/result、events 和 snapshots。

成员之间不能直接读取彼此的完整 session 或隐式共享上下文。所有跨成员协作必须显式写入共享状态：

```text
teammate A 私有上下文
  -> team_send_message / team_submit_result
  -> Mailbox / Task result
  -> teammate B 读取 mailbox/task
  -> teammate B 私有上下文
```

用户直接介入 teammate 时，也遵循同一规则：用户消息进入目标 teammate 的 mailbox，只影响目标 teammate 的后续上下文；如果该消息改变团队目标、约束或关键事实，目标 teammate 需要通过 mailbox 通知 Lead。

Teammate 可用工具：

```text
team_list_teammates
team_read_tasks
team_claim_task
team_claim_next_task
team_update_task
team_send_message
team_broadcast
team_read_mailbox
team_submit_result
team_request_help
```

### 3.4 Task List

Task List 是 TeamRun 的共享工作队列。任务主状态保持简单三态：

```text
pending
in_progress
completed
```

依赖和可领取状态由系统派生：

```text
claimable = status=pending && all(depends_on completed)
blocked = status=pending && any(depends_on not completed)
```

任务文件由 JSON 状态和 Markdown 内容组成：

```text
tasks/task-{task_id}/
  state.json
  brief.md
  result.md
  claim.lock
```

`state.json` 示例：

```json
{
  "id": "task_1",
  "teamRunId": "teamrun_123",
  "title": "梳理旧 Team Runtime 入口",
  "status": "pending",
  "assignedTeammateId": null,
  "ownerTeammateId": null,
  "dependsOn": [],
  "priority": 10,
  "createdByTeammateId": "lead",
  "claimedAt": null,
  "completedAt": null,
  "createdAt": "2026-07-08T00:00:00+08:00",
  "updatedAt": "2026-07-08T00:00:00+08:00"
}
```

#### 任务领取

任务领取必须是原子的，避免多个 teammate 同时领取同一任务。file-first 实现使用锁文件：

```text
tasks/task-{task_id}/claim.lock
```

领取流程：

1. 使用原子创建方式创建 `claim.lock`。
2. 创建成功后重新读取 `state.json`，校验任务仍为 `pending` 且依赖已完成。
3. 将任务状态改为 `in_progress`，写入 `ownerTeammateId` 和 `claimedAt`。
4. 使用临时文件 + 原子替换写回 `state.json`。
5. 锁文件记录 owner 和 expiresAt，崩溃后可由 coordinator 回收过期锁。

`team_claim_next_task` 按以下顺序选择任务：

```text
status=pending
assignedTeammateId IS NULL
ownerTeammateId IS NULL
dependencies completed
ORDER BY priority DESC, createdAt ASC
```

字段语义：

- `assignedTeammateId`：Lead 预分配给某个 teammate，但任务可能仍在等待依赖。
- `ownerTeammateId`：成功领取并进入 `in_progress` 的执行者。
- 自主领取只选择 `assignedTeammateId IS NULL` 的任务；被显式分配的任务由 Coordinator 在依赖满足后唤醒指定 teammate。

#### 自动解锁

当任务完成时，TeamCoordinator 必须自动检查依赖该任务的 pending tasks：

```text
team_submit_result(taskId)
  -> 写 result.md
  -> 标记 task completed
  -> 查找 dependsOn 包含 taskId 的任务
  -> 重新计算 claimable
  -> 对新解锁任务发送 TEAM_TASK_UNLOCKED 事件
  -> 唤醒可领取任务的 idle teammate 或通知 Lead
```

### 3.5 Mailbox

Mailbox 是 TeamRun 内部即时通信系统，不是普通 chat message，也不是内存队列。Mailbox 必须同时满足：

- 消息持久化可恢复。
- 指定消息即时投递，不依赖 Lead 轮询。
- 前端可观察。
- 可按 teammate、task、thread 查询。

MVP 采用一条 JSONL 消息流：

```text
mailbox/
  messages.jsonl
  threads/
    thread-{thread_id}.md
```

`messages.jsonl` 示例：

```jsonl
{"id":"msg_1","teamRunId":"teamrun_123","from":"lead","to":"tm_researcher","kind":"task_assignment","taskId":"task_1","content":"请梳理旧入口。","createdAt":"2026-07-08T00:01:00+08:00"}
{"id":"msg_2","teamRunId":"teamrun_123","from":"tm_researcher","to":"lead","kind":"result","taskId":"task_1","content":"结果见 tasks/task_1/result.md","createdAt":"2026-07-08T00:05:00+08:00"}
```

消息字段：

```json
{
  "id": "msg_1",
  "teamRunId": "teamrun_123",
  "taskId": "task_1",
  "threadId": "thread_1",
  "replyToId": null,
  "from": "lead",
  "to": "tm_researcher",
  "toScope": "teammate",
  "kind": "message",
  "subject": "请补充证据",
  "content": "你提到 runtime_builder，请给出文件位置和依据。",
  "payload": {},
  "createdAt": "2026-07-08T00:02:00+08:00"
}
```

`messages.jsonl` 是 append-only 原始消息流，不直接原地更新 `readBy` / `actedBy`。阅读和处理状态通过独立 delivery 状态文件或事件流记录，避免重写 JSONL 导致并发写入冲突。

MVP delivery 状态：

```text
mailbox/
  messages.jsonl
  deliveries/
    msg-{message_id}.json
```

`deliveries/msg-{message_id}.json` 示例：

```json
{
  "messageId": "msg_1",
  "recipients": [
    {
      "teammateId": "tm_researcher",
      "status": "delivered",
      "deliveredAt": "2026-07-08T00:02:01+08:00",
      "readAt": null,
      "actedAt": null
    }
  ]
}
```

消息类型：

```text
message
task_assignment
question
result
review
warning
idle_notice
approval_request
user_message
```

发送规则：

- `message`：向指定 teammate 发送消息，写入 JSONL 后立即投递并唤醒目标。
- `broadcast`：向全体发送消息。受 `broadcastRequiresApproval` 和 `maxAutoWakePerTick` 约束，MVP 可只推送 UI，不自动唤醒所有 teammate。
- `result`：teammate 提交结果时自动发送给 Lead。
- `idle_notice`：teammate 无任务、无待处理消息时自动发给 Lead。
- `approval_request`：发给 user，转成 HITL / approval UI。
- `user_message`：用户直接发送给某个 teammate 或 Lead 的消息，写入 mailbox 后即时投递；如果目标 teammate idle，应自动唤醒处理。

工具：

```text
team_send_message(to, content, kind?, taskId?, subject?, replyToId?)
team_broadcast(content, kind?, taskId?, subject?)
team_read_mailbox(unreadOnly?, taskId?, threadId?, limit?)
team_mark_message_read(messageId)
team_mark_message_acted(messageId, note?)
team_reply_message(messageId, content)
```

### 3.6 TeamCoordinator

TeamCoordinator 是系统服务，不做语义决策，只负责状态变更后的机械调度：

- 监听 `TEAM_MAIL_POSTED`，将消息即时投递到目标 teammate inbox。
- 监听 `TEAM_TASK_COMPLETED`，自动解锁依赖任务。
- 监听 teammate idle，给 Lead 写 `idle_notice`。
- 根据 policy 唤醒 idle teammate。
- 重启恢复时扫描 TeamRun 文件目录，重建内存调度状态。

Coordinator 不替 Lead 选择角色、不替 Lead 拆任务、不替 teammate 写结论。

## 4. File-first 状态布局

Team Runtime v2 使用文件作为 source of truth。SQLite 只用于可重建索引、RunState 投影、Trace 查询和历史列表加速。

目录结构：

```text
backend/data/team-runs/{team_run_id}/
  config.json
  members/
    lead.json
    teammate-{teammate_id}.json
  tasks/
    task-{task_id}/
      state.json
      brief.md
      result.md
      claim.lock
  mailbox/
    messages.jsonl
    deliveries/
      msg-{message_id}.json
    threads/
      thread-{thread_id}.md
  events.jsonl
  snapshots/
    latest.json
```

写入规则：

- JSON 状态文件必须使用临时文件 + 原子替换。
- JSONL 追加后必须同步发 runtime event。
- 锁文件必须包含 owner、createdAt、expiresAt。
- Markdown 仅存长文本说明、报告和讨论摘要；机器状态不得只存在 Markdown 中。
- 每个 TeamRun 目录应可独立打包、复现、导出。

SQLite 使用边界：

- 可以记录 `team_run_id -> chat_run_id/session_id` 索引。
- 可以存 DisplaySnapshot / RunState 投影。
- 可以缓存搜索索引。
- 不作为 Task / Mailbox / Teammate runtime 状态的唯一事实源。

## 5. Runtime 流程

### 5.1 启动

```text
用户选择 Team 模式发送消息
  -> readiness / intent_router 仍决定 runtime.kind=react
  -> executor.kind=team
  -> 创建 TeamRun 文件目录
  -> 创建 Lead teammate session
  -> 给 Lead 注入 Team tools 和 Team policy
  -> Lead 开始协调
```

### 5.2 创建 teammate

```text
Lead 调用 team_create_teammate
  -> policy 校验 maxTeammates / approval
  -> 从 Agent / preset / generated role 创建 member json
  -> 创建独立 AgentScope session key
  -> 发 TEAMMATE_CREATED
  -> 可选唤醒 teammate
```

### 5.3 分配和领取任务

```text
Lead 创建 task
  -> 写 tasks/task-x/state.json 和 brief.md
  -> 发 TEAM_TASK_UPSERTED
  -> 若无依赖且指定 assignedTeammateId，投递 task_assignment 消息

Teammate 调用 team_claim_next_task
  -> 原子创建 claim.lock
  -> 校验依赖
  -> 标记 in_progress
  -> 发 TEAM_TASK_CLAIMED
```

### 5.4 完成任务和自动解锁

```text
Teammate 调用 team_submit_result
  -> 写 result.md
  -> 标记 task completed
  -> 写 result mailbox 给 Lead
  -> Coordinator 解锁下游任务
  -> 发 TEAM_TASK_COMPLETED / TEAM_TASK_UNLOCKED / TEAM_MAIL_POSTED
```

### 5.5 Mailbox 即时投递

```text
team_send_message
  -> append mailbox/messages.jsonl
  -> emit TEAM_MAIL_POSTED
  -> Coordinator 按收件人唤醒 teammate 或通知 user/Lead
```

### 5.6 用户直接对话 teammate

用户可以在 Team Console 中选择某个 teammate 并发送消息。该消息不进入普通 assistant/user 主对话链路，而是进入 TeamRun mailbox：

```text
用户选择 teammate
  -> 写 mailbox message: from=user, to=teammate, kind=user_message
  -> emit TEAM_MAIL_POSTED
  -> Coordinator 将消息投递到 teammate inbox
  -> 如果 teammate idle，立即唤醒该 teammate
  -> teammate 回复时写回 mailbox，可选择回给 user、Lead 或其他 teammate
```

约束：

- 用户消息必须保留在 TeamRun 文件记录中，便于恢复和审计。
- 用户直接指令只影响目标 teammate 的后续上下文，不自动污染 Lead 或其他 teammate 的完整上下文。
- 如果用户消息改变了团队目标或约束，目标 teammate 应通过 mailbox 通知 Lead。

## 6. Runtime Event 契约草案

新增事件必须先进入 `docs/chat-sse-event-contract.md`，再实现后端和前端消费。

事件家族：

```text
TEAM_RUN_STARTED
TEAM_RUN_UPDATED
TEAM_RUN_COMPLETED
TEAM_RUN_FAILED
TEAMMATE_CREATED
TEAMMATE_STATUS_CHANGED
TEAMMATE_IDLE
TEAM_TASK_UPSERTED
TEAM_TASK_CLAIMED
TEAM_TASK_COMPLETED
TEAM_TASK_UNLOCKED
TEAM_MAIL_POSTED
TEAM_MAIL_READ
TEAM_MAIL_ACTED
TEAM_APPROVAL_REQUIRED
```

旧事件边界：

- `TEAM_ACTIVITY` 只保留历史消息兼容展示。
- `TEAM_MEMBER_EVENT` 只保留旧运行记录回放兼容。
- 新 Team Runtime 不发送上述旧事件表达新语义。

## 7. 前端 Team Console

新版前端不再展示发送前 Worker 确认 Dock，而是展示 Team Console：

- Teammates：成员列表、状态、当前任务、最后消息、可打开独立过程面板。
- Task List：任务、依赖、owner、claimable / blocked 派生状态、结果入口。
- Mailbox：全局消息流、teammate inbox、task thread、用户回复入口。
- Approval：创建 teammate、broadcast、高风险计划和工具权限审批。

用户介入能力：

- 批准或拒绝创建 teammate。
- 给某个 teammate 直接发送消息。
- 打开 teammate inbox / task thread，与该 teammate 进行连续对话。
- 回复 approval_request。
- 要求 Lead 汇总当前结果。
- 结束 TeamRun。

## 8. 分期计划

### M1：最小闭环

- File-first TeamRun 目录和状态读写。
- Lead teammate session。
- `team_create_teammate`。
- `team_create_task` / `team_assign_task` / `team_claim_task` / `team_submit_result`。
- Mailbox 指定消息和 result 消息。
- Coordinator 支持任务完成后自动解锁。
- 前端显示 teammates / tasks / mailbox 基础面板。
- 旧 Team Runtime 入口不再参与新 Team。

### M2：自主领取和空闲通知

- `team_claim_next_task`。
- teammate idle 检测和 `idle_notice`。
- 指定消息即时唤醒 teammate。
- 用户直接给 teammate 发消息。
- 用户与 teammate 的 mailbox thread 连续对话。
- TeamRun 重启恢复。

### M3：广播、审批和角色沉淀

- `team_broadcast` 和 broadcast approval policy。
- teammate 创建审批和高风险任务计划审批。
- generated teammate 结束后保存为 preset。
- TeamRun 导出和复现。

## 9. 实现守则

- 新 Team 只能围绕 TeamRun / TeammateSession / Task List / Mailbox / TeamCoordinator 建模。
- AgentScope 只作为单个成员的大脑和工具运行底座：复用 Agent、AgentState/session 隔离、reply_stream、Tool、Middleware、permission、HITL、模型 fallback 和事件流能力。
- Team 制度由本项目自研 TeamRuntime 实现：TeamRun 文件状态、Task List、Mailbox、任务锁、自动解锁、成员唤醒、用户直接介入和前端 Team Console 均归本项目负责。
- 不采用 AgentScope 内置 TeamCreate / AgentCreate / TeamSay 作为新版 Team Runtime 的主产品语义。可以参考其 message bus / wakeup / session 隔离实现，但不能让其 TeamRecord 或 worker-report 拓扑成为 source of truth。
- 选型边界：AgentScope 负责“成员如何思考和使用工具”，本项目 TeamRuntime 负责“团队如何组建、协作、共享任务和通信”。
- 不允许从旧 `team_worker_selector.py`、`team_assignment_planner.py`、`team_worker_runtime.py` 继续演进新版 Team。
- 不允许新增对 `TEAM_ACTIVITY` / `TEAM_MEMBER_EVENT` 的主链路依赖。
- 新增 Chat SSE 事件前必须先更新 `docs/chat-sse-event-contract.md`。
- 涉及 Team Runtime 的代码必须补回归测试，至少覆盖任务依赖解锁、原子领取、mailbox 投递和 TeamRun 恢复。
