# Group Chat Studio 事件契约

> 状态：2026-08-26 生效。实现不得偏离下表。
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
| `GROUP_FLOOR` | 是 | `memberId`, `runId`, `reason`: `host_assign\|mention\|human_return\|host_default` |
| `GROUP_MESSAGE_OPEN` | 是 | `messageId`, `speakerId`, `role`, `intent`, `visibleInTimeline` |
| `GROUP_MESSAGE_DELTA` | **否** | `messageId`, `text` |
| `GROUP_MESSAGE_CLOSE` | 是 | `messageId`, `body`（全文）, `outcome`: `completed\|cancelled\|failed` |
| `GROUP_ACTIVITY_OPEN` | 是 | `runId`, `memberId`, `toolName?` |
| `GROUP_ACTIVITY_DELTA` | 否 | `runId`, `memberId`, `text` |
| `GROUP_ACTIVITY_CLOSE` | 是 | `runId`, `memberId`, `toolName?` |
| `GROUP_BUDGET` | 是 | 完整 `GroupBudget` |
| `GROUP_CIRCUIT` | 是 | `reason`: `max_turns\|no_progress\|deadlock`, `usedTurns`, `noProgressTurns` |
| `GROUP_INTERRUPT` | 是 | `runId`, `source`: `human_message\|stop_button\|task_card\|pause` |
| `GROUP_APPROVAL` | 是 | 完整 `GroupApproval`（含 `replyId`、`toolCalls`） |
| `GROUP_FILE` | 是 | 完整 `GroupArtifact` |
| `GROUP_DOD` | 是 | `taskCardId`, `dodChecklist` |
| `GROUP_ERROR` | 是 | `code`, `message` |
| `GROUP_RUN_ENDED` | 是 | `runId`, `status` |

**DELTA 不落 SQLite。** 只持久化 OPEN / CLOSE / 状态类事件。运行中 `group_messages.body` 可由运行时每 256 字符或 200ms upsert 一次，刷新不靠 DELTA replay。

禁止新增未列入本表的前端事件名。P1 时间旅行对象不得命名为 `GROUP_SNAPSHOT`，以免与 `GROUP_STUDIO_STATE` 口语撞车。

## 4. 隔离约束

- 不注册 Chat projector，不写 `run_events`。
- 不把群事件写入 `POST /api/chat/send` 流。
- 通知走 `src/notifications/`，kind 前缀 `group_studio_`，不经过本契约。
- 前端只依赖本文件中的稳定事件与 `GET /api/group-studio/{id}` 快照。
