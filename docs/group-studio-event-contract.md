# Group Chat Studio 事件契约

> 状态：2026-09-01 生效。实现不得偏离下表。
> 本文是群聊工作室实时展示、刷新恢复的唯一前端事件契约。
> **不属于 Chat SSE。** 禁止复用 `RUNTIME_EVENT`、DisplaySnapshot V4、Chat projector、`run_events`。

## 1. 稳定边界

- Endpoint：`GET /api/group-studio/{groupId}/events?afterSeq=`
- Media Type：`text/event-stream`
- SQLite `group_studio_events` 是持久事实源；SSE 只推送。
- Heartbeat 使用 comment，风格与 Chat / Workflow 相同，**不是**通知通道的 `: ping`：

```text
: heartbeat
```

heartbeat 不带 `seq`，不进入 `group_studio_events`，也不进入前端 reducer。

连接后若 `afterSeq` 缺省或空洞：先发一条 `GROUP_STUDIO_STATE`（payload = `GET /{id}` 的完整 `GroupStudio`，已含最近 100 条 `messages`），再从持久 seq 续推。刷新恢复 = `GET /{id}` 快照 + `afterSeq` 追状态事件。不要学 Workflow 的 `replay=true` 全量重放。

`GROUP_RUN_ENDED` 表示当前 `GroupRun` 结束，**SSE 连接保持**。归档不靠断连。

## 2. 公共信封

每个 SSE 应用事件的 `data` JSON：

```json
{
  "seq": 12,
  "groupId": "gstu_...",
  "runId": "grun_...",
  "type": "GROUP_FLOOR",
  "status": "discussing",
  "payload": {},
  "createdAt": "2026-08-26T10:00:00+00:00"
}
```

| 字段 | 说明 |
| --- | --- |
| `seq` | 持久事件等于 `group_studio_events.sort_order`。易失 DELTA 使用 `null`，或复用当前 OPEN 的 seq，不占用新持久号。 |
| `groupId` | 工作室 id |
| `runId` | 当前 `GroupRun` id；无活动 Run 时为 `null` |
| `type` | 下表稳定事件名 |
| `status` | 发送时的工作室 `status` |
| `payload` | 该事件类型的必填对象 |
| `createdAt` | ISO-8601 UTC |

对外 JSON 空值一律 `null`。

## 3. 稳定事件

| type | 持久? | payload 必填 |
| --- | --- | --- |
| `GROUP_STUDIO_STATE` | 否（连接首包，可按 GET 重建） | 完整 `GroupStudio`（含 `messages`） |
| `GROUP_STATUS` | 是 | `from`, `to`, `reason` |
| `GROUP_TASK_CARD` | 是 | 完整 `TaskCard` |
| `GROUP_BLACKBOARD` | 是 | 完整 `Blackboard`（必须含全部 `openQuestions`，含已 resolved） |
| `GROUP_MEMBER` | 是 | 完整 `Membership` + `change`: `joined\|muted\|unmuted\|removed\|crashed` |
| `GROUP_FLOOR` | 是 | `memberId`, `runId`, `reason`: `host_assign\|mention\|human_return\|host_default\|queue_next\|claim_round`。可选 `remainingMemberIds` |
| `GROUP_MESSAGE_OPEN` | 是 | `messageId`, `speakerId`, `role`, `intent`, `visibleInTimeline` |
| `GROUP_MESSAGE_DELTA` | **否** | `messageId`, `text`；可选 `kind`: `answer`（缺省）\| `thinking`。思考增量不得写入 `body` |
| `GROUP_MESSAGE_CLOSE` | 是 | `messageId`, `body`（对群可见全文）, `outcome`: `completed\|cancelled\|failed`；可选 `thinking`（思考全文，缺省 `""`） |
| `GROUP_ACTIVITY_OPEN` | 是 | `runId`, `memberId`, `toolName?` |
| `GROUP_ACTIVITY_DELTA` | 否 | `runId`, `memberId`, `text` |
| `GROUP_ACTIVITY_CLOSE` | 是 | `runId`, `memberId`, `toolName?` |
| `GROUP_BUDGET` | 是 | 完整 `GroupBudget` |
| `GROUP_CIRCUIT` | 是 | `reason`: `max_turns\|no_progress\|deadlock`, `usedTurns`, `noProgressTurns` |
| `GROUP_INTERRUPT` | 是 | `runId`, `source`: `human_message\|stop_button\|task_card\|pause` |
| `GROUP_APPROVAL` | 是 | 完整 `GroupApproval`（含 `replyId`、`toolCalls`） |
| `GROUP_FILE` | 是 | 完整 `GroupArtifact` |
| `GROUP_DOD` | 是 | `taskCardId`, `dodChecklist` |
| `GROUP_ERROR` | 是 | `code`, `message`。已知 `code`：`model_not_configured`（缺 API Key，不创建 Run）、`host_failed`、`worker_failed`、`host_required`、`member_muted`、`not_found`、`worker_session_lost`、`intake_not_confirmed`、`intake_not_ready`、`intake_worker_forbidden`、`work_item_claimed`、`work_item_not_owned`、`floor_not_granted` |
| `GROUP_RUN_ENDED` | 是 | `runId`, `status` |
| `GROUP_INTAKE` | 是 | `awaitingConfirm`（bool）, `reason`: `host_request\|confirmed`。普通对话不发本事件 |
| `GROUP_WORK_ITEM` | 是 | `change`: `created\|claimed\|completed\|cancelled\|updated`；`items`: 工作项数组（created 可多项，其余通常一项） |
| `GROUP_SPEAKER_QUEUE` | 是 | `memberIds`（剩余队列或认领轮）, `kind`: `assign\|claim_round\|interrupt\|restatement\|drained`, `pendingMemberId`（`string\|null`） |

`GROUP_SPEAKER_QUEUE.kind=drained` 只在工人队列与认领轮都空、即将综合 Host 之前发一次。`interrupt` / `restatement` / Host 自然结束不发 `drained`。`GROUP_INTAKE` 只在主持人提请确认或人类确认接手时发出，普通对谈不发。

**DELTA 不落 SQLite。** 只持久化 OPEN / CLOSE / 状态类事件。运行中 `group_messages.body` 可由运行时每 256 字符或 200ms upsert 一次，刷新不靠 DELTA replay。

禁止新增未列入本表的前端事件名。P1 时间旅行对象不得命名为 `GROUP_SNAPSHOT`，以免与 `GROUP_STUDIO_STATE` 口语撞车。

intake / 任务台快照字段随 `GROUP_STUDIO_STATE` 与 `GET /{id}` 下发：

| 字段 | 语义 |
| --- | --- |
| `awaitingIntakeConfirm` | drafting 上的布尔，不是新 status。Host 提请确认后为 `true`；人类确认接手后为 `false`。普通对话不自动翻回 `false`。 |
| `speakerQueue` | FIFO 剩余 `memberId[]`。队首同步到 `pendingSpeakerMemberId`。调度只读本队列 / 认领轮，不把 pending 当第二输入。 |
| `claimRoundMemberIds` | 串行认领轮剩余工人。成立条件：Host 回合结束时队列为空，且本回合发布了仍为 `open` 的未指派工作项。 |
| `workItems` | `group_work_items` 投影。`status`：`open \| claimed \| done \| cancelled`。空值字段对外为 `null`。 |
| `pendingSpeakerMemberId` | 队首兼容列；`string \| null`。 |

阶段机与调度规则以 `docs/architecture-overview.md` 为准。

## 4. 隔离约束

- 不注册 Chat projector，不写 `run_events`。
- 不把群事件写入 `POST /api/chat/send` 流。
- 通知走 `src/notifications/`，kind 前缀 `group_studio_`，不经过本契约。
- 前端只依赖本文件中的稳定事件与 `GET /api/group-studio/{id}` 快照。
