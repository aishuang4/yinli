# group-studio Specification

## Purpose

群聊工作室是独立于 Chat Team 的多人房间：人类先进人、说话即开会，被点名的工人串行各说一轮后停下。本能力规定 V2 对外契约，并禁止 V1 主持、intake 与阶段机再次上线。

## Requirements

### Requirement: Public studio status is four-state

对外房间状态 MUST 仅为 `open`、`paused`、`done`；内部删除 MUST 使用 `deleted` 且不出现在列表。存储层若仍有旧阶段值（`drafting`、`discussing`、`executing`、`reviewing`、`awaiting_human`、`closing`、`failed`、`circuit_broken`、`archived`），读取时 MUST 归一为上述四态，不得把旧阶段名返回给客户端。

#### Scenario: Legacy discussing room opens as open

- **WHEN** 历史房间在库中的状态为 `discussing`
- **THEN** `GET /api/group-studio/{id}` 与 `GROUP_STUDIO_STATE` 的 `status` 为 `open`

#### Scenario: Deleted rooms stay off the list

- **WHEN** 房间已被标为 `deleted`
- **THEN** 列表接口不返回该房间

### Requirement: Snapshot omits V1 board fields

`GET /api/group-studio/{id}` 与连接首包 `GROUP_STUDIO_STATE` MUST 包含置顶 `pin`、成员、消息、当前 Run / 发言队列。它们 MUST NOT 再保证或要求客户端消费以下 V1 字段：`workItems`、`blackboard`、`awaitingIntakeConfirm`、`awaitingRestatement`、`claimRoundMemberIds`、`dodChecklist`。置顶备注为空 MUST 不阻止发言。

#### Scenario: Fresh snapshot has pin and no work board

- **WHEN** 客户端读取一间 V2 房间快照
- **THEN** 响应含 `pin` 且不含 `workItems`、`blackboard`、`awaitingIntakeConfirm`

### Requirement: V1 phase endpoints stay gone

下列 HTTP 入口 MUST 返回 410，错误码为 `illegal_transition`，且 MUST NOT 改变房间状态或写入工作项 / 黑板：

- `POST /api/group-studio/{id}/confirm-intake`
- `POST /api/group-studio/{id}/confirm-restatement`
- `POST /api/group-studio/{id}/start-execution`
- `POST /api/group-studio/{id}/start-review`

不得把这些路径改成 200 实现，也不得静默改成未声明的 404。

#### Scenario: Confirm-intake is gone

- **WHEN** 客户端对一间 `open` 房间调用 `POST /confirm-intake`
- **THEN** 响应状态为 410，房间仍为 `open`，没有 intake 确认标记

#### Scenario: Start-execution is gone

- **WHEN** 客户端调用 `POST /start-execution`
- **THEN** 响应状态为 410，不出现执行期状态或工作项

### Requirement: Host never takes a turn

系统 MUST NOT 再创建可见 Host 席位，MUST NOT 派 Host 回合，MUST NOT 让 `group-studio-host` 对群发言。每一波由人类消息的请求体 `mentions[]`（membershipId）或「再来一轮」按上一波 `mentions[]` 排队工人；空 mentions 则未静音工人按加入顺序各说一轮。本波进行中，工人 MAY 按 handoff 规则追加一名同伴。本波队列耗尽后 MUST 停止，不得再派任何系统角色，也不得自动开下一波。

#### Scenario: Human message without mentions runs workers only

- **WHEN** 人类在至少两名未静音工人的 `open` 房间发送不含 `mentions` 的消息
- **THEN** 系统按加入顺序串行让工人各说一轮后停止，时间线没有主持人消息

#### Scenario: Seating rejects the host sentinel

- **WHEN** 客户端试图把 `group-studio-host` 加为成员
- **THEN** 请求失败，房间成员列表不出现该席位

#### Scenario: Wave stops after queue and handoffs drain

- **WHEN** 人类点名的人与本波有效 handoff 都已说完
- **THEN** 系统停止，不再派 Host 或自动再来一轮

### Requirement: Worker may hand off once per turn

工人在本轮对群发言时 MAY 提名下一位 Worker。提名 MUST 使用对方的 `membershipId`。系统 MUST 在本轮正文成功落地后，把有效提名追加到当前波次剩余队列末尾，并广播 `GROUP_SPEAKER_QUEUE`（`kind=handoff`）。

下列提名 MUST 被忽略，且 MUST NOT 阻止本轮正文落地、MUST NOT 对群写出主持人/调度气泡：自己、本波已发言者、已在剩余队列中的人、`role=host`、静音、崩溃、已移出、不存在的席位。每位工人每轮最多生效一次提名。人类插话、停止或新消息 MUST 清空尚未开跑的 handoff。

#### Scenario: Valid handoff appends after remaining queue

- **WHEN** 工人 A 发言时队列剩余为 B，A 提名可跑且未在本波说过的 C
- **THEN** 队列变为 B 然后 C，时间线只有 A 的工人正文，没有主持人消息

#### Scenario: Invalid handoff is ignored

- **WHEN** 工人提名自己、已在队列中的人、或已静音的人
- **THEN** 本轮正文仍落地，队列不因该提名增加新人

### Requirement: Human can rerun the last turn

`open` 房间在无当前 Run、队列已空、且存在上一波人类点名记录时，人类 MUST 能请求再来一轮。该请求 MUST 按上一波的 `mentions[]` 重算可跑队列（空 mentions 则按当前未静音工人的加入顺序），MUST 开跑，MUST NOT 再写入一条人类时间线消息。

下列情况 MUST 失败且不改队列：房间不是 `open`、仍有当前 Run、队列未空、没有上一波记录、重算后无人可跑。成功时快照 `canRerun` 在新一轮开跑期间为 false，队列再次耗尽后只要上一波记录仍在则为 true。人类新消息 MUST 覆盖上一波记录。

#### Scenario: Rerun replays last mentions without a new human bubble

- **WHEN** 人类刚用 mentions=[B, C] 跑完一轮，队列已空且无 Run，再请求再来一轮
- **THEN** 系统按当前可跑的 B、C 顺序开跑，时间线不新增人类消息

#### Scenario: Rerun refused while someone is speaking

- **WHEN** 房间仍有当前 Run 或剩余队列
- **THEN** 再来一轮失败，当前发言与队列不变

### Requirement: Frontend ignores historical V1 events

前端 MUST 忽略历史事件名 `GROUP_INTAKE`、`GROUP_WORK_ITEM`、`GROUP_DOD`、`GROUP_BLACKBOARD`、`GROUP_TASK_CARD`。界面 MUST NOT 再出现确认开始、阶段条、工作项列表、DoD 或黑板弹层。

#### Scenario: Historical blackboard event does not render a board

- **WHEN** 重连重放或刷新时遇到已持久化的 `GROUP_BLACKBOARD` 事件
- **THEN** 界面不展示黑板，也不因此报错阻断后续事件

### Requirement: Chat isolation is unchanged

群聊 MUST 继续使用独立 `/api/group-studio` 与独立 SSE。Chat 发送入口与 Team Runtime MUST NOT 为了本变更引入群聊领域依赖。`pause`、`resume`、`close`、`interrupt` 与工作室审批兜底仍可用，不因清理 V1 而被删除。

#### Scenario: Chat send stays free of group-studio imports

- **WHEN** 开发者检查 Chat 发送与 Team Runtime 模块
- **THEN** 它们不 import 群聊工作室领域包
