# Spec Delta

## Purpose

规定 Chat Trace 按一次 Run 组织观测步骤：列表与时间线对应用户在 Chat 里看见的步骤和耗时，点开一步只看该步的输入与输出，不把审计碎事件或整段会话当成一条运行。

## ADDED Requirements

### Requirement: Trace identity is a Chat Run

Trace 列表与详情的稳定身份 MUST 是一次 Chat Run（用户送出或恢复同一 Run），MUST NOT 再用 Chat Session 当作 `traceId`。同一 Session 内的多次 Run MUST 在列表中分列。列表 MUST 只包含当前 Project 的 Run。每条摘要 MUST 带：Run 状态（与 Chat Run 生命周期一致）、墙钟起止或总耗时、Agent 名称、mode、用户任务摘要。产品档关闭 traces 页面时，对应 Trace API MUST 继续 404，且 MUST NOT 因此关闭 Chat `/api/chat/runs`。

#### Scenario: Two sends become two traces

- **WHEN** 用户在同一 Session 连续发送两轮并都已结束
- **THEN** Trace 列表出现两条记录，各自 `id` 为对应 Chat Run id，互不合并

#### Scenario: Session id is not a trace id

- **WHEN** 客户端用 `chat_sessions.id` 请求 Trace 详情
- **THEN** 系统返回未找到，或明确不是该 Trace

#### Scenario: List stays in current project

- **WHEN** 用户切换到另一 Project 打开 Trace 页
- **THEN** 列表不含上一 Project 的 Run

### Requirement: Run status is the Chat Run lifecycle

Trace 摘要与详情上的运行状态 MUST 读取该 Chat Run 的生命周期状态（`running`、`waiting_user`、`waiting_permission`、`waiting_external`、`completed`、`failed`、`cancelled` 及产品已暴露的等价态）。系统 MUST NOT 再靠扫描事件类型启发式推断「完成 / 等待 / 失败」。

#### Scenario: Waiting matches Chat

- **WHEN** 该 Run 因 AskUser 处于 `waiting_user`
- **THEN** Trace 列表与详情显示等待用户，而不是 completed 或 running

#### Scenario: Cancel is not inferred from messages

- **WHEN** 用户取消该 Run，Chat Run 状态为 `cancelled`
- **THEN** Trace 状态为已取消，即使助手消息里没有 error 字段

### Requirement: Observation is one closed step

每条 Observation MUST 表示 Chat 中对得上的一步，并 MUST 包含：稳定 id、所属 `runId`、类型、名称、可选 parent、`startedAt`、`endedAt`（未结束可空）、`durationMs`（已结束时等于墙钟差）、状态、input、output。一次工具调用 MUST 只产生一条 `tool` Observation；一次模型调用 MUST 只产生一条 `generation` Observation。系统 MUST NOT 把 `start` / `call` / `end` 或 token 级 delta 写成独立 Observation。

#### Scenario: One bash call is one tool row

- **WHEN** 本轮 Agent 调用一次 Bash 且执行结束
- **THEN** 该 Run 恰好多一条类型为 `tool`、名称为该工具的 Observation，input 含最终参数，output 含结果或错误

#### Scenario: Deltas are not timeline children

- **WHEN** 同一工具在执行中产生多段输出增量
- **THEN** 时间线不出现多条以 delta 为名的 Observation

### Requirement: Observation types map to Chat steps

时间线可展示的 Observation 类型 MUST 仅为：`run`（本轮根）、`generation`（模型 reasoning / 输出）、`tool`、`skill`、`approval`（AskUser 或权限确认）、`subagent`、`team-task`。`generation` 与它触发的 `tool` MUST 为兄弟，MUST NOT 把整圈 ReAct 收成一条 `generation`。并行工具或并行 Team 任务 MUST 各为一条 Observation，时间可重叠。群聊工作室步骤 MUST NOT 写入 Chat Trace Observation。

#### Scenario: Reasoning then tools are siblings

- **WHEN** 模型先输出 reasoning 并决定调用两个工具，两工具先后或并行执行
- **THEN** 时间线上先有一条 `generation`，随后两条 `tool`，三者不互相把对方收进自己的 input/output 当唯一节点

#### Scenario: AskUser is an approval step

- **WHEN** 本轮因 AskUser 暂停
- **THEN** 时间线出现一条 `approval` Observation，状态为等待，直到用户提交或 Run 取消

#### Scenario: Group studio is absent

- **WHEN** 用户在群聊工作室完成一轮工人发言
- **THEN** Chat Trace 列表与 Observation 树不出现该群聊步骤

### Requirement: Timeline is the first layer

打开一条 Trace 的第一层 MUST 是该 Run 的墙钟时间线：总长度等于 Run 起止（进行中则到当前），每一段对应一条 Observation（根 `run` 可只作为总条）。等待类 `approval` MUST 与执行类步骤在视觉上可区分。并行 Observation MUST 允许在同一时刻重叠展示，MUST NOT 被强制串成先后。总条展示墙钟；若同时展示分段耗时合计，界面 MUST 不把并行段简单相加冒充总时长。第一层 MUST NOT 展示下列内容作为时段：Intent 路由、Readiness 闸门、Workspace / 权限策略配置、Context 占用碎片、`RUNTIME_EVENT` 包层、Memory 审计、模型 fallback 细节（fallback 可作为该 `generation` 的 metadata）。

#### Scenario: Open trace shows segments not event dump

- **WHEN** 用户打开已完成的单 Agent Run
- **THEN** 第一屏是带总耗时的时间线，以及 generation / tool / approval 等时段，而不是 `TOOL_CALL_START` 与 `RUNTIME_EVENT` 交错的事件列表

#### Scenario: Wait does not look like model time

- **WHEN** 一轮总墙钟 8 分钟，其中 7 分钟在等 AskUser
- **THEN** 时间线上等待段可与模型/工具段区分，且总条仍为约 8 分钟

#### Scenario: Hidden audit stays off the axis

- **WHEN** 该 Run 写入了 Readiness、Intent 与 CONTEXT_USAGE 审计
- **THEN** 第一层时间线没有对应时段

### Requirement: Detail is input and output of one observation

用户选中时间线某一段后，第二层 MUST 只展示该 Observation 的 input、output 与该步必要 metadata（如模型名、token、权限判定、父步骤 id）。`tool` 的 input MUST 是最终调用参数，output MUST 是最终结果或错误。`generation` 的 output MUST 能看出本轮模型产出（reasoning 文本、要调用的工具声明或最终回答摘要），MUST NOT 要求用户再点 `start` / `call` / `end` 子事件。未选中任何时段时，根 Observation 的 input/output MUST 分别为本轮用户任务与助手最终回答（若已有）。

#### Scenario: Click tool shows params and result

- **WHEN** 用户点开一条已完成的 `tool` 时段
- **THEN** 第二层显示该次调用的最终参数与结果，不出现独立的 start/call/end 列表

#### Scenario: Click generation does not embed tool stdout

- **WHEN** 用户点开触发工具之前的 `generation`
- **THEN** 第二层不把后续工具的 stdout 当作该 generation 的 output

#### Scenario: Root shows user and assistant

- **WHEN** 用户打开已成功结束的 Trace 且未点子时段
- **THEN** 详情默认展示用户原话与助手最终回答

### Requirement: Trace write must not break Chat

Observation 写入失败 MUST NOT 中断 Chat Run。`persist=false`（Sandbox）MUST NOT 持久化 Observation。Chat SSE 仍 MUST 只使用 RuntimeFrame / DisplaySnapshot V4；前端 Chat MUST NOT 为了 Trace 去解析 Observation。刷新 Chat 历史 MUST 仍只依赖 V4 Snapshot。

#### Scenario: Trace store failure keeps streaming

- **WHEN** 持久化某条 Observation 失败
- **THEN** 当前 Chat Run 继续执行并继续向订阅者推送 Frame

#### Scenario: Sandbox leaves no trace row

- **WHEN** 用户以 sandbox 发送一轮
- **THEN** Trace 列表不出现该 Run 的 Observation 详情（或不出现该 Run）

### Requirement: Resume continues the same trace

对同一 Chat Run 的 HITL resume MUST 把后续 Observation 追加到同一 `runId` 的时间线，MUST NOT 新建一条 Trace。新的用户送出 MUST 新建 Run，从而新建 Trace。

#### Scenario: AskUser resume stays one timeline

- **WHEN** 用户回答 AskUser 后同一 Run 继续调用工具并结束
- **THEN** Trace 详情仍是原来的 Run id，时间线上在 approval 之后出现后续 generation/tool

#### Scenario: New send is a new trace

- **WHEN** 上一轮已结束后用户再发一条新消息
- **THEN** Trace 列表新增一条 Run，不把新步骤追加进上一 Trace
