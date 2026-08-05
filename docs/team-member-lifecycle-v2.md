# Team Member Lifecycle V2 设计

> 状态：方案草案，待批准后实施。更新时间：2026-08-05。
>
> 本文定义 V2 目标状态；Phase 1/2 已在 `codex/team-member-lifecycle-v2` 分支实现，Reviewer、原 Task rework 和跨进程恢复仍未实现。当前在线基线仍以 `docs/team-runtime-redesign.md` 为准。

## 1. 背景与核心问题

当前 Team Runtime 已具备一次性 TeamRun、动态 Member、Task List、Mailbox、并发执行和独立综合，但 Member 的领域身份与单次任务执行仍然耦合：

1. Leader 在协调阶段一次性创建 Member 和 Task，调用 `team_finish_coordination` 后退出。
2. Coordinator 只调度初始 Task Graph，不判断结果质量，也不动态创建返工或追加任务。
3. `run_member_task()` 按 `member_id + task_id` 创建一次执行实例；Member 调用 `team_submit_result` 后，本次执行立即结束。
4. Member 状态虽然会恢复为 `idle`，但只代表它可以领取一个已经存在的 queued Task，不代表它仍有可被 Mailbox 唤醒的行为生命周期。
5. 发给 running Member 的消息可以通过未读提交屏障进入当前结果；发给 idle Member 的消息只会持久化并进入综合证据，不会触发二次执行。
6. 任务一旦写为 `succeeded` 就被视为可综合结果，没有候选提交、质量审核、返工和多次 Attempt。

因此，当前模型实际是：

```text
Member Definition
  -> One Task Execution
  -> Submit Result
  -> Execution Ends
```

V2 需要把 Member、Task 和 TaskAttempt 分层，让 Member 在整个 TeamRun 内保持逻辑存活，但不保持常驻模型进程。

## 2. 目标

V2 必须实现：

1. Member 是 TeamRun 级长期领域实体，直到 TeamRun 终态才停止。
2. Task 是需要交付和验收的工作单元，不等同于一次模型执行。
3. TaskAttempt 是 Member 对 Task 的一次执行，可失败、返工和重试。
4. idle Member 可以被明确的用户指令、返工决定或新 Task 事件唤醒并再次执行。
5. Member 提交的是候选结果；只有 Reviewer 接受后，Task 才进入 `succeeded`。
6. Reviewer 可以接受结果、要求原 Member 返工，或创建补充 Task 并分配其他 Member。
7. 全部 Task 验收通过且没有待处理执行型 Mailbox 消息后，TeamRun 才能进入综合。
8. 所有状态可持久化、可审计、可重放，并继续投影到 DisplaySnapshot V4。

## 3. 非目标

V2 不实现：

- 不让模型进程或 AgentScope session 永久驻留。
- 不恢复持久 Team、固定成员选择器或跨 TeamRun 复用 Member。
- 不允许 Member 任意修改 Task Graph。
- 不允许模型绕过权限、ToolScope、Workspace 或 Project 边界。
- 不恢复旧顶层 `TEAM_EVENT`、`teamSnapshot` 或平行前端状态树。
- 不保证开发期 V1 running TeamRun 原地升级或跨进程续跑。

## 4. 设计原则

### 4.1 长生命周期身份，短生命周期执行

Member 在 TeamRun 内持续存在；只有收到可执行工作时才创建一次短生命周期 TaskAttempt。Attempt 结束后释放模型和工具资源，Member 回到 `idle` 或进入 `waiting_review`。

```text
Member
  idle
    -> running(Attempt 1)
    -> waiting_review
    -> idle
    -> running(Attempt 2)
    -> waiting_review
    -> idle
  stopped when TeamRun terminates
```

### 4.2 任务成功必须经过验收

`team_submit_result` 不再直接把 Task 写为 `succeeded`，而是：

```text
running
  -> review_pending
  -> succeeded
  -> rework_requested -> queued -> running
```

### 4.3 调度由事件触发，不轮询常驻 Agent

Coordinator 持续监听持久化状态变化并机械调度；Leader/Reviewer 只在需要语义判断时创建短 session：

- 候选结果进入 `review_pending`。
- 用户发出执行型 Mailbox 指令。
- 新 Task 解锁。
- 综合期间 Mailbox 版本变化。

### 4.4 普通消息与执行命令必须区分

Mailbox 正文不能靠猜测决定是否启动模型。用户发送时必须选择明确的 `dispatchMode`：

| `dispatchMode` | 语义 | 是否唤醒 idle Member |
|---|---|---|
| `context` | 补充上下文，仅供当前 Attempt 或最终综合使用 | 否 |
| `continue_task` | 要求指定 Member 继续当前/最近 Task | 是 |
| `new_task` | 创建一个新的追加 Task | 是 |

推荐交互：

- running Member 默认 `continue_task`，消息并入当前 Attempt 的未读屏障。
- idle Member 默认 `new_task`，界面明确显示“将创建追加任务”。
- broadcast 默认 `context`，避免一次广播意外创建多个模型调用。
- 用户可以手动切换语义，后端不得根据自然语言静默改变 `dispatchMode`。

## 5. 领域模型

### 5.1 TeamRun

新增字段：

```json
{
  "schemaVersion": 2,
  "phase": "coordinating | executing | reviewing | synthesizing",
  "pendingDispatchCount": 0,
  "pendingReviewCount": 0,
  "dynamicTaskCount": 0
}
```

TeamRun 终态仍为 `completed | failed | cancelled`，终态不可逆。

### 5.2 Member

Member 状态：

| 状态 | 含义 |
|---|---|
| `created` | 已建立身份，尚未进入调度 |
| `idle` | 无活动 Attempt，可接收新任务或执行型消息 |
| `running` | 正在执行一个 Attempt |
| `waiting_review` | 已提交候选结果，等待 Reviewer 决策 |
| `stopped` | TeamRun 正常或取消收口 |
| `failed` | Member 发生不可恢复失败 |

新增字段：

```json
{
  "activeTaskId": "task_xxx",
  "activeAttemptId": "attempt_xxx",
  "lastTaskId": "task_xxx",
  "lastAttemptId": "attempt_xxx",
  "attemptCount": 2
}
```

不变量：

- 一个 Member 同时最多有一个 running Attempt。
- `idle` Member 不持有 `activeTaskId` 和 `activeAttemptId`。
- TeamRun 未进入终态前，Member 的 `idle` 不是生命周期终态。

### 5.3 Task

Task 状态：

| 状态 | 含义 |
|---|---|
| `queued` | 等待依赖满足或等待分配 |
| `running` | 存在一个 running Attempt |
| `review_pending` | 候选结果已提交，等待验收 |
| `rework_requested` | Reviewer 已拒绝候选结果，等待重新排队 |
| `succeeded` | 最新候选结果已经验收 |
| `failed` | 达到保护上限或发生不可恢复失败 |
| `cancelled` | TeamRun 取消或任务被明确取消 |

新增字段：

```json
{
  "currentAttemptId": "attempt_xxx",
  "acceptedAttemptId": "attempt_xxx",
  "attemptCount": 2,
  "reviewCount": 1,
  "lastReviewReason": "证据不足，需要补充来源",
  "origin": "leader | reviewer | user_mail",
  "originMailId": "mail_xxx"
}
```

Task 只有在 `acceptedAttemptId` 非空时才能进入 `succeeded`。

### 5.4 TaskAttempt

TaskAttempt 状态：

```text
created -> running -> submitted -> accepted
                   -> rejected
                   -> failed
                   -> cancelled
```

建议结构：

```json
{
  "id": "attempt_xxx",
  "teamRunId": "trun_xxx",
  "taskId": "task_xxx",
  "memberId": "member_xxx",
  "ordinal": 2,
  "status": "submitted",
  "trigger": "initial | rework | user_mail | retry",
  "triggerMailId": "mail_xxx",
  "instruction": "补充证据并核对版本差异",
  "resultPath": "result.md",
  "resultHash": "sha256:...",
  "startedAt": "...",
  "submittedAt": "...",
  "reviewedAt": "...",
  "failureReason": ""
}
```

每次 Attempt 使用独立 runtime cache key：

```text
team-member-{teamRunId}-{memberId}-{taskId}-{attemptId}
```

Member 的角色、runtime prompt、Memory 摘要和当前 Team 事实可重新注入；不得依赖常驻进程内对象恢复上下文。

## 6. 文件事实源

V2 保持 Team file-first 边界：

```text
team-runs/{teamRunId}/
├── run.json
├── members/
│   └── {memberId}.json
├── tasks/
│   └── {taskId}/
│       ├── task.json
│       ├── brief.md
│       ├── accepted-result.md
│       └── attempts/
│           └── {attemptId}/
│               ├── attempt.json
│               ├── instruction.md
│               └── result.md
├── mailbox/
│   ├── messages.jsonl
│   └── deliveries/{messageId}.json
├── events.jsonl
└── snapshot.json
```

写入顺序必须是：事实文件成功写入，再追加领域事件，再写轻量 snapshot。任何 projector 都不得回读这些文件补齐事件字段。

## 7. Coordinator V2

Coordinator 仍是机械状态机，不直接做模型判断：

1. 原子消费未处理的执行型 Mailbox 指令。
2. Phase 2 将 `continue_task` 转换为依赖原 Task 的追加 Task；Phase 3 在 Task Block 生命周期契约冻结后再支持原 Task 的新 Attempt。
3. 将 `new_task` 转换为新 Task，并记录 `originMailId`。
4. 调度依赖满足且存在空闲 Member 的 queued Task。
5. Attempt 提交后将 Task 转为 `review_pending`。
6. 触发 Reviewer session。
7. 根据 Reviewer 决策接受、返工或扩展 Task Graph。
8. 重新计算依赖、并发槽位和 completion barrier。
9. 所有 Task 验收、无 running Attempt、无待处理执行型 Mail 后进入综合。

Coordinator 不得根据结果正文自行判断质量，也不得在没有持久化命令的情况下启动 Attempt。

### 7.1 TeamRunService 编排循环

V1 的 `Coordinator -> Synthesis` 单向调用必须改成可回退的监督循环：

```text
while TeamRun is running:
  consume executable mailbox commands
  run coordinator until quiescent
  run pending reviews
  if completion barrier is not satisfied:
    continue
  run synthesis against taskGraphVersion + mailboxVersion
  if either version changed:
    discard draft and continue
  atomically complete TeamRun
```

综合期间收到 `context` 只需重新综合；收到 `continue_task` 或 `new_task` 必须从 `synthesizing` 回到 `executing/reviewing`，完成新增 Attempt 和验收后才能再次综合。不能只重跑 synthesis 而跳过 Coordinator。

## 8. Reviewer / Supervisor

V2 不恢复一直运行的 Leader session，而是保留 Leader 逻辑身份，并在决策点创建短生命周期 Reviewer session。

Reviewer 输入：

- 用户原始目标；
- Task brief 和验收条件；
- 当前候选结果；
- 上游已验收结果；
- 与当前 Task 相关的 Mailbox；
- Attempt 历史摘要和保护预算。

Reviewer 只拥有控制工具：

```text
team_accept_result
team_request_rework
team_create_followup_task
team_read_tasks
team_read_mailbox
```

Reviewer 不拥有普通文件、Bash、MCP 或知识库工具，避免它绕过 Member 自己完成业务任务。

Reviewer 决策：

- `accept`：Attempt -> accepted，Task -> succeeded，Member -> idle。
- `rework`：Attempt -> rejected，Task -> rework_requested -> queued，优先交回原 Member。
- `followup`：接受或暂缓当前结果，同时创建一个有显式依赖的补充 Task。
- `fail`：仅在达到保护上限或存在不可恢复错误时使用。

## 9. Mailbox 唤醒语义

### 9.1 running Member

- `context` 和 `continue_task` 都写入 Mailbox。
- 未读提交屏障继续生效。
- Member 必须在提交候选结果前读取消息并处理。
- `new_task` 创建独立 Task，不混入当前 Attempt。

### 9.2 idle Member

- `context` 只进入综合证据，不启动模型。
- `continue_task` 为最近 Task 创建一个依赖原 Task 的追加 Task，保留原 Task 的验收历史；没有可继续 Task 时返回明确错误。
- `new_task` 创建并分配一个新 Task，随后由 Coordinator 唤醒 Member。
- Phase 2 不 reopen 已完成的 `team-task` Block，而是创建 `origin=user_mail` 的追加 Task，避免违反当前 Block 终态不可逆不变量。
- Phase 3 若引入原 Task rework，必须先定义 Task Block 的 revision/Attempt 子树语义，并使依赖旧验收结果的下游重新进入审核。

### 9.3 waiting_review Member

- `context` 进入 Reviewer 输入。
- `continue_task` 不直接并发启动第二个 Attempt，而是标记当前候选结果需要重审。
- Reviewer 必须基于新消息决定接受或返工。

### 9.4 已终态 TeamRun

- 继续拒绝 Mailbox 写入。
- 用户需要通过新 Chat Run 发起后续任务，不允许复活已完成 TeamRun。

## 10. 并发与幂等

必须保证：

1. `messageId` 只能生成一个 dispatch 结果。
2. 同一 Task 同时最多一个 running Attempt。
3. 同一 Member 同时最多一个 running Attempt。
4. Reviewer 决策使用 `expectedAttemptId` 做 compare-and-set，不能接受已被替换的候选结果。
5. 综合启动前记录 `taskGraphVersion` 和 `mailboxVersion`；任一变化都丢弃草稿并重新综合。
6. Run 取消后，晚到的 Attempt/Reviewer 事件只能收口子状态，不能恢复 TeamRun。
7. 重复 `submitted`、`accepted`、`rejected` 事件按幂等处理。

文件锁至少按以下粒度建立：

- TeamRun dispatch lock；
- 每个 Task transition lock；
- 每个 Member assignment lock；
- synthesis completion lock。

## 11. 循环保护与预算

V2 允许多次执行，但必须防止无效循环：

| 限制 | 推荐默认值 |
|---|---:|
| 单 Task 最大 Attempt | 3 |
| 单 Task 最大 Reviewer 次数 | 3 |
| TeamRun 动态追加 Task | 16 |
| 同一 Member 连续返工 | 3 |
| 相同结果指纹连续出现 | 2 |
| TeamRun 总 Attempt | 32 |

结果指纹由 Task、规范化结果摘要、工具证据摘要和 Reviewer 原因计算。连续无实质变化时应失败并提示用户，而不是继续提高 `maxReactIters`。

用户明确发送 `continue_task` 可以消耗一次额外 Attempt，但不能绕过 TeamRun 总预算和取消状态。

## 12. API 方案

现有接口保留路径：

```text
POST /api/chat/runs/{runId}/team/mail
```

请求扩展为：

```json
{
  "recipient": "member_xxx",
  "content": "继续核对上游版本差异",
  "dispatchMode": "continue_task",
  "taskId": "task_xxx"
}
```

兼容策略：

- V2 新请求必须显式发送 `dispatchMode`。
- 旧客户端缺省为 `context`，避免静默产生额外模型调用。
- HTTP response 只确认命令已接收，不直接修改前端 Team 状态。
- 最终状态继续由 RuntimeFrame 回流。

## 13. 领域事件与展示

建议新增 Team 领域事件：

```text
attempt.created
attempt.started
attempt.submitted
attempt.accepted
attempt.rejected
attempt.failed
attempt.cancelled
review.started
review.completed
mail.dispatched
task.rework.requested
```

建议新增 `kind=team-attempt` Block，作为 `team-task` 的子节点；Reviewer 活动继续使用 `team-activity`。前端展示：

- Task 当前状态和 Attempt 次数；
- 每次 Attempt 的执行者、触发原因、结果和终态；
- Reviewer 的接受/返工结论；
- idle Member 收到执行型消息后的排队与再次运行；
- Mail 的 delivered/read/acted 以及对应 Task/Attempt。

这是 Chat 展示语义变化。进入实现阶段前，必须先更新 `docs/chat-sse-event-contract.md`，再修改 projector 和前端 kind registry。

## 14. Completion Barrier

TeamRun 进入综合前必须同时满足：

```text
all tasks == succeeded
and no task == review_pending | rework_requested | queued | running
and no attempt == created | running | submitted
and no executable mailbox dispatch is pending
and no reviewer session is running
```

综合期间出现以下事件必须使当前草稿失效：

- 新执行型 Mail；
- 新 Task；
- 新 Attempt；
- Task 从 succeeded 回到 rework_requested；
- Reviewer 决策版本变化。

## 15. 恢复、取消与历史兼容

- V2 新 TeamRun 写入 `schemaVersion=2`。
- V1 历史 TeamRun 保持只读展示，不迁移成 V2，也不补造 Attempt。
- 开发期进程启动时发现孤儿 running TeamRun，仍按当前规则收口为 cancelled；V2 第一阶段不承诺跨进程续跑。
- Run 取消时必须取消 running Attempt 和 Reviewer session，将 queued/review_pending/rework_requested Task 收口为 cancelled，并把所有 Member 收口为 stopped 或 failed。
- 未来如支持恢复，必须从文件事实源重建 dispatch 队列，不能依赖进程内 `asyncio.Task`。

## 16. 分阶段实施

### Phase 0：契约冻结

- 更新 `chat-sse-event-contract.md`。
- 固化 Pydantic、Python 和 TypeScript 类型。
- 确定 TaskAttempt 文件布局和事件字段。
- 确定 Mailbox `dispatchMode` UI 文案。

### Phase 1：TaskAttempt 基础

- 引入 Attempt 存储和状态机。
- `team_submit_result` 改为提交候选结果。
- 先使用确定性自动接受策略保持现有行为，验证数据迁移边界。
- Team Console 展示 Attempt 历史。

### Phase 2：Mailbox 唤醒

- 增加 `dispatchMode`。
- idle Member 支持 `continue_task`（追加 Task）和 `new_task`。
- Coordinator 支持同一 Member 多次事件驱动执行。
- 完成 Mail -> Task -> Attempt -> Frame 的端到端测试。

### Phase 3：Reviewer 闭环

- 增加短生命周期 Reviewer session。
- 支持接受、返工和补充 Task。
- 增加结果指纹、Attempt 上限和动态 Task 上限。
- 综合 Completion Barrier 切换到验收语义。

### Phase 4：恢复与产品化

- 完善取消、刷新恢复、孤儿收口和 Trace。
- 增加 Team Console 操作和 Playwright 回归。
- 评估跨进程续跑，不与前三阶段绑定上线。

## 17. 测试矩阵

后端必须覆盖：

1. 同一 Member 顺序执行两个预建 Task，产生两个独立 Attempt。
2. idle Member 收到 `continue_task` 后创建追加 Task 并重新运行。
3. idle Member 收到 `new_task` 后创建并领取追加 Task。
4. `context` 不触发模型执行。
5. running Member 收到消息后，未读屏障阻止提交。
6. Reviewer 接受候选结果后 Task 才 succeeded。
7. Reviewer 返工后原 Member 创建下一次 Attempt。
8. Reviewer 创建补充 Task 后综合不得提前开始。
9. 消息、提交和 Reviewer 决策并发时不丢消息、不重复 Attempt。
10. 达到 Attempt、动态 Task 或无进展上限后明确失败。
11. Run 取消后晚到事件不能重新激活 Member 或 Task。
12. V1 历史 TeamRun 仍可只读展示。

浏览器必须覆盖：

1. idle Member 的“继续执行”和“新建追加任务”。
2. Attempt 1 完成后 Member 回到 idle，再进入 Attempt 2。
3. Task 显示待审核、返工原因和最终验收结果。
4. Mail delivery 从 delivered 到 read/acted，并关联新 Attempt。
5. 刷新后 Member、Task、Attempt 和 Mail 状态一致。
6. 综合阶段出现新执行命令时，旧草稿作废并重新综合。

## 18. 验收标准

V2 完成必须满足：

- Member 完成一个 Task 后仍是 TeamRun 内可再次调度的实体。
- 给 idle Member 发送明确执行命令会产生新的 TaskAttempt，并在界面实时可见。
- Task 成功与 Attempt 结束不再是同一状态转换。
- 不需要常驻模型进程，也不依赖进程内 Agent 对象维持 Member 身份。
- Reviewer 可以基于结果质量要求继续调研，且所有返工有持久化原因。
- 循环保护可阻止重复无进展执行，同时允许合理的长任务。
- Team 最终答案只使用已验收结果和完整 Mailbox 证据。
- 取消、并发消息、刷新和历史回放不破坏终态不变量。

## 19. 推荐决策

建议批准以下方向：

1. 采用“长生命周期 Member + 短生命周期 TaskAttempt”，拒绝常驻 Agent。
2. Mailbox 使用显式 `dispatchMode`，旧请求缺省为 `context`。
3. Phase 1 先建立 Attempt 数据模型，再实现 Mailbox 唤醒，最后引入 Reviewer。
4. Reviewer 复用 Leader 的逻辑身份，但每次审核创建独立短 session。
5. 默认单 Task 最多 3 次 Attempt、TeamRun 最多 32 次 Attempt，后续通过真实测试校准。

批准本文后，实施必须从 Phase 0 契约更新开始，不直接在现有 Coordinator 上追加隐式唤醒分支。
