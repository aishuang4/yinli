# Proposal

## Why

群聊 V2 已经是人类点名的 Shared-thread：发一句、被点到的人串行各说一轮、停。队列走完后人类必须再打一句才能续场；工人也不能把话筒交给更合适的同伴。这让房间像「一轮问卷」，不像还能接着聊的群。现在壳已经清干净，适合只加控场，不恢复主持人。

## What Changes

- 工人在本轮 `group_post_message` 时可提名下一位可跑 Worker（constrained handoff）。每人每轮最多一次；不能提名自己、已在本波说过的人、已在剩余队列里的人、Host / 静音 / 崩溃 / 已移出。无效提名不挡正文落地，也不对群发调度词。
- 人类在队列空、无当前 Run 时可以「再来一轮」：按上一波人类消息的 `mentions[]`（空则按当前未静音工人）重算队列并开跑，**不**再写一条人类气泡。
- 快照增加 `lastTurn`（上一波人类点名）与 `canRerun`，供按钮启停。
- 人类再发一条、停止、插话仍清空队列；不恢复 Host、intake、阶段机、真并行、正文 `@` 解析。

## Capabilities

### New Capabilities

- （无。沿用已有 `group-studio`。）

### Modified Capabilities

- `group-studio`：在「人类点名、说完停、无 Host」之上，允许工人追加一名同伴，以及人类无新气泡地重跑上一波。

## Impact

- 后端：`group_post_message` 可选 `nextMemberId`；`speaker_queue` / `scheduler` / `service` 记录本波已发言与 `lastTurn`；新增 `POST /api/group-studio/{id}/rerun`。
- 存储：`group_studios` 增加 `last_turn_json`、`wave_spoken_json`（或等价列）；快照字段 `lastTurn` / `canRerun`。
- SSE：`GROUP_SPEAKER_QUEUE` 增加 `kind=handoff` / `kind=rerun`；不新增主持人事件。
- 前端：输入列在「停止」旁加「再来一轮」；工人提名不单独做弹层。
- 契约：更新 `docs/group-studio-event-contract.md` 与 `docs/group-studio-v2.md` 调度节。
- 隔离：Chat handlers / Team Runtime 仍禁止 import `group_studio`。
