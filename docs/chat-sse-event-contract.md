# Chat SSE 事件契约

> 状态：2026-06-26 收口版。本文定义 `/api/chat/send`、`/api/chat/resume` 与 `/api/chat/runs/{run_id}/stream` 可被前端依赖的事件。新增事件前必须先更新本文档。

## 基本规则

- SSE 事件名使用全大写下划线，例如 `TEXT_BLOCK_DELTA`。
- 事件 payload 对外字段使用 camelCase；透传 AgentScope 原始事件时可保留 snake_case。
- 前端只消费本文档列出的稳定事件；调试类或 trace 类事件应进入 `run_events`，不要直接驱动 Chat UI。
- 应用级状态统一通过 `RUNTIME_EVENT` 表达。AgentScope 原始事件只负责流式文本、思考和底层调试，不承载业务状态。
- 新功能必须定义独立 Runtime Event 家族，不允许混用其他功能事件。例如记忆使用 `MEMORY_*`，产物使用 `ARTIFACT_*`，沙箱使用 `SANDBOX_*`。
- Plan Runtime 与 Subagent Runtime 已下线，`PLAN_*`、`SUBAGENT_*` 事件不属于当前稳定契约。

## Runtime Event

`RUNTIME_EVENT` 是业务状态事实日志，payload 固定为：

- `id`、`runId`、`sessionId`：事件与 Run 关联信息。
- `type`：应用级事件类型，例如 `RUN_STARTED`、`TOOL_CALL_STARTED`。
- `scope`：`run|single|leader|worker|team|skill|memory|artifact|sandbox`。
- `source` / `sourceEventType`：来源系统与原始事件类型。
- `agentId`、`agentName`、`teamActivityId`：可选执行主体。
- `status`：`running|waiting|completed|failed|cancelled|pending`。
- `reason`、`durationMs`、`payload`：补充信息。

当前稳定 Runtime Event 家族：

- `RUN_*`：`RUN_STARTED`、`RUN_WAITING`、`RUN_RESUMED`、`RUN_COMPLETED`、`RUN_FAILED`、`RUN_CANCELLED`。
- `AGENT_*`：模型调用、输出、等待人工、恢复、完成、失败等 Agent 状态。
- `TOOL_*`：`TOOL_CALL_STARTED`、`TOOL_CALL_DELTA`、`TOOL_CALL_COMPLETED`、`TOOL_RESULT_STARTED`、`TOOL_RESULT_DELTA`、`TOOL_RESULT_COMPLETED`。
- `TEAM_*`：Team 初始化、Worker 调度/等待/完成/失败、Leader 汇总、Team 完成/失败。
- `SKILL_*`：Skill 开始、完成、失败。
- `HITL_*` / `CLARIFICATION_*`：人工确认与反问采集。
- `MEMORY_*`：记忆总结与候选记忆生命周期。
- `ARTIFACT_*`：文件、下载产物、生成内容等可交付产物生命周期。
- `SANDBOX_*`：沙箱运行生命周期。

## Run 生命周期

- `RUN`：后端已创建后台 Run。必须包含 `run_id` 或 `runId`，建议包含 `created_at`。
- `RUN_CANCELLED`：用户取消本轮执行。必须包含 `completed_at` 或 `completedAt`。
- `ERROR`：本轮执行失败。必须包含 `message`。

## 模型与文本输出

- `MODEL_CALL_START`：模型调用开始。建议包含 `model_name`。
- `MODEL_CALL_END`：模型调用结束。可包含 `input_tokens`、`output_tokens`、`cache_creation_input_tokens`、`cache_input_tokens`。
- `TEXT_BLOCK_DELTA`：最终回复文本增量。必须包含 `delta`。
- `REASONING_DELTA`：ReAct 过程文本增量。必须包含 `delta`。主 Leader 新链路会优先发送该事件，避免过程文本混入最终答案。
- `FINAL_ANSWER_DELTA`：最终答案文本增量。必须包含 `delta`。由 `FinalAnswerStart` 阶段工具触发后发送。
- `THINKING_BLOCK_DELTA`：深度思考文本增量。必须包含 `delta`。
- `REPLY_END`：AgentScope 回复结束。注意 SSE 结束才代表本轮后处理全部完成，`REPLY_END` 后仍可能出现记忆总结等事件。

## 工具调用

- `TOOL_CALL_START`：工具调用开始。必须包含 `tool_call_id`，建议包含 `tool_call_name`。
- `TOOL_CALL_DELTA`：工具参数增量。必须包含 `tool_call_id`、`delta`。
- `TOOL_CALL_END`：工具参数结束。必须包含 `tool_call_id`。
- `TOOL_RESULT_START`：工具结果开始。必须包含 `tool_call_id`。
- `TOOL_RESULT_TEXT_DELTA`：工具结果文本增量。必须包含 `tool_call_id`、`delta`。
- `TOOL_RESULT_END`：工具结果结束。必须包含 `tool_call_id`。

## Final Answer 阶段切换

- 后端默认注入 `FinalAnswerStart` 系统工具。
- Agent 在完成分析、工具调用、反问等步骤后，必须先调用 `FinalAnswerStart`，随后再输出最终答案正文。
- `FinalAnswerStart` 只负责阶段切换，不承载最终答案内容，不在 Chat 工具调用列表中展示。
- 调用该工具前的普通文本会转换为 `REASONING_DELTA` / `AGENT_REASONING_DELTA`；调用后的普通文本会转换为 `FINAL_ANSWER_DELTA` / `AGENT_FINAL_ANSWER_DELTA`。

## Skills

- `SKILL_CONTEXT`：本轮注入的 Skill 上下文。必须包含 `skills` 数组。
- `SKILL_END`：本轮 Skill 使用结束。必须包含 `skills` 数组，可包含 `status=completed|failed`。

## Team 协作

- `TEAM_ACTIVITY`：Team 成员状态快照。必须包含 `activities` 数组。前端按 `id` 或 `agentId` 合并同一成员状态。
- `TEAM_MEMBER_EVENT`：兼容旧前端的 Worker ReAct 子事件。新代码应优先消费 `RUNTIME_EVENT` 中的 `AGENT_*` / `TOOL_*` / `TEAM_*`，不要再新增依赖。

## 用户介入

- `HITL_REQUIRED`：需要用户审批或补充外部执行结果。必须包含 `interactionId`、`type`、`sessionId`、`agentId`、`agentName`、`replyId`、`toolCalls`。
- `CLARIFICATION_REQUIRED`：模型反问采集信息。必须包含 `id`、`title`、`questions`，可包含 `description`。

## 后处理

- 记忆、产物、沙箱等后处理均通过 `RUNTIME_EVENT` 的独立家族表达：`MEMORY_*`、`ARTIFACT_*`、`SANDBOX_*`。

## 兼容字段

- 历史消息中仍可能存在 `planRuns`、`subagentCalls` 字段。当前前端不展示、不执行这些字段；后端仅保留读写兼容，避免旧 SQLite 数据导致接口失败。
