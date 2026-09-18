# Proposal

## Why

现行 Trace 按 Chat Session 聚合 `run_events`，状态靠启发式猜测，列表当「单次运行」但实际是整段对话。页面同时倒审计碎事件和已停写 `tool_calls` 的消息卡，既对不齐 Chat，也看不出每一步耗时。用户需要的是 Langfuse 那一类观测：一次 Chat Run 一条时间线，点一步看 input / output。

## What Changes

- Trace 身份改为 **Session → Run（一次送出或 resume）→ Observation（一步）**。列表是当前 Project 下的 Chat Run，不再用 `chat_sessions.id` 当 `trace_id`。
- **BREAKING**：`GET /api/traces` / `GET /api/traces/{id}` 的 `id` 改为 `chat_runs.id`；旧的 session 级详情 URL 不再有效。
- 运行时写入闭环 Observation：名称、类型、起止、耗时、状态、input、output、可选 parent。一次模型调用一条 `generation`，一次工具一条 `tool`，二者为兄弟，挂在本轮根或 agent 步骤下。
- 第一层 UI 是该 Run 的时间线（整轮墙钟 + 可重叠时段），只展示 Chat 对得上的步骤，不展示 Intent / Readiness / Workspace / 权限策略 / Context 碎片 / `RUNTIME_EVENT` 包层。
- 第二层是选中 Observation 的 input / output（及必要 metadata）。工具不是再列 `start / call / end` 三条事件：`call` 参数在 input，结果在 output。
- 等人（AskUser / 权限）是一等 `approval` 步骤，与执行时段分色。Team 任务、Subagent 可作为父节点，子步骤嵌套。
- 不引入评测、Annotation、Dataset、Prompt 版本、Playground，也不给群聊工作室写 Chat Trace。
- Chat SSE / DisplaySnapshot V4 契约不变。Chat 仍负责进行中展示；Trace 负责回放耗时与步骤详情。

## Capabilities

### New Capabilities

- `traces`：Chat Run 级观测的写入、查询与两层展示（时间线 + Observation 详情）。

### Modified Capabilities

- （无。现有主 spec 只有 `group-studio` 与 `skills`；群聊继续不进 Chat Trace，Skill 装配不变。）

## Impact

- 后端：`run_events` 或新观测表带 `run_id` / 起止 / 类型 / input / output；`trace_recorder` 与 `leader_stream` 改为一步一闭环，去掉 AS 名与 `RUNTIME_EVENT` 双写；`/api/traces` 按 Run 列表，状态读 `chat_runs.status`。
- 前端：`features/traces` 从 Session 列表 + 事件时间线改为 Run 列表 + 墙钟时间线 + 右侧/下层 input-output；不再渲染空的 `toolCalls` 消息卡作为主路径。
- 文档：补 `docs` 下 Trace 契约（Observation 类型、与 Chat Block 的对应、不进时间线的审计项）。
- 不改：Chat `/send` / Frame 投影、群聊 `GROUP_*`、Memory 页对 `MEMORY_AUDIT` 的横查可继续读审计表，但不进 Trace 第一层。
