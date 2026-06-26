# Chat SSE 事件契约

> 状态：2026-06-26 收口版。本文定义 `/api/chat/send`、`/api/chat/resume` 与 `/api/chat/runs/{run_id}/stream` 可被前端依赖的事件。新增事件前必须先更新本文档。

## 基本规则

- SSE 事件名使用全大写下划线，例如 `TEXT_BLOCK_DELTA`。
- 事件 payload 对外字段使用 camelCase；透传 AgentScope 原始事件时可保留 snake_case。
- 前端只消费本文档列出的稳定事件；调试类或 trace 类事件应进入 `run_events`，不要直接驱动 Chat UI。
- Plan Runtime 与 Subagent Runtime 已下线，`PLAN_*`、`SUBAGENT_*` 事件不属于当前稳定契约。

## Run 生命周期

- `RUN`：后端已创建后台 Run。必须包含 `run_id` 或 `runId`，建议包含 `created_at`。
- `RUN_CANCELLED`：用户取消本轮执行。必须包含 `completed_at` 或 `completedAt`。
- `ERROR`：本轮执行失败。必须包含 `message`。

## 模型与文本输出

- `MODEL_CALL_START`：模型调用开始。建议包含 `model_name`。
- `MODEL_CALL_END`：模型调用结束。可包含 `input_tokens`、`output_tokens`、`cache_creation_input_tokens`、`cache_input_tokens`。
- `TEXT_BLOCK_DELTA`：最终回复文本增量。必须包含 `delta`。
- `THINKING_BLOCK_DELTA`：深度思考文本增量。必须包含 `delta`。
- `REPLY_END`：AgentScope 回复结束。注意 SSE 结束才代表本轮后处理全部完成，`REPLY_END` 后仍可能出现记忆总结等事件。

## 工具调用

- `TOOL_CALL_START`：工具调用开始。必须包含 `tool_call_id`，建议包含 `tool_call_name`。
- `TOOL_CALL_DELTA`：工具参数增量。必须包含 `tool_call_id`、`delta`。
- `TOOL_CALL_END`：工具参数结束。必须包含 `tool_call_id`。
- `TOOL_RESULT_START`：工具结果开始。必须包含 `tool_call_id`。
- `TOOL_RESULT_TEXT_DELTA`：工具结果文本增量。必须包含 `tool_call_id`、`delta`。
- `TOOL_RESULT_END`：工具结果结束。必须包含 `tool_call_id`。

## Skills

- `SKILL_CONTEXT`：本轮注入的 Skill 上下文。必须包含 `skills` 数组。
- `SKILL_END`：本轮 Skill 使用结束。必须包含 `skills` 数组，可包含 `status=completed|failed`。

## Team 协作

- `TEAM_ACTIVITY`：Team 成员状态快照。必须包含 `activities` 数组。前端按 `id` 或 `agentId` 合并同一成员状态。
- `TEAM_MEMBER_EVENT`：Worker 自己的 ReAct 子事件。必须包含 `activityId` 或 `agentId`、`eventType`、`event`。该事件只用于更新对应 Worker 的当前输出、思考和工具调用。

## 用户介入

- `HITL_REQUIRED`：需要用户审批或补充外部执行结果。必须包含 `interactionId`、`type`、`sessionId`、`agentId`、`agentName`、`replyId`、`toolCalls`。
- `CLARIFICATION_REQUIRED`：模型反问采集信息。必须包含 `id`、`title`、`questions`，可包含 `description`。

## 后处理

- `MEMORY_SUMMARY`：自动记忆候选生成。必须包含 `agentId`、`agentName`、`facts`。

## 兼容字段

- 历史消息中仍可能存在 `planRuns`、`subagentCalls` 字段。当前前端不展示、不执行这些字段；后端仅保留读写兼容，避免旧 SQLite 数据导致接口失败。
