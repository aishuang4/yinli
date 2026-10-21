# 群聊工作室

> 状态：2026-10-09。在线摘要见 `docs/architecture-overview.md`「群聊工作室」。事件字段只以 `docs/group-studio-event-contract.md` 为准。不要恢复可见 Host、intake、阶段机、工作项或确认开始。

房间留下，主持流程已拆掉。群聊不是一次性 TeamRun，也不替换 Chat 的 Team 模式。不共用 `POST /api/chat/send`、Chat SSE、Mailbox 或 DisplaySnapshot。

| ID | 锁定值 |
| --- | --- |
| D1 | 时间线没有主持人。`group-studio-host` 不入座、不对群发言 |
| D2 | 新建 / 进群先空白画布。第一条消息才 `from-chat` 落库为 `open`，不自动开跑 |
| D3 | 人类点发送才开会。没有确认开始，也没有 drafting |
| D4 | 队列只看 `mentions[]`（membershipId）。不解析正文里的 `@名字` |
| D5 | `mentions` 非空：按数组去重后串行。一个都不能跑则 `no_eligible_speaker`，不回退全员 |
| D6 | `mentions` 为空：未静音、未移除、未崩溃的 Worker 按 `joinedAt` 升序各说一轮 |
| D7 | 对外状态 `open \| paused \| done`，内部另有 `deleted` |
| D8 | 最多 6 个 Worker。人类不是 Membership |
| D9 | 同时只有一条 `GroupRun`。说完停，不派系统角色 |
| D10 | 置顶可空，不挡发言 |
| D11 | 不做黑板、工作项、真并行、时间旅行，群事件不进 Trace |

## 调度

可跑席位：`role=worker`、`status=active`、`muted=false`，且仍在本群。

队列跑完发 `GROUP_SPEAKER_QUEUE kind=drained` 然后停。不自动综合。工人本轮 `group_post_message` 可带 `nextMemberId`，有效则追加到剩余队列末尾；自己、本波已说过、已在队列或不可跑的席位忽略。

| 动作 | 结果 |
| --- | --- |
| 人类再发一条 | 取消当前 Run，丢掉旧队列，按新消息重算。若原是 paused，回到 open |
| 输入列停止 | 取消当前 Run，清空队列，房间仍 open |
| 再来一轮 | 仅当没有 Run 且队列为空，按上一轮 mentions 重建，不写人类气泡 |
| 踢走或静音正在说的人 | 取消其 Run，队列继续 |
| 拉人 | 不加入本轮队列 |
| 熔断 | 房间 `paused`，`pauseReason=circuit`。再发一条恢复 |

`done` / `deleted` 拒绝发送，返回 `409 studio_not_open`。旧的 `confirm-intake` / `confirm-restatement` / `start-execution` / `start-review` 返回 410。禁止用 `gstu_local_*` 打 API。

满员再拉返回 `409 member_limit`。踢人把席位标 `removed`，不物理删行。同一 `agentId` 的 `crashed` 行可以重新入座；`removed` 再拉则新建。

## Worker

工具只留 `group_post_message`。权限固定 `PermissionMode.BYPASS`，不抄 Chat Agent profile。另有 bypass 免疫的 `WORKER_SHELL_BLACKLIST`。`group_approvals` 只兜底，禁止写入 Chat `PENDING_INTERACTIONS`。

`default` 与 `group-studio-host` 不入座。界面不提供暂停 / 结束，只留输入列「停止」。`close_studio(confirm=true)` 仍可标 `done` 并归档记忆，前端不露出该按钮。

通知 `kind` 以 `group_studio_` 开头，点击打开工作室，不进 Chat SSE。
