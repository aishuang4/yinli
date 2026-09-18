# Design

## Context

现行写入在 `trace_recorder._record_run_event` → `run_events`，只挂 `session_id`。`GET /api/traces` 把 `chat_sessions.id` 当 trace，状态由最近事件与消息启发式揉合；详情再塞最近 12 条 `chat_messages`（`tool_calls` 已恒为空）和分页 `run_events`。`leader_stream` 对同一步既写 AgentScope 名（`TOOL_CALL_START`）又写 `RUNTIME_EVENT`（`TOOL_CALL_STARTED`）。Chat 展示契约（RuntimeFrame / Snapshot V4）保持不动。动机见 proposal.md，行为见 specs/traces。

`chat_runs` 已有 id、session、project、status、created/completed。缺的是一步一闭环的 Observation 存储，以及按 Run 查询的 API。

## Goals / Non-Goals

**Goals:**

- 用一张 Observation 表（或等价结构）表达 Langfuse 式步骤：`runId`、类型、parent、起止、input/output。
- 运行时在模型调用、工具、Skill、HITL、Subagent、Team 任务的边界开闭这些步骤，不再双写碎事件当 Trace 主数据。
- `/api/traces` 改为 Run 列表 + 该 Run 的 Observation 树；前端第一层墙钟时间线，第二层 input/output。

**Non-Goals:**

- 不改 Chat SSE、不把 Observation 推给 Chat reducer。
- 不接 Langfuse / OpenTelemetry 导出，不做评测与 Dataset。
- 不把群聊 `GROUP_*` 写入 Chat Trace。
- 不在本变更把 Memory 页的 `MEMORY_AUDIT` 横查改掉（可继续读旧审计表）。
- 不回填历史 `run_events` 为 Observation；旧 Session 级 URL 直接作废。

## Decisions

### D1. 新表 `trace_observations`，不复用 `run_events` 当主模型

`run_events` 是追加型审计行（无 `run_id`、无起止、无类型枚举）。硬加列再同时表示「步骤」和「碎事件」会让查询与前端再做一次聚合。

主路径改为一行一步：

- `id`, `run_id`, `session_id`, `workspace_id`, `project_id`
- `kind`（`run|generation|tool|skill|approval|subagent|team-task`）
- `name`, `parent_id`, `status`
- `started_at`, `ended_at`, `duration_ms`
- `input_json`, `output_json`, `metadata_json`

索引：`(project_id, started_at DESC)` 给列表旁路、`(run_id, started_at)` 给详情。

`run_events` 本变更可继续收 Memory / 可选诊断，但 **Trace API 不再把它当第一层数据源**。权限策略、Intent、CONTEXT_USAGE 若仍要留审计，可写 `run_events` 或挂在对应 Observation 的 `metadata`，不进时间线。

备选：给 `run_events` 加 `run_id` 由前端拼 start/end。否决：与「一步一条、第二层是字段不是子事件」冲突。

### D2. 写入放在现有边界，不扫 Chat Snapshot 反推

开闭 Observation 的位置与 Chat 已知生命周期对齐，避免第二套推断：

| 步骤 | 打开 | 关闭 |
|---|---|---|
| `run` | `start_chat_background_run` 成功 | Run 终态（completed/failed/cancelled） |
| `generation` | `MODEL_CALL_START` / Reply 内模型开始 | `MODEL_CALL_END`；output 取本轮累计 reasoning / tool_calls 声明 / 已开始的最终文本摘要 |
| `tool` | `TOOL_CALL_END`（参数齐）或首次拿到 call id | `TOOL_RESULT_END`；HITL 拒绝则 failed |
| `approval` | `HITL_REQUIRED` / AskUser | `HITL_RESOLVED` 或 Run 取消 |
| `skill` | 现有 Skill 开始 | Skill 结束 |
| `subagent` | invocation 创建 | 终态且可带 consumed |
| `team-task` | Team 任务进入 running | 任务终态 |

进行中的步骤 `ended_at` 为空，时间线画到「现在」或 Run 当前时刻。写入包 try，失败只打日志。

`leader_stream` 去掉「AS 名 + RUNTIME_EVENT」双写进 Trace 主路径；Chat 投影仍吃原来的 SourceEvent，不受影响。

备选：从 DisplaySnapshot 离线投影 Observation。否决：Snapshot 没有可靠墙钟，工具 delta 也不是闭环。

### D3. API 以 Run 为资源

- `GET /api/traces?projectId=` → 当前 Project 的 Chat Run 摘要（最多与现网同量级，如 100），`id=chat_runs.id`，`status` 原样，`durationMs` 由 `completed_at - created_at`（进行中用 now）。
- `GET /api/traces/{runId}?projectId=` → 摘要 + `observations[]`（该 Run 全量步骤；单轮数量远小于旧 `run_events`，首期不分页）。
- 不再返回 `messages[].toolCalls` 作为主展示，也不再分页 dump `run_events`。
- 用 session id 打详情 → 404。

前端按 Project 过滤已有；Run 无 Observation 时仍显示总条（只有根 `run` 或空轴 + 状态）。

### D4. 前端：左/上时间线，右/下详情

替换 `TraceLandingPage` 的「历史运行 = Session」和 `TraceEventTimeline` 的事件筛选。

- 列表：任务摘要、Agent、mode、状态、总耗时。
- 详情：一条水平（或垂直）墙钟轴；每段按 `startedAt/endedAt` 定位；`approval` 用等待色；重叠段换行/泳道（Team / 并行 tool）。
- 选中段：Input / Output / 少量 metadata。默认选中根 `run`。
- 去掉以 `eventType === 'RUNTIME_EVENT'` 为「Runtime」筛选的主 UI；去掉依赖 `message.toolCalls` 的工具卡主路径。

树与轴是同一份 `observations`：有 `parentId` 的缩进可选，但第一层默认按时间排。

### D5. 与 Chat Block 对齐但不共用协议

| Observation | Chat Block |
|---|---|
| `run` | `message` 根 |
| `generation` | `thinking` / `reasoning` / 未完成的模型轮 |
| `tool` | `tool` |
| `skill` | `skill` |
| `approval` | `approval` |
| `subagent` | `subagent` |
| `team-task` | `team-task` |

只对齐单位，不共享 Frame。Trace 可以比 Chat 多耗时和 input 全文；Chat 继续流式。

### D6. 旧数据

不迁移 `run_events` → Observation。上线后只有新 Run 有完整时间线。旧 Session 级书签 404。`run_events` 表可留，避免 Memory 审计立刻坏掉。

## Risks / Trade-offs

- [进行中 Run 的时间线要刷新] → 打开详情时若 status 非终态，前端短轮询详情或沿用现有刷新按钮；不做 Chat SSE。
- [模型 output 太大] → input/output 落库前截断（与现 Trace 千字级裁剪同量级），完整正文仍以 Chat 历史为准。
- [Team 并行不好画] → 首期允许重叠条 + parent=`team-task`；不做独立甘特产品。
- [双写期 Chat 仍产碎 run_events] → 主 API 不读它们；后续可再停写以减小库。
- [resume 跨进程] → Observation 按 `run_id` 追加，与 HITL 同 Run 一致。

## Migration Plan

1. 建 `trace_observations` + 写入辅助；Run 开始/结束写根节点。
2. 接 generation / tool / approval，再接 skill / subagent / team-task。
3. 切换 `/api/traces` 与前端；产品档行为保持。
4. 回滚：恢复旧 traces 路由读 `run_events`（代码回退）；新表可留。

## Open Questions

- 定时任务 Job firing 是否出现在同一 Trace 列表：现 `chat_runs.trigger_source` 已区分，首期列表可含 Job Run 或只展示 `trigger_source=user`。实现时按产品档 `views.traces` 默认展示全部 Chat Run（含 job），若过吵再加筛选。不阻塞本变更规格。
