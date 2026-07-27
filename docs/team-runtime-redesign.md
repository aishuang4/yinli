# Team Runtime 设计

> 状态：当前在线架构。Team 功能已硬切换到本文定义的架构，不读取、不投影、不兼容任何旧 Team runtime、旧事件或旧前端 actor 数据。

## 1. 产品语义

Team 是一次性协作运行：用户先在单 Agent 模式选择 Agent，再切换到 Team 模式；所选 Agent 成为本轮 Leader，Team 模式隐藏所有成员选择器，系统创建一个全新的 `TeamRun`。Leader 在本轮动态组建临时成员、创建共享任务并通过 Mailbox 协调；成员拥有隔离的 Agent session，只通过共享 Task List、Mailbox 和任务结果交流。全部任务收敛后，由独立综合阶段生成唯一用户答案。

必须满足以下不变量：

1. 每次用户请求创建新的 `TeamRun`，同一 Chat session 也不得复用上轮成员或 Mailbox。
2. Leader 与每个 Member 的 Agent session 相互隔离，不共享对话历史或私有工具过程。
3. 跨成员信息只能通过 Task List、Mailbox 和任务结果传递。
4. Chat 主回答区只显示最终综合答案；成员过程通过 Team Block 子树进入 Team Console。
5. Chat 展示只使用 `RUNTIME_EVENT` 和 `DisplaySnapshot V4`；Team 不维护独立 SSE 或前端状态树。
6. Team 文件状态是领域事实源；SQLite Domain Event、RuntimeFrame 和 DisplaySnapshot 是可重建的展示投影。
7. 一个 Member 同时最多执行一个任务；一个任务同时最多有一个 owner。
8. 任务只有在非空结果成功持久化后才能进入 `succeeded`。
9. 依赖任务只有在全部上游任务 `succeeded` 后才可调度。
10. 未进入终态的任务存在时，TeamRun 不得进入 `completed`。
11. Member 提交结果前必须读取全部入站 Mailbox 消息；存在未读消息时提交必须失败。
12. 综合期间 Mailbox 发生变化时必须丢弃当前草稿并基于最新消息重新综合。
13. 后端启动时发现仍为 running 的孤儿 TeamRun，必须明确收口为 cancelled，并同步 ChatRun 和 DisplaySnapshot V4；不得伪装为可续跑。
14. TeamRun 文件按保留天数和最大记录数清理；删除 Chat session 时同步删除其终态 TeamRun 文件。
15. Team 模式不选择持久 Team，也不接收固定成员列表；Chat 请求只携带 `mode=team` 和 Leader 的 `agent_id`。
16. Chat session、后台 Run、重跑和 RunState 只能通过显式 `mode` 判断 Team，不得通过 `teamId`、`teamName` 或成员数组推断。

## 2. 组件边界

```text
Chat Runtime
  -> TeamRunService
       -> TeamStore
       -> LeaderSessionRuntime
       -> TeamCoordinator
            -> MemberSessionRuntime × N
  -> SynthesisRuntime
  -> FINAL_ANSWER_DELTA*
  -> FINAL_ANSWER_REPLACE（一致性校正）

Team events.jsonl（领域事实 / outbox）
  -> TEAM_DOMAIN_EVENT（仅进程内源信号）
  -> Chat Domain Event
  -> Team per-kind projector
  -> RUNTIME_EVENT
  -> DisplaySnapshot V4
  -> Team Console selectors / renderer
```

### TeamRunService

负责 TeamRun 生命周期，不执行成员语义决策：

- 创建本轮目录和初始状态。
- 启动 Leader 协调 session。
- 启动 Coordinator 并等待任务收敛。
- 启动独立综合 session。
- 输出 Team 事件和最终答案。

### LeaderSessionRuntime

Leader 只拥有 Team 控制工具：

```text
team_spawn_member
team_list_members
team_create_task
team_read_tasks
team_send_mail
team_read_mailbox
team_finish_coordination
```

协调阶段不使用 `FinalAnswerStart`，也不拥有普通 Write、Edit、Bash、HTTP、知识库或 MCP 工具。`team_finish_coordination` 只有在至少存在一个 Member 和一个任务时才能成功。
`team_finish_coordination` 成功后 Runtime 必须立即终止 Leader session 并启动 Coordinator，禁止 Leader 在协调 session 内轮询任务或 Mailbox。成员间通信前置条件必须建模为显式任务依赖，不得创建“A 等 B 消息、B 的任务又依赖 A 完成”的隐性环。

### MemberSessionRuntime

每个 Member 使用唯一 `sessionKey` 和 runtime Agent cache key。Member 不继承 Leader 对话历史，也不使用 Leader 专属流处理器。可用协作工具：

```text
team_read_tasks
team_read_mailbox
team_send_mail
team_submit_result
```

Member 的普通模型、工具和思考事件转换为 Team domain event，只进入 Team Console。任务结果必须通过 `team_submit_result` 提交，普通文本不自动提升为任务结果。`team_read_mailbox` 会原子写入 read receipt；`team_submit_result` 检测到未读入站消息时拒绝提交，保证用户或其他成员的新消息不会被静默越过。

### TeamCoordinator

Coordinator 是机械状态机：

- 计算依赖满足的 queued tasks。
- 原子领取任务并分配给空闲 Member。
- 在并发上限内启动多个 Member session。
- 成员完成后立即重新计算依赖和可执行任务。
- 没有可执行任务但仍有 queued/running task 时按阻塞或失败收口。
- 不创建角色、不拆任务、不编写结果。

## 3. 状态机

TeamRun：

```text
created -> coordinating -> executing -> synthesizing -> completed
                    \          \              \
                     ----------------------------> failed
created/coordinating/executing/synthesizing -> cancelled
```

Member：

```text
created -> idle -> running -> idle
                    |  \
                    |   -> waiting
                    -> failed
idle/running/waiting -> stopped
```

Task：

```text
queued -> running -> succeeded
   |         |
   |         -> failed
   -> cancelled
```

`blocked` 是派生状态，不写入任务主状态：

```text
blocked = status == queued && any(dependency.status != succeeded)
claimable = status == queued && all(dependency.status == succeeded)
```

## 4. 文件状态

新运行时只使用以下目录，不扫描旧目录：

```text
backend/data/team-runtime/runs/{teamRunId}/
  run.json
  members/
    {memberId}.json
  tasks/
    {taskId}/
      task.json
      brief.md
      result.md
      claim.lock
  mailbox/
    messages.jsonl
    deliveries/
      {messageId}.json
  events.jsonl
  snapshot.json
```

JSON 状态使用临时文件和原子替换。JSONL 只追加。Task claim 使用独占锁文件。`snapshot.json` 可由其他状态文件重建。

## 5. Team 事件

TeamStore 继续把不可变领域事实追加到 `events.jsonl`。这些事件是内部 outbox，不能直接作为 SSE 发送，也不能携带完整 Team snapshot。统一结构：

```json
{
  "id": "tevt_xxx",
  "seq": 12,
  "teamRunId": "trun_xxx",
  "chatRunId": "run_xxx",
  "type": "task.completed",
  "actorId": "member_xxx",
  "taskId": "task_xxx",
  "status": "succeeded",
  "phase": "executing",
  "timestamp": "2026-07-10T00:00:00Z",
  "dataVersion": 1,
  "counts": {"tasks": 3, "running": 1, "succeeded": 2},
  "updatedAt": "2026-07-10T00:00:00Z",
  "payload": {
    "title": "检查后端",
    "resultPreview": "发现三个问题"
  }
}
```

稳定事件名：

```text
run.created
run.completed
run.failed
run.cancelled
member.spawned
member.status.changed
member.output.delta
member.thinking.delta
member.tool.started
member.tool.completed
member.tool.failed
member.tool.denied
task.created
task.claimed
task.completed
task.failed
task.cancelled
task.unlocked
mail.sent
mail.read
mail.acted
```

Member session 不开放单 Agent HITL。遇到权限确认或外部执行事件时，Runtime 自动返回拒绝结果并恢复同一 session，同时记录 `member.tool.denied`；连续请求超过保护上限才判定该 Member 失败。

Team 不继承普通 Agent 的 `maxReactIters`。Leader 协调固定 16 轮，Member 主执行固定 32 轮，综合固定 8 轮。Member 首次耗尽时在同一 Agent session 追加一次 8 轮恢复，恢复轮只保留 `team_read_tasks`、`team_read_mailbox`、`team_send_mail`、`team_submit_result`，要求基于已有证据立即提交；恢复轮再次耗尽才失败。

每个事件 payload 必须包含 projector 所需的白名单字段，projector 禁止回读 Team 文件补充展示数据。`TeamEventPublisher` 只按 `seq` 转发事件，不再为每条事件计算或附加 `snapshot`。Chat normalizer 将其翻译为带 `team.` 前缀的 Domain Event，并由单一 ProjectionRunner 按 `stream_ordinal` 串行消费。

Team Block 子树固定为：

```text
message:{chatRunId}
├── team:{teamRunId}
│   ├── team-member:{teamRunId}:{memberId}
│   ├── team-task:{teamRunId}:{taskId}
│   │   ├── team-activity:{teamRunId}:{activityId}
│   │   └── tool:team:{teamRunId}:{toolCallId}
│   └── team-mail:{teamRunId}:{messageId}
└── text:{chatRunId}
```

- `team` 只保存 Run 状态、阶段、计数、时间和失败摘要。
- `team-member` 保存 Member 身份、角色、状态和当前任务。
- `team-task` 保存 Task 依赖、owner、状态、brief/result 摘要。
- `team-activity` 保存 Member 输出、思考和迭代恢复信息；文本 delta 只追加到对应 activity。
- `team-mail` 保存单条 Mailbox 消息和 delivery 状态。
- Team 工具调用复用 `tool` kind，并挂在所属 task 下；协调阶段工具挂在 Team 根节点下。
- Task 固定挂在 Team 根节点，owner 变化只更新数据，不改变 parent。
- Team 进入终态时必须按 tool/activity -> task/member -> team 的顺序关闭 Block，之后才允许 Run completion barrier 关闭 message 并发送 done。若 `task.completed` 先于 `team_submit_result` 的 `member.tool.completed` 到达，投影先更新 task 并延迟 close，直到最后一个已启动 tool 关闭；task 领域终态后的迟到 activity 或新 tool 不再创建展示 Block。同一 `toolCallId` 的 `member.tool.denied -> member.tool.failed` 等重复终态按幂等事件处理，首个终态关闭后不得重新 open Tool Block。
- 综合 session 的 `TEXT_BLOCK_DELTA` 同时写入 Leader 的 `team-activity` 和消息直属 `text` Block；最终 replace 只做一致性校正，禁止等综合完成后一次性展示回答。

## 6. 前端边界

前端不建立独立 Team 状态。Team Console 只从当前 `DisplaySnapshot V4` 的 Team Block 子树派生：

```text
RUNTIME_EVENT
  -> blockTreeReducer
  -> registry[kind].reduce
  -> Team selectors
  -> Team summary / Team Console
```

约束：

- 用户在单 Agent 模式选择 Agent；切换到 Team 模式后沿用该 Agent 作为 Leader，并隐藏选择器。Team 模式禁止出现 Team、Member 或 Leader 下拉框。
- Chat 和 Sandbox 请求只发送 `mode`、`agent_id`、`agent_name` 及普通运行参数，禁止发送 `team_id`、`team_name`、`team_members`。
- `useEventStream` 只维护一个 `displaySnapshot`，不能新增 `teamConsole` 平行状态。
- 通用 reducer 只理解 Block identity、parent、生命周期和 kind registry，不识别 Team 业务事件。
- Team Console 使用只读 selectors 读取 `team/team-member/team-task/team-activity/team-mail/tool` Block。
- Chat 内的 Team 摘要同样通过 selectors 实时展示成员、任务和最新活动，并用局部动画表达运行状态；不得只显示根计数并把全部实时信息藏到终态 Console。
- 历史 Assistant message 只使用 `displaySnapshot: DisplaySnapshot V4` 恢复 Team Console。
- 切换成员只改变 Team Console 的本地选中态，不重放事件流。
- 用户通过 `POST /api/chat/runs/{runId}/team/mail` 向 active TeamRun 成员投递消息；HTTP 响应不是 UI 状态，写入后的 `team-mail` RuntimeFrame 回流才更新界面。
- Mailbox 的 `recipient=broadcast` 表示广播。用户广播投递给包括 Leader 在内的全部成员；成员广播投递给除自己外的全部成员，每个收件人生成独立持久化消息和 delivery receipt。
- 发给执行中 Member 的消息通过未读提交屏障进入其任务结果；发给已完成 Member 或 Leader 的消息会进入最终综合证据。
- 综合阶段若收到新 Mailbox 消息，当前综合草稿作废并重新生成；TeamRun 完成与 Mailbox 版本检查在同一临界区内完成。
- Team Console 只跟随当前 live Team 或当前会话最新一条 Assistant Team 消息；后续单 Agent 回复不得继续展示更早的 Team Console。

## 7. 删除规则

本次事件重构必须直接删除：

- 顶层 SSE `TEAM_EVENT` 及其 decoder。
- `teamSnapshot`、`TeamConsoleState` 和 `teamEventReducer`。
- 每条 Team event 携带完整 `snapshot` 的发布逻辑。
- 把完整 snapshot 浅合并进单个 `team` Block 的 `_team_updated()` 逻辑。
- 前端从旧 actor、旧 RunState 或历史 Team snapshot 构造 Team 的逻辑。
- 所有旧 Team 事件双读、双写和回放适配。

删除后，旧 Team 记录不展示 Team Console；这是本次硬切换的明确产品选择。
