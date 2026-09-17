# Group Chat Studio 事件契约

> 状态：2026-09-16 V2。实现不得偏离下表。
> 本文是群聊工作室实时展示、刷新恢复的唯一前端事件契约。
> **不属于 Chat SSE。** 禁止复用 `RUNTIME_EVENT`、DisplaySnapshot V4、Chat projector、`run_events`。
>
> 产品模型见 `docs/group-studio-v2.md`。阶段机、主持人、intake、工作项已从在线契约删除。

## 1. 稳定边界

- Endpoint：`GET /api/group-studio/{groupId}/events?afterSeq=`
- Media Type：`text/event-stream`
- SQLite `group_studio_events` 是持久事实源；SSE 只推送。
- Heartbeat 使用 comment，风格与 Chat / Workflow 相同，**不是**通知通道的 `: ping`：

```text
: heartbeat
```

heartbeat 不带 `seq`，不进入 `group_studio_events`，也不进入前端 reducer。

连接后先发一条 `GROUP_STUDIO_STATE`（payload = `GET /{id}` 的完整 `GroupStudio`，已含最近 100 条 `messages`）。

- `afterSeq` 缺省或空洞：STATE 之后只推 **晚于当前最大持久 seq** 的新事件，不重放历史。
- `afterSeq` 有值：STATE 之后只推 `seq > afterSeq` 的事件。

刷新恢复 = `GET /{id}` 快照 + `afterSeq` 追状态事件。不要学 Workflow 的 `replay=true` 全量重放。

`GroupStudio` 快照含 `modelConfigId`：创建请求记录 Chat 所选模型配置 ID，Worker 在各轮开始时解析该配置并固定本轮连接。历史空值按文件默认模型解析。该字段不包含 Key、地址或完整连接。

工作室右侧“文件”列表中的每个产物都有下载按钮，对应 `GET /api/group-studio/{groupId}/files/{artifactId}/download`。服务端只允许下载已登记且位于当前 Project 根目录内的文件，不接受任意路径。

`GROUP_RUN_ENDED` 表示当前 `GroupRun` 结束，**SSE 连接保持**。结束房间不靠断连。

房间对外状态只有 `open | paused | done`（内部另有 `deleted`）。

## 2. 公共信封

每个 SSE 应用事件的 `data` JSON：

```json
{
  "seq": 12,
  "groupId": "gstu_...",
  "runId": "grun_...",
  "type": "GROUP_FLOOR",
  "status": "open",
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
| `GROUP_STUDIO_STATE` | 否（连接首包，可按 GET 重建） | 完整 `GroupStudio`（含 `messages`、`pin`） |
| `GROUP_STATUS` | 是 | `from`, `to`, `reason`：`created \| human_pause \| human_resume \| human_message \| circuit \| startup \| close \| delete` |
| `GROUP_PIN` | 是 | 完整 pin：`title`, `goal`, `notes`, `updatedAt` |
| `GROUP_MEMBER` | 是 | 完整 `Membership` + `change`: `joined\|muted\|unmuted\|removed\|crashed\|reactivated` |
| `GROUP_FLOOR` | 是 | `memberId`, `runId`, `reason`: `mention\|queue_next`。可选 `remainingMemberIds` |
| `GROUP_MESSAGE_OPEN` | 是 | `messageId`, `speakerId`, `role`, `intent`, `visibleInTimeline` |
| `GROUP_MESSAGE_DELTA` | **否** | `messageId`, `text`；可选 `kind`: `answer`（缺省）\| `thinking`。思考增量不得写入 `body` |
| `GROUP_MESSAGE_CLOSE` | 是 | `messageId`, `body`（对群可见全文）, `outcome`: `completed\|cancelled\|failed`；可选 `thinking`（思考全文，缺省 `""`） |
| `GROUP_ACTIVITY_OPEN` | 是 | `runId`, `memberId`, `toolName?` |
| `GROUP_ACTIVITY_DELTA` | 否 | `runId`, `memberId`, `text` |
| `GROUP_ACTIVITY_CLOSE` | 是 | `runId`, `memberId`, `toolName?` |
| `GROUP_BUDGET` | 是 | 完整 `GroupBudget` |
| `GROUP_CIRCUIT` | 是 | `reason`: `max_turns\|no_progress`, `usedTurns`, `noProgressTurns` |
| `GROUP_INTERRUPT` | 是 | `runId`, `source`: `human_message\|stop_button\|pause` |
| `GROUP_APPROVAL` | 是 | 完整 `GroupApproval`（含 `replyId`、`toolCalls`） |
| `GROUP_FILE` | 是 | 完整 `GroupArtifact` |
| `GROUP_ERROR` | 是 | `code`, `message` |
| `GROUP_RUN_ENDED` | 是 | `runId`, `status` |
| `GROUP_SPEAKER_QUEUE` | 是 | `memberIds`（剩余队列）, `kind`: `assign\|all_round\|queue_next\|interrupt\|drained\|handoff\|rerun`, `pendingMemberId`（`string\|null`） |

`GROUP_SPEAKER_QUEUE.kind=drained` 只在本轮点名说完、不再派人时发一次。停止 / 插话发 `interrupt`，不发 `drained`。工人有效提名发 `handoff`；人类「再来一轮」发 `rerun`。没有综合 Host。

**DELTA 不落 SQLite。** 只持久化 OPEN / CLOSE / 状态类事件。运行中 `group_messages.body` 可由运行时每 256 字符或 200ms upsert 一次，刷新不靠 DELTA replay。

禁止新增未列入本表的前端事件名。

快照字段随 `GROUP_STUDIO_STATE` 与 `GET /{id}` 下发：

| 字段 | 语义 |
| --- | --- |
| `status` | `open \| paused \| done \| deleted` |
| `pauseReason` | `paused` 时为 `user \| startup \| circuit`，否则 `null` |
| `pin` | 置顶备注。空 Goal 不挡发言 |
| `speakerQueue` | 还没轮到的 Worker `memberId[]`。队首是下一位，不是正在说的人 |
| `pendingSpeakerMemberId` | 队列下一位；`string \| null`。不要用来标「正在说」 |
| `currentSpeakerMemberId` | 当前 Run 的发言工人；无 Run 时 `null` |
| `lastTurn` | 上一波人类点名。`{ kind: assign\|all_round, mentions: string[] } \| null` |
| `canRerun` | `open` 且无当前 Run、队列已空、且有 `lastTurn` 时为 true |
| `members` | 未移除的 Worker。没有 Host 行 |

前端必须忽略历史事件名：`GROUP_INTAKE`、`GROUP_WORK_ITEM`、`GROUP_DOD`、`GROUP_BLACKBOARD`、`GROUP_TASK_CARD`。

已知 `GROUP_ERROR.code`：`model_not_configured`、`worker_failed`、`member_muted`、`not_found`、`worker_session_lost`、`no_eligible_speaker`、`no_last_turn`、`studio_busy`、`member_limit`、`already_member`、`studio_not_open`、`chat_run_active`、`group_studio_disabled`。

历史 `GROUP_ERROR` / 重放事件不得再次 toast。

## 4. 隔离约束

- 不注册 Chat projector，不写 `run_events`。
- 不把群事件写入 `POST /api/chat/send` 流。
- 通知走 `src/notifications/`，kind 前缀 `group_studio_`，不经过本契约。
- 前端只依赖本文件中的稳定事件与 `GET /api/group-studio/{id}` 快照。
