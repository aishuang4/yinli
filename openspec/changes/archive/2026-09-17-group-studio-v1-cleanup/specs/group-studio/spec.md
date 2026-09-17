# Spec Delta

## Purpose

群聊工作室是独立于 Chat Team 的多人房间：人类先进人、说话即开会，被点名的工人串行各说一轮后停下。本能力规定 V2 对外契约，并禁止 V1 主持、intake 与阶段机再次上线。

## ADDED Requirements

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

系统 MUST NOT 再创建可见 Host 席位，MUST NOT 派 Host 回合，MUST NOT 让 `group-studio-host` 对群发言。人类消息只按请求体 `mentions[]`（membershipId）排队工人；空 mentions 则未静音工人按加入顺序各说一轮。队列耗尽后 MUST 停止，不得再派任何系统角色。

#### Scenario: Human message without mentions runs workers only

- **WHEN** 人类在至少两名未静音工人的 `open` 房间发送不含 `mentions` 的消息
- **THEN** 系统按加入顺序串行让工人各说一轮后停止，时间线没有主持人消息

#### Scenario: Seating rejects the host sentinel

- **WHEN** 客户端试图把 `group-studio-host` 加为成员
- **THEN** 请求失败，房间成员列表不出现该席位

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
