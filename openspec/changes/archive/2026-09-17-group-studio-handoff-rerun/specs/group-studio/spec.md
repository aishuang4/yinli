# Spec Delta

## ADDED Requirements

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

## MODIFIED Requirements

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
