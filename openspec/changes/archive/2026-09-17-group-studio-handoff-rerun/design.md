# Design

## Context

现行调度见 `docs/group-studio-v2.md` 第 8 节与 `speaker_queue.build_turn_queue`：只有人类 `POST /messages` 会 `set_queue` 再 `schedule_next`。工人工具只有 `group_post_message`（body / intent），说完 `_continue_after_worker`，队列空则 `kind=drained` 停。快照已有 `speakerQueue` / `pendingSpeakerMemberId` / `currentSpeakerMemberId`，没有「上一波点名」或「本波谁说过」。动机见 proposal.md Why；行为见 specs。

## Goals / Non-Goals

**Goals:**

- 在现有串行队列上长出「追加一人」和「重放上一波」，不新开 Runtime。
- 用列 + 快照表达 `lastTurn` / 本波已发言，进程重启不丢。
- 前端只加一个「再来一轮」按钮，不恢复顶栏暂停/结束。

**Non-Goals:**

- 不引入 LLM Selector、Host 工具、正文 `@` 解析、真并行。
- 不把 handoff 画成时间线系统公告。
- 不把 `canRerun` 做成「预计算一定有人可跑」；无人可跑由 POST 失败表达。

## Decisions

### D1. 提名挂在 `group_post_message.nextMemberId`

工人本轮本来就要调这个工具收口。加可选 `nextMemberId`（membershipId），比新工具少一次调用，也避免「先提名再忘了发言」。

备选：独立 `group_handoff`。否决：会变成控场工具，像 V1 Host。

无效或重复的 `nextMemberId`：工具仍成功（正文优先），运行时丢弃提名。第二次传入忽略（每轮一次）。

### D2. 本波状态：`last_turn_json` + `wave_spoken_json`

`group_studios` 用 `_ensure_column` 加两列，默认 `'{}'` / `'[]'`。

- `lastTurn`：`{ "kind": "assign" | "all_round", "mentions": string[] }`。只在人类 `POST /messages` 成功写出气泡后覆盖。rerun / handoff / 停止不改。
- `waveSpoken`：本波已完成回合的 `membershipId[]`（含 failed；cancelled 由人类插话整波重置）。工人 Run 终态写入；handoff 校验「已发言」读它。

人类新消息、停止、插话：`clear_queue`，`waveSpoken=[]`。rerun：`waveSpoken=[]`，保留 `lastTurn`。

备选：只从时间线反推上一波。否决：系统提示、失败气泡、handoff 追加会让边界糊。

### D3. handoff 追加队尾，不插队

`pop_head` 在开跑时已把自己弹出。A 说话时剩余 `[B]`、提名 C → `set_queue([B, C], kind=handoff)`。A 是本波最后一人且队列已空 → `[C]`，`_continue_after_worker` 接着跑 C。

拒绝：自己、`waveSpoken`、剩余队列、不可跑席位。不写系统句。

### D4. `POST /{id}/rerun` 不写人类气泡

请求体只要 `projectId`。前置：`status=open`、无 `currentRunId`、队列空、`lastTurn` 非空。`build_turn_queue(snapshot, lastTurn.mentions)`；空队列则 409 `no_eligible_speaker`。成功则 `set_queue(..., kind=rerun)` + `schedule_next`。

`paused` 不自动恢复（与「再发一条会 resume」区分，避免无气泡静默开跑）。409 `studio_not_open`。

忙：409，复用或新增稳定码 `studio_busy`（有 Run 或队列未空）。无 `lastTurn`：409 `no_last_turn`。

备选：`POST /messages` 空 body + `rerun=true`。否决：空 body 今天已 422，和「人类说话才开会」混在一起。

### D5. 快照 `lastTurn` + `canRerun`

`canRerun = status==open && !currentRunId && speakerQueue空 && lastTurn.kind 存在`。按钮只看这个；点下去才可能 `no_eligible_speaker`。

`GROUP_SPEAKER_QUEUE.kind` 增加 `handoff` | `rerun`。前端已忽略未知 kind 以外的队列更新只要仍读 `memberIds`；文档补上。

### D6. 提示词只加一句

Worker 系统提示保持「你不是主持人」。补：需要同伴接着说时，可在 `group_post_message` 带对方 `membershipId`；不要指挥全场、不要等待批准。

## Risks / Trade-offs

- [工人乱点名] → 每轮一次 + 不能点已说/已排队的人，最长追加不超过剩余席位。
- [rerun 打到已静音的旧名单] → 重算时丢掉不可跑；全灭则 409，不空转。
- [pytest 不点火] → 断言队列与 `lastTurn` / `canRerun`，不跑模型；handoff 单测直接调 enqueue helper。
- [旧客户端无按钮] → 只是少入口，契约向后兼容。

## Migration Plan

1. 加列，旧房间 `lastTurn=null`、`canRerun=false`。
2. 部署后端再前端；旧 Worker 不传 `nextMemberId` 行为不变。
3. 回滚：停用 rerun 路由与工具字段即可，列可留。

## Open Questions

无。按钮文案固定为「再来一轮」，与停止并排。
