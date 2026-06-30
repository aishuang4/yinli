# Chat SSE 事件契约

> 状态：2026-06-30 RunState 中间层过渡版。本文定义 `/api/chat/send/start`、`/api/chat/send`、`/api/chat/resume/start`、`/api/chat/resume`、`/api/chat/plans/{plan_id}/approve`、`/api/chat/plans/{plan_id}/deny`、`/api/chat/runs/{run_id}/stream`、`/api/chat/runs/{run_id}/state` 与 `/api/chat/runs/{run_id}/state/stream` 可被前端依赖的事件和状态。新增事件前必须先更新本文档。

## 基本规则

- SSE 事件名使用全大写下划线，例如 `TEXT_BLOCK_DELTA`。
- 事件 payload 对外字段使用 camelCase；透传 AgentScope 原始事件时可保留 snake_case。
- 前端只消费本文档列出的稳定事件；调试类或 trace 类事件应进入 `run_events`，不要直接驱动 Chat UI。
- 应用级状态统一通过 `RUNTIME_EVENT` 表达。AgentScope 原始事件只负责流式文本、思考和底层调试，不承载业务状态。
- Chat 主界面应逐步迁移到 `RunState` 中间层；原始 SSE 事件保留为兼容增量和 Trace 输入，不再作为长期 UI 状态的唯一来源。
- 新增 Chat UI 功能只能依赖 RunState 字段；不得新增对 `TEXT_BLOCK_DELTA`、`TOOL_*`、`TEAM_ACTIVITY`、`TEAM_MEMBER_EVENT` 等 raw SSE 分支的主链路依赖。
- 当前前端 live handler 只消费 `RUN_STATE`、`RUN_STATE_PATCH`、`ERROR`、`RUN_CANCELLED`；其他 raw SSE 只允许后端投影器、Trace 或调试入口消费。
- 新功能必须定义独立 Runtime Event 家族，不允许混用其他功能事件。例如记忆使用 `MEMORY_*`，产物使用 `ARTIFACT_*`，沙箱使用 `SANDBOX_*`。
- 旧 Plan Runtime 与 Subagent Runtime 已下线，旧 `PLAN_*`、`SUBAGENT_*` 事件不属于当前稳定契约；新的 Plan Mode 事件只通过本文定义的 `RUNTIME_EVENT` 契约表达。

## Chat 路由分层

- Readiness Gate 是进入 architecture 前的前置判断，由系统内置 `ReadinessAgent` 判断本轮信息是否足够开始执行。若信息不足，后端直接返回 `CLARIFICATION_REQUIRED` 并停止进入 Runtime。
- 第一层是 architecture，由前置意图识别决定。当前在线值为 `react`；`plan`、`subagent` 后续重新设计后再进入稳定契约。`clarification` 不是 architecture runtime，只是运行前等待补充信息的状态。
- 第二层是 executor，由前端选择机械决定。选择单 Agent 时为 `single_agent`，选择 Team 时为 `team`，模型不得把单 Agent 改成 Team 或把 Team 改成单 Agent。
- `INTENT_ROUTE` trace payload 必须包含 `runtime`、`executor` 与 `readiness_gate` decision，其中 `runtime.kind` 表示架构模式，`executor.kind` 表示执行主体。

## ReAct Runtime 链路

- `/api/chat/send/start` 创建后台 Run 并返回 JSON 元数据；Chat 主界面随后订阅 `/api/chat/runs/{run_id}/state/stream`。
- `/api/chat/send` 保留原始 SSE 兼容入口；新前端不应再用它驱动 Chat 主状态。
- `/api/chat/resume/start` 在后台恢复 HITL Run 并返回原 `runId`；Chat 主界面随后订阅 `/api/chat/runs/{run_id}/state/stream`。
- `/api/chat/resume` 保留原始 SSE 兼容入口；新前端不应再用它驱动 Chat 主状态。
- Runtime 执行入口只负责构建 `ChatRunContext`、输出起始事件和启动校验。
- `runtime_dispatcher.execute_chat_runtime()` 只按第一层 `runtime.kind` 分发。当前仅 `react` 在线；信息不足时在进入 Runtime 前由 send 直接返回反问事件。
- `react_runtime.execute_react_runtime()` 是 ReAct 唯一执行入口。它先读取第二层 `executor.kind`：`single_agent` 直接进入主 Agent，`team` 先运行 Worker，再把 Worker 输出拼入 Leader 上下文。
- ReAct Runtime 内部仍复用统一的 `LeaderStreamState`、`RUNTIME_EVENT`、成功/失败收尾与历史消息持久化，不为 Team 另开独立 SSE 契约。

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
- `TOOL_*`：`TOOL_CALL_STARTED`、`TOOL_CALL_DELTA`、`TOOL_CALL_COMPLETED`、`TOOL_RESULT_STARTED`、`TOOL_RESULT_DELTA`、`TOOL_RESULT_COMPLETED`（工具失败时 status 为 `failed`）。
- `TEAM_*`：Team 初始化、Worker 调度/等待/报告/完成/失败、Leader 汇总、Team 完成/失败。
- `SKILL_*`：Skill 开始、完成、失败。
- `HITL_*` / `CLARIFICATION_*`：人工确认与反问采集。
- `MEMORY_*`：记忆总结与候选记忆生命周期。
- `ARTIFACT_*`：文件、下载产物、生成内容等可交付产物生命周期。
- `SANDBOX_*`：沙箱运行生命周期。
- `PLAN_*`：Plan Mode 进入、更新、等待审批和工具拦截状态。

## Plan Mode Runtime Events

Plan Mode 基于 AgentScope Middleware hooks 实现，不恢复旧 Plan Runtime。所有事件都通过 `RUNTIME_EVENT` 发送，`scope` 使用 `plan`。

Plan Mode 内部只产生 `PlanDomainEvent`；跨出 Plan middleware / Plan tools 边界前，必须统一转换为 `RUNTIME_EVENT`。前端不得依赖 middleware 内部事件结构，也不得直接解析 Plan 工具结果作为主状态来源。

| type | status | payload |
| --- | --- | --- |
| `PLAN_MODE_ENTERED` | `running` | `{ planId, planFile, mode }` |
| `PLAN_UPDATED` | `running` | `{ planId, planFile, markdown, source }` |
| `PLAN_WAITING_APPROVAL` | `waiting` | `{ planId, planFile, markdown }` |
| `PLAN_APPROVED` | `completed` | `{ planId, planFile }` |
| `PLAN_DENIED` | `failed` | `{ planId, planFile, reason }` |
| `PLAN_TOOL_DENIED` | `completed` | `{ planId, toolCallId, toolName, message }` |
| `PLAN_TASKS_UPDATED` | `running` | `{ planId, planFile, steps }` |
| `PLAN_TASK_OUTPUT` | `running|completed|failed` | `{ planId, planFile, step, steps }` |

`steps` 使用现有 `ChatPlanStep` camelCase 字段：`id`、`planId`、`stepIndex`、`title`、`description`、`status`、`assignedAgentId`、`evidence`、`result`、`startedAt`、`completedAt`、`updatedAt`。

### Plan Mode 审批与执行

- `POST /api/chat/plans/{plan_id}/approve`：批准 `waiting_approval` 状态的 Plan，返回 `{ plan, runtimeEvent }`，其中 `runtimeEvent.type` 为 `PLAN_APPROVED`。
- `POST /api/chat/plans/{plan_id}/deny`：拒绝 `waiting_approval` 状态的 Plan，body 为 `{ reason?: string }`，返回 `{ plan, runtimeEvent }`，其中 `runtimeEvent.type` 为 `PLAN_DENIED`。
- 前端批准后发起新一轮 `/api/chat/send`，请求体带 `approvedPlanId`。后端在 ReAct Runtime 内加载该 approved plan，通过 Plan Mode middleware 注入 Build mode 提示和计划正文，然后由同一个主 Agent 按计划执行。
- `approvedPlanId` 只表示"本轮按已批准计划执行"，不是新的 architecture runtime；`intent_router.runtime.kind` 仍为 `react`。

### Plan Mode 执行链路详解

1. **调查与计划阶段（Plan Mode）**：Agent 调用 `plan_enter` 进入 Plan Mode。该模式下，`PlanModeMiddleware.on_acting` 拦截变更型工具（`Write`、`Edit`、`Bash` 变更命令等），阻止其真实执行，并发送 `PLAN_TOOL_DENIED` Runtime Event 告知前端。Agent 只能执行只读工具（`Read`、`Grep`、`Glob`、只读 `Bash`/`HttpRequest`）和计划控制工具（`plan_enter`、`plan_write`、`plan_exit`、`FinalAnswerStart`）。

2. **请求审批**：Agent 调用 `plan_exit` 将计划置为 `waiting_approval` 状态，发送 `PLAN_WAITING_APPROVAL` Runtime Event。此后 Agent 停止本轮执行，前端展示审批操作入口（批准/拒绝）。

3. **审批执行阶段（Build Mode）**：
   - 用户批准后，前端调用 `POST /api/chat/plans/{plan_id}/approve`，后端返回 `PLAN_APPROVED`。
   - 前端发起新一轮 `/api/chat/send`，请求体携带 `approvedPlanId`。
   - 后端 ReAct Runtime 加载该 approved plan，`PlanModeMiddleware` 将 system prompt 切换为 Build mode（放行所有工具），并将计划正文注入 Leader 上下文。
   - 主 Agent 按计划执行，此时无工具拦截，所有工具调用真实生效。
   - 用户拒绝时，前端调用 `POST /api/chat/plans/{plan_id}/deny`，后端返回 `PLAN_DENIED`，本轮计划作废。

## Run 生命周期

- `RUN`：后端已创建后台 Run。必须包含 `run_id` 或 `runId`，建议包含 `created_at`。
- `RUN_CANCELLED`：用户取消本轮执行。必须包含 `completed_at` 或 `completedAt`。
- `ERROR`：本轮执行失败。必须包含 `message`。

## RunState 中间层

`GET /api/chat/runs/{run_id}/state` 返回某个 Run 的最新前端状态快照。该接口用于刷新、断线恢复和后续替代 Chat 主界面对原始 SSE 的直接依赖。

`GET /api/chat/runs/{run_id}/state/stream` 返回 RunState 中间层 SSE。首个事件为 `RUN_STATE` 完整快照，后续事件优先使用 `RUN_STATE_PATCH` 轻量增量。该流是 Chat 主界面恢复运行中任务的推荐入口，前端不需要直接处理工具结果、模型调用等原始事件。

当 RunState 首包快照的 `status` 为 `completed|failed|cancelled` 时，`state/stream` 必须在发送 `RUN_STATE` 后结束连接。`waiting` 表示等待用户操作，不是仍在持续输出的流，但只有快照或 patch 已经包含前端可操作内容时才结束连接，例如 `approvals[]` / `approval` 或 `plans[]` / `plans`。`AGENT_WAITING_HUMAN`、`CLARIFICATION_WAITING` 这类预告等待的 Runtime Event 不得单独关闭流，必须等待随后的 `HITL_REQUIRED` / `CLARIFICATION_REQUIRED` 把可操作数据投影进 RunState。

`RUN_STATE_PATCH` 用于连接中的实时更新，允许丢失；网络断开、页面刷新或前端怀疑状态不一致时，应重新订阅并以新的 `RUN_STATE` 快照为准。每个 patch 必须包含 `kind`，前端必须优先按 `kind` 分支处理，不得再靠猜测可选字段判断 patch 语义。稳定字段包括：

- `kind`：稳定 patch 类型，当前值为 `snapshot_meta|reasoning_delta|final_answer_delta|thinking_delta|tool|team_activities|skill_calls|model_usage|approval|approval_resolved|runtime_event|plans|error`。
- `runId`、`sessionId`、`status`、`currentStage`、`updatedAt`、`completedAt`、`lastEventType`、`eventCount`。
- `reasoningDelta`、`finalAnswerDelta`、`thinkingDelta`：文本增量。
- `tool`：一个轻量工具摘要，字段同 RunState `tools[]`。
- `teamActivities`：Team 活动快照，仅在 Team 活动变化时出现。
- `approval`：一个等待处理的用户介入摘要，字段同 RunState `approvals[]`。
- `runtimeEvent`：最近一个应用级 Runtime Event，用于 Plan Dock、Team 面板等兼容展示。
- `plans`：Plan Mode 当前计划摘要数组，仅在 Plan 事件变化时出现。
- `skillCalls`：Skill 注入/完成状态数组，仅在 Skill 事件变化时出现。
- `modelName`、`modelCaches`、`tokenUsage`：模型调用与用量摘要，仅在模型事件变化时出现。
- `error`：错误摘要。

为避免同一段文本被 `RUNTIME_EVENT:AGENT_*_DELTA` 与 `FINAL_ANSWER_DELTA` / `REASONING_DELTA` 重复累计，RunState 正文投影只以稳定文本事件作为正文增量来源；Runtime delta 只保留在 `runtimeEvent` 中用于过程展示和调试。

后端必须用 `backend/src/server/routes/chat/run_state_schema.py` 校验 `RUN_STATE` 和 `RUN_STATE_PATCH` 输出；前端必须用 `frontend/src/types/runState.ts` 作为主链路类型来源。新增字段时先更新本文档、后端 Pydantic schema、前端 TypeScript 类型，再更新 projector 和测试矩阵。

`GET /api/chat/runs/{run_id}/stream?replay=false` 用于 RunState 恢复后的兼容订阅。`replay=false` 表示只接收订阅之后的新事件，避免前端先恢复快照后又重复消费历史 SSE。默认 `replay=true`，保持旧行为。

RunState 稳定字段：

- `version`：状态版本，当前为 `1`。
- `runId`、`sessionId`、`agentId`、`agentName`、`teamId`、`teamName`、`mode`、`task`。
- `status`：`running|waiting|completed|failed|cancelled|cancelling`。
- `currentStage`：面向用户的当前阶段。
- `reasoning`、`reasoningTruncated`：ReAct 过程文本。可截断。
- `finalAnswer`、`finalAnswerTruncated`：最终答案文本。可截断，完整最终结果以 `chat_messages` 为准。
- `thinking`：深度思考摘要。
- `tools`：轻量工具摘要数组，不包含完整 Observation。
- `teamActivities`：Team worker/leader 当前活动摘要，字段沿用 Chat `TeamActivityInfo` camelCase 结构。
- `skillCalls`：本轮注入/使用的 Skill 摘要。
- `modelName`、`modelCaches`、`tokenUsage`：模型调用与 token/cache 用量摘要。
- `plans`：Plan Mode 当前计划摘要。
- `approvals`：等待用户处理的 HITL / clarification 摘要；每项必须包含 `interactionId`、`kind`、`type`、`status`、`createdAt`。`kind` 表示 UI 交互大类，稳定值为 `hitl|clarification`；`type` 表示该大类内部的交互子类型。clarification 的 `kind` 与 `type` 都必须为 `clarification`，并保留 `title`、`description`、`questions`，不得被问题字段或原始 payload 中的 `type` 覆盖。
- `runtimeEvents`：最近少量应用级事件，用于 Plan Dock、Team 面板等兼容展示。
- `error`、`lastEventType`、`eventCount`、`createdAt`、`updatedAt`、`completedAt`。

`tools[]` 稳定字段：

- `id`、`name`、`status`：工具调用身份和状态。
- `inputPreview`：参数预览，禁止存完整大参数。
- `outputPreview`：结果预览，默认最多保留少量文本。
- `outputSize`、`outputTruncated`：完整结果大小与是否截断。
- `error`、`startedAt`、`completedAt`。

Chat 主界面不得依赖完整工具结果。大体积 Read、Glob、Bash、HTTP、MCP 输出必须通过 Trace 或 Artifact 按需查看，不进入 RunState 常驻状态。

### Raw Event 到 RunState 到前端状态映射

| 后端原始事件 / Runtime Event | RunState 字段 | `RUN_STATE_PATCH` 字段 | 前端 live 状态 | 流语义 |
| --- | --- | --- | --- | --- |
| `CLARIFICATION_REQUIRED` / `CLARIFICATION_WAITING` | `status=waiting`、`approvals[]` | `status=waiting`、`approval` | `clarificationRequests[]` | `CLARIFICATION_REQUIRED` 带 `approval` 后结束，等待用户补充 |
| `HITL_REQUIRED` / `AGENT_WAITING_HUMAN` | `status=waiting`、`approvals[]` | `status=waiting`、`approval` | `hitlRequests[]` | `HITL_REQUIRED` 带 `approval` 后结束，等待用户审批 |
| `PLAN_WAITING_APPROVAL` | `plans[]`、`runtimeEvents[]` | `plans`、`runtimeEvent` | `plans[]` / Plan Dock | 本轮可结束，等待用户批准计划 |
| `PLAN_APPROVED` / `PLAN_DENIED` | `plans[]`、`runtimeEvents[]` | `plans`、`runtimeEvent` | `plans[]` / Plan Dock | 由前端发起下一轮执行或结束 |
| `TOOL_CALL_*` / `TOOL_RESULT_*` | `tools[]` 摘要 | `tool` | `toolCalls[]` | running 继续，终态工具不可保留 running |
| `REASONING_DELTA` | `reasoning` | `reasoningDelta` | `reasoningContent` | running 继续 |
| `FINAL_ANSWER_DELTA` | `finalAnswer` | `finalAnswerDelta` | `finalAnswerContent` | running 继续 |
| `TEAM_ACTIVITY` / `TEAM_*` | `teamActivities[]`、`runtimeEvents[]` | `teamActivities`、`runtimeEvent` | `teamActivities[]` | running 继续 |
| `SKILL_CONTEXT` / `SKILL_END` | `skillCalls[]` | `skillCalls` | `skillCalls[]` | running 继续 |
| `MODEL_CALL_*` | `modelName`、`modelCaches[]`、`tokenUsage` | `modelName`、`modelCaches`、`tokenUsage` | 模型与用量展示 | running 继续 |
| `RUN_COMPLETED` / `RUN_FAILED` / `RUN_CANCELLED` | `status`、`completedAt`、`error` | `status`、`completedAt`、`error` | 停止 streaming | 发送后结束 |

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
- `RUNTIME_EVENT:TOOL_RESULT_COMPLETED`：工具结果结束的应用级事件。若工具调用失败（抛出异常、超时等）或原始工具结果 `state=error|failed|denied`，`status` 必须为 `failed`，payload 需保留 `state` 或 `error` 信息，前端据此在流式阶段将工具节点标记为失败。
- RunState 的工具投影只保留工具摘要和 `outputPreview`，不得把完整 `TOOL_RESULT_TEXT_DELTA` 内容长期放入 Chat UI 状态。

## Final Answer 阶段切换

- 后端默认注入 `FinalAnswerStart` 系统工具。
- Agent 在完成分析、工具调用、反问等步骤后，必须先调用 `FinalAnswerStart`，随后再输出最终答案正文。
- `FinalAnswerStart` 只负责阶段切换，不承载最终答案内容，不在 Chat 工具调用列表中展示。
- 调用该工具前的普通文本会转换为 `REASONING_DELTA` / `AGENT_REASONING_DELTA`；调用后的普通文本会转换为 `FINAL_ANSWER_DELTA` / `AGENT_FINAL_ANSWER_DELTA`。

## Skills

- `SKILL_CONTEXT`：本轮注入的 Skill 上下文。必须包含 `skills` 数组。
- `SKILL_END`：本轮 Skill 使用结束。必须包含 `skills` 数组，可包含 `status=completed|failed`。

## Team 协作

- `TEAM_ACTIVITY`：Team 成员状态快照。必须包含 `activities` 数组。前端按 `id` 或 `agentId` 合并同一成员状态。Worker 活动应区分 `output`（运行时过程输出）与 `report`（提交给 Leader 的正式 TeamSay 报告）；跳过成员使用 `skippedReason` 说明原因。
- `TEAM_MEMBER_EVENT`：兼容旧前端的 Worker ReAct 子事件。新代码应优先消费 `RUNTIME_EVENT` 中的 `AGENT_*` / `TOOL_*` / `TEAM_*`，不要再新增依赖。
- `TEAM_ASSIGNMENT_PLANNED`：Team 已完成本轮 Worker 分派。payload 必须包含 `workerCount`；具体分派内容以随后 `TEAM_ACTIVITY.activities[].assignment/expectedOutput/constraints` 为准。
- `TEAM_WORKER_REPORTED`：Worker 已按 AgentScope TeamSay 语义向 Leader 提交报告。必须包含 `agentId`、`agentName`，payload 建议包含 `to` 与 `reportPreview`。
- `TEAM_REPORT_REVIEWED`：Team 已完成 Worker report 质量检查。payload 必须包含 `acceptedCount` 与 `needsRevisionCount`；具体缺口以 `TEAM_ACTIVITY.activities[].reportReviewStatus/reportReviewNotes/reportReviewGaps` 为准。

## 用户介入

- `HITL_REQUIRED`：需要用户审批或补充外部执行结果。必须包含 `interactionId`、`type`、`sessionId`、`agentId`、`agentName`、`replyId`、`toolCalls`。
  `toolCalls` 是前端展示用列表，可能合并同一回复里仍处于 UI running/calling 的工具；后端恢复 AgentScope 时必须使用内部保存的 AgentScope 原始等待工具集合，不能把展示合并列表整体回传给 `UserConfirmResultEvent` / `ExternalExecutionResultEvent`。
- `CLARIFICATION_REQUIRED`：模型反问采集信息。必须包含 `id`、`title`、`questions`，可包含 `description`。

## 后处理

- 记忆、产物、沙箱等后处理均通过 `RUNTIME_EVENT` 的独立家族表达：`MEMORY_*`、`ARTIFACT_*`、`SANDBOX_*`。

## 兼容字段

- 历史消息中仍可能存在 `planRuns`、`subagentCalls` 字段。当前前端不展示、不执行这些字段；后端仅保留读写兼容，避免旧 SQLite 数据导致接口失败。
