# Chat Trace Observation 契约

> 状态：2026-09-18 落地。Trace 页按一次 Chat Run 展示墙钟时间线；Chat SSE 不变。

## 身份

- Trace `id` = `chat_runs.id`。
- 同一 Session 的多次送出是多条 Trace。
- 用 `chat_sessions.id` 请求详情返回 404。
- 列表只含当前 `projectId`。
- 状态直接读 Chat Run 生命周期（`running` / `waiting_*` / `completed` / `failed` / `cancelled`），不再从事件启发式推断。
- 产品档关闭 `views.traces` 时 `/api/traces` 仍 404；Chat `/api/chat/runs` 不受影响。

## Observation

一步一条，存在 `trace_observations`：

| 字段 | 含义 |
|---|---|
| `kind` | `run` / `generation` / `tool` / `skill` / `approval` / `subagent` / `team-task` |
| `parentId` | 通常为根 `run`；Team 成员工具可挂在 `team-task` |
| `startedAt` / `endedAt` / `durationMs` | 墙钟。未结束则 `endedAt` 为空 |
| `input` / `output` / `metadata` | 第二层详情；落库前截断 |

`generation` 与它触发的 `tool` 是兄弟，不把工具 stdout 收进模型步。不把 `TOOL_CALL_START` / delta / `RUNTIME_EVENT` 写成独立 Observation。

`generation.input` 是 `MODEL_CALL_START` 时即将发给模型的内容：`system` 用 AgentScope `_get_system_prompt()`（静态规则 + 本轮 `<agent-skills>` 目录，含 name / description / dir），`skills` 是同一份目录的结构化列表，`summary` / `messages` 来自 `state.context`。`HintBlock`（时间、Skill 补回等）按 formatter 规则展开成 `messages` 里的 user 消息，不再单独标 `type=hint`。`generation.output` 等 thinking / 文本 / 工具声明齐了再关：`MODEL_CALL_END` 只补 `usage`（读 `input_tokens` / `output_tokens`），`REPLY_END`、`HITL_REQUIRED`、`TOOL_RESULT_START` 或下一轮 `MODEL_CALL_START` 才闭环。`output.toolCalls` 是模型这一步声明要调的工具；工具执行结果仍在兄弟 `tool` 步，stdout 来自 `TOOL_RESULT_TEXT_DELTA` 累计，不依赖 `TOOL_RESULT_END.output`。

第一层时间线一行一步：左侧步骤名与耗时，右侧按整轮墙钟对齐，不再把重叠步骤挤进同一条。

## 与 Chat Block 对齐

| Observation | Chat Block |
|---|---|
| `run` | `message` 根 |
| `generation` | `thinking` / `reasoning` / 模型轮 |
| `tool` | `tool` |
| `skill` | `skill` |
| `approval` | `approval` |
| `subagent` | `subagent` |
| `team-task` | `team-task` |

只对齐单位，不共享 RuntimeFrame。Chat 仍只收 `RUNTIME_EVENT`。

## 不进时间线

Intent / Readiness、Workspace 权限策略、`CONTEXT_USAGE`、模型 fallback 细节、`MEMORY_AUDIT` 仍可写 `run_events`。Memory 页继续横查 `MEMORY_AUDIT`。群聊 `GROUP_*` 不进 Chat Trace。

## API

- `GET /api/traces?projectId=`：当前 Project 的 Run 摘要（`durationMs`、任务摘要、状态）。
- `GET /api/traces/{runId}?projectId=`：摘要 + `observations[]`。
- 旧 Session 级详情与 `/{id}/events` 分页已删除。
- `persist=false`（Sandbox）不写 Observation；写入失败不打断 Chat。

## 前端

第一层是总耗时条 + 可重叠时段；`approval` 等待色。点选第二层只渲染该步 input / output / metadata。默认选中根 `run`。进行中 Run 短轮询详情，不接 Chat SSE。
