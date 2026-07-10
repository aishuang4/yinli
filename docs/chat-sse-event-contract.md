# Chat SSE 事件契约

> 状态：2026-07-02 RunState v2 + DisplaySnapshot v3。本文定义 `/api/chat/send/start`、`/api/chat/send`、`/api/chat/resume/start`、`/api/chat/resume`、`/api/chat/runs/{run_id}/stream`、`/api/chat/runs/{run_id}/state` 与 `/api/chat/runs/{run_id}/state/stream` 可被前端依赖的事件和状态。新增事件前必须先更新本文档。

## 基本规则

- SSE 事件名使用全大写下划线，例如 `TEXT_BLOCK_DELTA`。
- 事件 payload 对外字段使用 camelCase；透传 AgentScope 原始事件时可保留 snake_case。
- 前端只消费本文档列出的稳定事件；调试类或 trace 类事件应进入 `run_events`，不要直接驱动 Chat UI。
- 应用级状态统一通过 `RUNTIME_EVENT` 表达。AgentScope 原始事件只负责流式文本、思考和底层调试，不承载业务状态。
- Chat 主界面应逐步迁移到 `RunState` 中间层；原始 SSE 事件保留为兼容增量和 Trace 输入，不再作为长期 UI 状态的唯一来源。
- 新增 Chat UI 功能只能依赖 RunState 字段；Team UI 只能依赖独立 `teamSnapshot` / `teamEvent`，不得读取单 Agent 展示快照。
- 当前前端 live handler 只消费 `RUN_STATE`、`RUN_STATE_PATCH`、`ERROR`、`RUN_CANCELLED`；其他 raw SSE 只允许后端投影器、Trace 或调试入口消费。
- 新功能必须定义独立 Runtime Event 家族，不允许混用其他功能事件。例如后续重设计记忆时使用新的 `MEMORY_*` 语义，产物使用 `ARTIFACT_*`，沙箱使用 `SANDBOX_*`。
- 记忆模块的详细过程审计使用 `run_events.event_type=MEMORY_AUDIT`，不作为 Chat SSE 事件发送，不驱动 Chat 页面过程展示。
- Chat 侧只允许通过 `RUNTIME_EVENT.type=MEMORY_SKILL_GENERATING` 投影为 `RUN_STATE_PATCH.kind=notice`，展示 “Skills 生成中” 轻量提醒；payload 不得包含 Skill 内容、记忆内容、生成结果或提炼过程。
- 旧 Plan Runtime 与旧 Subagent Runtime 已下线；新的 Plan Mode 与 Subagent 委托能力只通过本文定义的 `RUNTIME_EVENT` 契约表达，不恢复旧 runtime.kind 分支。

## Chat 路由分层

- Readiness Gate 是进入 architecture 前的轻量启动判断，由系统内置 `ReadinessAgent` 判断本轮是否可以启动 Runtime。业务信息缺口不在这里发起用户交互，交给主 Agent 在 ReAct 内调用 `AskUser` 外部工具暂停。
- 第一层是 architecture，由前置意图识别决定。当前在线值为 `react`；`plan`、`subagent` 后续重新设计后再进入稳定契约。`clarification` 不是 architecture runtime，也不是新的 runtime.kind。
- 第二层是 executor，由前端选择机械决定。选择单 Agent 时为 `single_agent`，选择 Team 时为 `team`，模型不得把单 Agent 改成 Team 或把 Team 改成单 Agent。
- `INTENT_ROUTE` trace payload 必须包含 `runtime`、`executor` 与 `readiness_gate` decision，其中 `runtime.kind` 表示架构模式，`executor.kind` 表示执行主体。

## ReAct Runtime 链路

- `/api/chat/send/start` 创建后台 Run 并返回 JSON 元数据；Chat 主界面随后订阅 `/api/chat/runs/{run_id}/state/stream`。
- `/api/chat/send` 保留原始 SSE 兼容入口；新前端不应再用它驱动 Chat 主状态。
- `/api/chat/resume/start` 在后台恢复 HITL / AskUser 外部执行暂停的原 Run 并返回原 `runId`；Chat 主界面随后订阅 `/api/chat/runs/{run_id}/state/stream`。
- `/api/chat/resume` 保留原始 SSE 兼容入口；新前端不应再用它驱动 Chat 主状态。
- Runtime 执行入口只负责构建 `ChatRunContext`、输出起始事件和启动校验。
- `runtime_dispatcher.execute_chat_runtime()` 只按第一层 `runtime.kind` 分发。当前仅 `react` 在线；信息不足时由主 Agent 在 Runtime 内调用 `AskUser`，通过 AgentScope `RequireExternalExecutionEvent` 暂停。
- `react_runtime.execute_react_runtime()` 是 ReAct 唯一执行入口。它先读取第二层 `executor.kind`：`single_agent` 直接进入主 Agent，`team` 委托独立 `TeamRunService`，由 Leader 动态组队、Member 隔离执行并在任务收敛后综合。
- ReAct Runtime 内部仍复用统一的 `LeaderStreamState`、`RUNTIME_EVENT`、成功/失败收尾与历史消息持久化，不为 Team 另开独立 SSE 契约。

## Runtime Event

`RUNTIME_EVENT` 是业务状态事实日志，payload 固定为：

- `id`、`runId`、`sessionId`：事件与 Run 关联信息。
- `type`：应用级事件类型，例如 `RUN_STARTED`、`TOOL_CALL_STARTED`。
- `scope`：`run|single|subagent|skill|memory|artifact|sandbox`。Team 不使用该字段，领域事件改用 `actorId`。
- `source` / `sourceEventType`：来源系统与原始事件类型。
- `agentId`、`agentName`：可选执行主体。
- `status`：`running|waiting|completed|failed|cancelled|pending`。
- `reason`、`durationMs`、`payload`：补充信息。

当前稳定 Runtime Event 家族：

- `RUN_*`：`RUN_STARTED`、`RUN_WAITING`、`RUN_RESUMED`、`RUN_COMPLETED`、`RUN_FAILED`、`RUN_CANCELLED`。
- `AGENT_*`：模型调用、输出、等待人工、恢复、完成、失败等 Agent 状态。
- `TOOL_*`：`TOOL_CALL_STARTED`、`TOOL_CALL_DELTA`、`TOOL_CALL_COMPLETED`、`TOOL_RESULT_STARTED`、`TOOL_RESULT_DELTA`、`TOOL_RESULT_COMPLETED`（工具失败时 status 为 `failed`）。
- Team 领域状态不属于 `RUNTIME_EVENT` 家族，只允许使用独立顶层 `TEAM_EVENT`。
- `SKILL_*`：Skill 开始、完成、失败。
- `HITL_*` / `CLARIFICATION_*`：人工确认与反问采集。
- `MEMORY_*`：历史兼容 / 后续记忆重设计预留。旧记忆模块已删除，当前运行时不得新增依赖旧记忆语义的事件。
- `ARTIFACT_*`：文件、下载产物、生成内容等可交付产物生命周期。
- `SANDBOX_*`：沙箱运行生命周期。
- `PLAN_*`：Plan Mode 进入、更新、最终计划输出和工具拦截状态。
- `SUBAGENT_*`：ReAct 内部 Subagent 委托生命周期。

## Plan Mode Runtime Events

Plan Mode 基于 AgentScope Middleware hooks 实现，不恢复旧 Plan Runtime。所有事件都通过 `RUNTIME_EVENT` 发送，`scope` 使用 `plan`。当前 Plan Mode 只支持 `executor.kind=single_agent`；当 `executor.kind=team` 时，后端不得向 TeamRun 注入 `plan_enter`、`plan_write`、`plan_exit`、`todo_write`、`task_output` 等 Plan 控制工具。

Plan Mode 内部只产生 `PlanDomainEvent`；跨出 Plan middleware / Plan tools 边界前，必须统一转换为 `RUNTIME_EVENT`。前端不得依赖 middleware 内部事件结构，也不得直接解析 Plan 工具结果作为主状态来源。

| type | status | payload |
| --- | --- | --- |
| `PLAN_MODE_ENTERED` | `running` | `{ planId, planFile, mode }` |
| `PLAN_UPDATED` | `running` | `{ planId, planFile, markdown, source }` |
| `PLAN_FINALIZED` | `completed` | `{ planId, planFile, markdown }` |
| `PLAN_TOOL_DENIED` | `completed` | `{ planId, toolCallId, toolName, message }` |
| `PLAN_TASKS_UPDATED` | `running` | `{ planId, planFile, steps }` |
| `PLAN_TASK_OUTPUT` | `running|completed|failed` | `{ planId, planFile, step, steps }` |

`steps` 使用现有 `ChatPlanStep` camelCase 字段：`id`、`planId`、`stepIndex`、`title`、`description`、`status`、`assignedAgentId`、`evidence`、`result`、`startedAt`、`completedAt`、`updatedAt`。

## Subagent Runtime Events

Subagent 委托能力基于 ReAct 内部 `DelegateTask` 工具实现，不新增 `runtime.kind=subagent`。V1 只支持前台 blocking 调用：主 Agent 调用 `DelegateTask` 后，必须等待子 Agent 完成并拿到 observation，才能继续 ReAct 或输出最终答案。

Subagent 定义来自 Markdown + YAML frontmatter 文件，不来自历史数据库 Subagent 配置。后端若发现历史 `callableAgents`、`subagentCalls`、旧 `runtime.kind=subagent`、旧 Subagent Runtime 事件兼容分支或对应前端入口，必须删除，不能改造成新版能力事实源。

| type | status | payload |
| --- | --- | --- |
| `SUBAGENT_STARTED` | `running` | `{ invocationId, toolCallId, subagentName, definitionScope, definitionPath, taskPreview, expectedOutput }` |
| `SUBAGENT_CONTEXT` | `running` | `{ invocationId, toolCallId, subagentName, skillRefs, loadedSkills, missingSkillRefs, memoryAgentId, memoryInjected, errors, permission }` |
| `SUBAGENT_WAITING_HUMAN` | `waiting` | `{ invocationId, toolCallId, subagentName, interactionType, replyId, toolCalls, partialResultPreview, durationMs }` |
| `SUBAGENT_COMPLETED` | `completed` | `{ invocationId, toolCallId, subagentName, resultPreview, durationMs }` |
| `SUBAGENT_FAILED` | `failed` | `{ invocationId, toolCallId, subagentName, error, partialResultPreview }` |

V1 的 Chat 主界面不展示子 Agent 完整 transcript，也不铺开完整轨迹列表；`DelegateTask` 工具项只展示当前 Subagent 正在做什么、委托任务和最终返回主 Agent 的结果。后端将 `SUBAGENT_*` 与子 Agent 关键模型/工具 Runtime Event 归并到同一个工具 display item，附加 `subagentName`、`subagentInvocationId`、`subagentStatus`、`subagentTimeline`、`subagentSummary`、`subagentError` 字段；前端只取 `subagentTimeline` 最新有效项作为“当前状态”。普通工具 display item 不包含这些字段，仍按通用工具输入/输出渲染。前台 blocking 模式下，主 Agent 仍等待 `DelegateTask` observation，但 `leader_stream` 必须在等待期间持续 drain Subagent runtime event sources，让当前状态实时刷新；子 Agent 调用 `FinalAnswerStart` 后的文本 delta 可以实时追加到 `subagentSummary`，仅用于前端显示，父 Agent 仍在完整 observation 到达后才继续。完整子 Agent 模型调用、工具调用、权限决策和错误进入 Trace / `subagent_invocations` 审计。`SUBAGENT_WAITING_HUMAN` 表示子 Agent 已暂停在人工确认点；后端 `/resume` 已能恢复 child agent、更新 invocation，并把恢复后的摘要作为 `DelegateTask` external result 交还父 Agent，继续父 Agent ReAct。

### Plan Mode 收口与后续执行

- `plan_exit` 不进入人工审批状态，也不触发 approve/deny HTTP 端点。
- Team Runtime 不支持 Plan Mode。用户如需“先规划再由 Team 执行”，应先用单 Agent 生成计划，再在下一轮选择 Team 普通执行。
- Agent 调用 `plan_exit` 后，后端发送 `RUNTIME_EVENT:PLAN_FINALIZED`，随后通过分片 `FINAL_ANSWER_DELTA` / `AGENT_FINAL_ANSWER_DELTA` 将当前计划 markdown 作为本轮 assistant 最终答案，禁止把完整计划作为单个超大 delta 一次性发给前端。
- 本轮 Run 继续走正常成功收尾并发送 `RUN_COMPLETED`。前端不得展示手动批准/拒绝按钮。
- 用户通过下一条自然语言消息决定后续动作，例如“修改这个计划”或“按这个计划执行”。该下一轮仍由普通 Chat 路由进入 ReAct Runtime，不携带旧 Plan 审批字段。

### Plan Mode 执行链路详解

1. **调查与计划阶段（Plan Mode）**：Agent 调用 `plan_enter` 进入 Plan Mode。该模式下，`PlanModeMiddleware.on_acting` 拦截变更型工具（`Write`、`Edit`、`Bash` 变更命令等），阻止其真实执行，并发送 `PLAN_TOOL_DENIED` Runtime Event 告知前端。Agent 只能执行只读工具（`Read`、`Grep`、`Glob`、只读 `Bash`/`HttpRequest`）和计划控制工具（`plan_enter`、`plan_write`、`plan_exit`、`FinalAnswerStart`）。

2. **计划收口**：Agent 调用 `plan_exit` 后，后端将计划置为 `finalized` 状态，发送 `PLAN_FINALIZED` Runtime Event，把计划 markdown 输出为最终答案，并停止当前 Agent 流。

3. **下一轮决策**：前端只展示计划和最终答案，不显示审批入口。用户下一条消息决定修改计划或执行计划；后续执行能力需独立设计，不复用旧 approve/deny 或 Build mode 字段。

## Run 生命周期

- `RUN`：后端已创建后台 Run。必须包含 `run_id` 或 `runId`，建议包含 `created_at`。
- `RUN_CANCELLED`：用户取消本轮执行。必须包含 `completed_at` 或 `completedAt`。
- `ERROR`：本轮执行失败。必须包含 `message`。

## RunState 中间层

`GET /api/chat/runs/{run_id}/state` 返回某个 Run 的最新前端状态快照。该接口用于刷新、断线恢复和后续替代 Chat 主界面对原始 SSE 的直接依赖。

`GET /api/chat/runs/{run_id}/state/stream` 返回 RunState 中间层 SSE。首个事件为 `RUN_STATE` 完整快照，后续事件优先使用 `RUN_STATE_PATCH` 轻量增量。该流是 Chat 主界面恢复运行中任务的推荐入口，前端不需要直接处理工具结果、模型调用等原始事件。

当 RunState 首包快照的 `status` 为 `completed|failed|cancelled` 时，`state/stream` 必须在发送 `RUN_STATE` 后结束连接。`waiting` 表示等待用户操作，不是仍在持续输出的流，但只有快照或 patch 已经包含前端可操作内容时才结束连接，例如 `approvals[]` / `approval` 或 `plans[]` / `plans`。`AGENT_WAITING_HUMAN`、`CLARIFICATION_WAITING` 这类预告等待的 Runtime Event 不得单独关闭流，必须等待随后的 `HITL_REQUIRED` / `CLARIFICATION_REQUIRED` 把可操作数据投影进 RunState。

`RUN_STATE_PATCH` 用于连接中的实时更新，允许丢失；网络断开、页面刷新或前端怀疑状态不一致时，应重新订阅并以新的 `RUN_STATE` 快照为准。每个 patch 必须包含 `kind`，前端必须优先按 `kind` 分支处理，不得再靠猜测可选字段判断 patch 语义。稳定字段包括：

- `kind`：稳定 patch 类型，当前值为 `snapshot_meta|display_event|model_usage|approval_resolved|error`。普通 Runtime Event 不得作为 `RUN_STATE_PATCH` 发送到 Chat 主界面。
- `runId`、`sessionId`、`status`、`currentStage`、`updatedAt`、`completedAt`、`lastEventType`、`eventCount`。
- `displayEvent`：一个单 Agent 展示增量事件。patch 中只允许携带一个 `displayEvent`，前端用 reducer 聚合到 `displaySnapshot`。
- `approvalResolvedId` / `resolvedApprovalId`：用户介入完成后的等待项 ID。
- `modelName`、`tokenUsage`：模型调用与用量摘要。
- `error`：错误摘要。

为避免同一段文本被 `RUNTIME_EVENT:AGENT_*_DELTA` 与 `FINAL_ANSWER_DELTA` / `REASONING_DELTA` 重复累计，RunState 正文投影只以稳定文本事件作为正文增量来源；Runtime Event 只保留在后端内部状态、Trace 或原始兼容流中，不进入 Chat 主界面的 RunState 契约。

后端必须用 `backend/src/server/routes/chat/run_state_schema.py` 校验 `RUN_STATE` 和 `RUN_STATE_PATCH` 输出；前端必须用 `frontend/src/types/runState.ts` 作为主链路类型来源。新增字段时先更新本文档、后端 Pydantic schema、前端 TypeScript 类型，再更新 projector 和测试矩阵。

`GET /api/chat/runs/{run_id}/stream?replay=false` 用于 RunState 恢复后的兼容订阅。`replay=false` 表示只接收订阅之后的新事件，避免前端先恢复快照后又重复消费历史 SSE。默认 `replay=true`，保持旧行为。

RunState 稳定字段：

- `version`：状态版本，当前为 `2`。
- `runId`、`sessionId`、`agentId`、`agentName`、`teamId`、`teamName`、`mode`、`task`。
- `status`：`running|waiting|completed|failed|cancelled|cancelling`。
- `currentStage`：面向用户的当前阶段。
- `reasoning`、`reasoningTruncated`：ReAct 过程文本。可截断。
- `finalAnswer`、`finalAnswerTruncated`：最终答案文本。可截断，完整最终结果以 `chat_messages` 为准。
- `thinking`：深度思考摘要。
- `tools`：轻量工具摘要数组，不包含完整 Observation。
- `skillCalls`：本轮注入/使用的 Skill 摘要。
- `modelName`、`tokenUsage`：模型调用与 token 用量摘要。
- `plans`：Plan Mode 当前计划摘要。
- `displaySnapshot`：单 Agent Chat 主展示完整快照，结构为 `{ version: 3, mode, actors, blocks }`，用于刷新、断线恢复和历史消息回放。Team 严禁进入该字段，使用并列的 `teamSnapshot`。
- `approvals`：等待用户处理的 HITL / clarification 摘要；每项必须包含 `interactionId`、`kind`、`type`、`status`、`createdAt`。`kind` 表示 UI 交互大类，稳定值为 `hitl|clarification`；`type` 表示该大类内部的交互子类型。clarification 的 `kind` 与 `type` 都必须为 `clarification`，并保留 `title`、`description`、`questions`，不得被问题字段或原始 payload 中的 `type` 覆盖。
- `error`、`lastEventType`、`eventCount`、`createdAt`、`updatedAt`、`completedAt`。

`tools[]` 稳定字段：

- `id`、`name`、`status`：工具调用身份和状态。
- `inputPreview`：参数预览，禁止存完整大参数。
- `outputPreview`：结果预览，默认最多保留少量文本。
- `outputSize`、`outputTruncated`：完整结果大小与是否截断。
- `error`、`startedAt`、`completedAt`。
- `subagentName`、`subagentInvocationId`、`subagentStatus`、`subagentTimeline`、`subagentSummary`、`subagentError`：仅 `DelegateTask` Subagent 工具项可携带的展示字段；`subagentTimeline[]` 只放压缩后的关键步骤，不存完整 transcript。

Chat 主界面不得依赖完整工具结果。大体积 Read、Glob、Bash、HTTP、MCP 输出必须通过 Trace 或 Artifact 按需查看，不进入 RunState 常驻状态。

### Chat DisplaySnapshot v3

`displaySnapshot` / `displayEvent` 是 Chat 主界面展示层契约，只表达“前端现在应该显示什么”。它不替代 `RUNTIME_EVENT` 事实日志；事实事件仍用于 Trace 和审计。

DisplaySnapshot v3 的核心原则：

- 外层 `mode` 固定为 `single_agent`。
- 内层 `actors[]` 只描述一个 `role=single` 的单 Agent 执行主体。
- `blocks[]` 只放整轮 Run 级展示块，例如 Plan、人工反馈、最终答案、生成文件和错误。
- actor 内部 `items[]` 承载 reasoning、thinking、tool、skill 的有序 ReAct 过程。Team Console 不读取这些 actor 或 item。

`RUN_STATE` 必须返回完整快照：

```ts
displaySnapshot: {
  version: 3;
  mode: "single_agent";
  actors: DisplayActor[];
  blocks: DisplayBlock[];
}
```

`RUN_STATE_PATCH` 必须返回单个展示事件：

```ts
displayEvent: {
  id: string;
  runId: string;
  sessionId: string;
  seq: number;
  createdAt: string;
  scope: "single_agent";
  block: "process" | "plan" | "feedback" | "answer" | "files" | "error";
  event: string;
  op: "append" | "upsert" | "replace" | "remove" | "clear";
  targetId?: string;
  actorId?: string;
  status?: "pending" | "running" | "waiting" | "completed" | "failed" | "cancelled";
  data?: Record<string, unknown>;
}
```

`DisplayActor` / `DisplayBlock` / `DisplayItem` 使用通用结构，避免后端输出组件形状：

```ts
type DisplayActor = {
  id: string;
  role: "single";
  agentId: string;
  agentName: string;
  status: "pending" | "running" | "waiting" | "completed" | "failed" | "skipped" | "cancelled";
  task: string;
  detail: string;
  output: string;
  createdAt: string;
  updatedAt: string;
  completedAt: string;
  items: DisplayItem[];
  data: Record<string, unknown>;
};

type DisplayBlock = {
  id: string;
  block: "process" | "plan" | "feedback" | "answer" | "files" | "error";
  status: string;
  updatedAt: string;
  items: DisplayItem[];
  data: Record<string, unknown>;
};

type DisplayItem = {
  id: string;
  event: string;
  targetId: string;
  status?: string;
  createdAt?: string;
  updatedAt: string;
  data: Record<string, unknown>;
};
```

当前稳定展示事件：

- `process`：ReAct 过程事件，投影到对应 actor 的 `items[]`。连续 reasoning / thinking delta 使用同一个 `targetId` 追加；如果中间出现 tool / skill，再出现 reasoning / thinking 时必须使用新的 `targetId`，避免前端把非连续过程合并成一个 item。
- `plan`：Plan Mode 展示块，使用 `data.planId`、`data.planFile`、`data.markdown`、`data.steps`。`PLAN_FINALIZED` 使用 `status=completed`。
- `feedback`：当前等待用户处理的人机交互，使用 `data.approval`。只放当前等待项，不保留历史已完成项。
- `answer`：最终答案展示，使用 `data.content`。没有 `FinalAnswerStart` 时，后端不得把 reasoning 兜底复制成最终答案；如果 Agent 流自然结束但没有产生最终答案，后端可以先执行一次受限 Final Answer Recovery，恢复轮必须重新生成 `FINAL_ANSWER_DELTA`。恢复失败后必须发送 `RUN_FAILED` 和 `ERROR`，并投影为 `error` 展示块，常见原因包括达到最大 ReAct 迭代次数。
  如果模型误把阻塞性的补充信息请求当作普通最终答案输出，后端必须把该自然语言反问转换为 `CLARIFICATION_REQUIRED`，发送 `displayEvent(block=answer, op=remove)` 清理误发答案，再发送 AskUser `process` 项和 `feedback` 块进入等待态。
- `files`：生成文件下载，使用 `data.files`，每项至少包含 `path`、`name`、`extension`。
- `error`：错误展示块，使用 `data.message` 或 `data.error`。

当前前端登记的事件名集中维护在 `frontend/src/types/displayEvents.ts`。新增事件时先扩展该文件，再更新本文档、后端 Pydantic schema、前端 projector 和测试矩阵。

Team 严禁进入 `displaySnapshot.actors[]` 或 `displaySnapshot.blocks[]`。

### 历史消息回放

`GET /api/chat/sessions/{session_id}/messages` 返回的 assistant 消息携带 `displaySnapshot`；Team 轮次另带独立 `teamSnapshot`。两者不得互相推导。

后端在 RunState 到达可回放节点后，把当前 `displaySnapshot` 写回本轮最新 assistant 消息：

- `RUN_COMPLETED` / `RUN_FAILED` / `RUN_CANCELLED` 后保存终态快照。
- `HITL_REQUIRED` 后保存等待用户审批或 AskUser 补充信息的快照。`CLARIFICATION_REQUIRED` 仅用于旧兼容回放。
- 没有 `displaySnapshot` 的旧历史消息不做回填，前端可仅按 `content` 显示最终答案。

### Raw Event 到 RunState 到前端状态映射

| 后端原始事件 / Runtime Event | RunState 字段 | `RUN_STATE_PATCH` 字段 | 前端 live 状态 | 流语义 |
| --- | --- | --- | --- | --- |
| `CLARIFICATION_REQUIRED` | `displaySnapshot.actors[].items[]`、`displaySnapshot.blocks[]`、`approvals[]` | AskUser 先发 `displayEvent(block=process,event=tool.started)`，随后发 `displayEvent(block=feedback,event=feedback.waiting)` | `displaySnapshot: process item + feedback block` | 旧兼容事件；新可恢复反问不得依赖它 |
| `HITL_REQUIRED` | `displaySnapshot.actors[].items[]`、`displaySnapshot.blocks[]`、`approvals[]` | AskUser 先发 `displayEvent(block=process,event=tool.started)`，随后发 `displayEvent(block=feedback,event=feedback.waiting)` | `displaySnapshot: process item + feedback block` | `HITL_REQUIRED` 带 `feedback` 后结束，等待用户审批或 AskUser 补充信息；`AGENT_WAITING_HUMAN` 只作内部预告，不单独发 RunState patch |
| `PLAN_FINALIZED` | `displaySnapshot.blocks[]`、`finalAnswer` | `displayEvent(block=plan,event=plan.completed)`，随后 `displayEvent(block=answer,event=answer.delta)` | `displaySnapshot: plan block + answer block` | 本轮正常完成，用户下一条普通消息决定修改或执行 |
| `TOOL_CALL_*` / `TOOL_RESULT_*` | `displaySnapshot.blocks[]`、`tools[]` 摘要 | `displayEvent(block=process,event=tool.*)` | `displaySnapshot: process item` | running 继续，终态工具不可保留 running |
| `REASONING_DELTA` | `displaySnapshot.blocks[]`、`reasoning` | `displayEvent(block=process,event=reasoning.delta)` | `displaySnapshot: process item` | running 继续 |
| `FINAL_ANSWER_DELTA` | `displaySnapshot.blocks[]`、`finalAnswer` | `displayEvent(block=answer,event=answer.delta)` | `displaySnapshot: answer block` | running 继续 |
| Agent 流结束但没有最终答案 | 恢复成功时写入 `finalAnswer`；恢复失败时 `status=failed`、`error` | 恢复成功时 `displayEvent(block=answer,event=answer.delta)`；恢复失败时 `displayEvent(block=error,event=error.raised)` | `displaySnapshot: answer block` 或 `error block` | 后端最多执行一次受限 Final Answer Recovery；恢复失败后结束 |
| `TEAM_EVENT` | `teamSnapshot` | `kind=team_event`、`teamEvent` | `TeamConsoleState` | running 继续，完全绕过 DisplaySnapshot |
| `SKILL_CONTEXT` / `SKILL_END` | `displaySnapshot.blocks[]`、`skillCalls[]` | `displayEvent(block=process,event=skill.*)` | `displaySnapshot: process item` | running 继续 |
| `MODEL_CALL_*` | `modelName`、`tokenUsage` | `modelName`、`tokenUsage` | 模型与用量状态 | running 继续 |
| `RUN_COMPLETED` / `RUN_FAILED` / `RUN_CANCELLED` | `status`、`completedAt`、`error` | `status`、`completedAt`、`error` | 停止 streaming | 发送后结束 |

## 模型与文本输出

- `MODEL_CALL_START`：模型调用开始。建议包含 `model_name`。
- `MODEL_CALL_END`：模型调用结束。可包含 `input_tokens`、`output_tokens`、`cache_creation_input_tokens`、`cache_input_tokens`。
- `TEXT_BLOCK_DELTA`：最终回复文本增量。必须包含 `delta`。
- `REASONING_DELTA`：ReAct 过程文本增量。必须包含 `delta`。主 Leader 新链路会优先发送该事件，避免过程文本混入最终答案。
- `FINAL_ANSWER_DELTA`：最终答案文本增量。必须包含 `delta`。由 `FinalAnswerStart` 阶段工具触发后发送。
- `FINAL_ANSWER_REPLACE`：替换当前最终答案。必须包含 `content`。仅用于 TeamRun 等后处理完成后需要用最终汇总覆盖早先协调输出的场景。
- `THINKING_BLOCK_DELTA`：深度思考文本增量。必须包含 `delta`。
- `REPLY_END`：AgentScope 回复结束。注意 SSE 结束才代表本轮后处理全部完成，`REPLY_END` 后仍可能出现 Team / Skill / Artifact 等收尾事件。

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
- 如果 AgentScope 自然结束但没有调用 `FinalAnswerStart`，后端可执行一次受限 Final Answer Recovery：内部强制调用 `FinalAnswerStart`，随后禁止工具调用并重新生成最终答案正文。Recovery 不得把既有 reasoning 原样提升为 final answer；既有 reasoning 仍保留为过程文本。
- Recovery 仍未产生最终答案时，本轮按失败收口。

## Skills

- `SKILL_CONTEXT`：本轮注入的 Skill 上下文。必须包含 `skills` 数组。
- `SKILL_END`：本轮 Skill 使用结束。必须包含 `skills` 数组，可包含 `status=completed|failed`。

## Team 协作

- Team 使用顶层 SSE `TEAM_EVENT`，不经过 `RUNTIME_EVENT`，也不进入单 Agent `DisplaySnapshot`。
- payload 必须包含 `id`、`seq`、`teamRunId`、`chatRunId`、`type`、`timestamp`、`payload` 和最新 `snapshot`；成员或任务事件还应包含 `actorId` / `taskId`。
- 稳定事件名为：`run.created`、`run.phase.changed`、`run.completed`、`run.failed`、`run.cancelled`、`member.spawned`、`member.status.changed`、`member.output.delta`、`member.thinking.delta`、`member.tool.started`、`member.tool.completed`、`member.tool.failed`、`member.tool.denied`、`member.iteration.recovery`、`member.iteration.exhausted`、`task.created`、`task.claimed`、`task.completed`、`task.failed`、`task.cancelled`、`task.unlocked`、`mail.sent`、`mail.read`、`mail.acted`。
- RunState 首包通过独立 `teamSnapshot` 字段恢复 Team Console；`teamSnapshot.recentEvents` 保存最多 300 条已压缩领域事件，用于刷新和历史回放恢复“运行”页。后续 `RUN_STATE_PATCH(kind=team_event)` 必须携带原始 `teamEvent`。`teamSnapshot` / `teamEvent` 与 `displaySnapshot` / `displayEvent` 互不派生。
- Team 最终用户答案仍通过 `FINAL_ANSWER_REPLACE` 进入 Chat 主回答区；协调文本和 Member 输出不得发送 `FINAL_ANSWER_DELTA`。
- TeamRun 执行期间，Chat 主回答区只显示 Team 阶段和任务完成数等运行占位，不显示 Member 过程；最终综合完成后用正式答案替换该占位。
- `mail.sent` 必须在消息持久化后立即发布；Member 未读入站消息会阻止 `team_submit_result`，`mail.read` 表示该消息已进入 Member session 上下文。
- Team Mailbox 支持 `recipient=broadcast`，广播在 Store 边界展开为每个目标成员一条独立 `mail.sent`，不引入特殊广播消息结构。
- Member 不进入单 Agent HITL。工具请求需要人工确认或外部执行时，Runtime 必须拒绝该工具、发出 `member.tool.denied`，并用拒绝结果恢复原 Member session；不得因此直接终止 TeamRun，也不得绕过权限执行工具。
- Team session 使用独立 ReAct 预算：Leader 协调 16 轮、Member 执行 32 轮、综合 8 轮。Member 首次达到上限时发出 `member.iteration.recovery`，把工具收缩为 Team 协作工具并追加 8 轮收敛恢复；再次耗尽时发出 `member.iteration.exhausted` 后失败。
- 旧 Team 事件和旧 Team 历史快照不兼容、不回放、不投影。

## 用户介入

- `HITL_REQUIRED`：需要用户审批或补充外部执行结果。`AskUser` 反问也走该事件，前端按工具名渲染为补充信息表单。必须包含 `interactionId`、`type`、`sessionId`、`agentId`、`agentName`、`replyId`、`toolCalls`。
  `toolCalls` 是前端展示用列表，可能合并同一回复里仍处于 UI running/calling 的工具；后端恢复 AgentScope 时必须使用内部保存的 AgentScope 原始等待工具集合，不能把展示合并列表整体回传给 `UserConfirmResultEvent` / `ExternalExecutionResultEvent`。
- `HITL_RESOLVED`：用户处理完成。AskUser 场景必须在 Runtime Event payload 中携带 `externalResultsPreview`，用于把“用户补充信息”写回同一个 AskUser process item；该字段只保存短预览，完整恢复数据仍以 `/resume/start` 请求和 AgentScope `ExternalExecutionResultEvent` 为准。
- `CLARIFICATION_REQUIRED`：旧兼容事件。新链路不得用它发起用户可恢复反问；用户可恢复反问必须通过 `AskUser` -> `HITL_REQUIRED` -> `/resume/start` -> `ExternalExecutionResultEvent`。

## 后处理

- 产物、沙箱等后处理均通过 `RUNTIME_EVENT` 的独立家族表达：`ARTIFACT_*`、`SANDBOX_*`。记忆能力重设计后再定义新的 `MEMORY_*` 事件语义。

## 兼容字段

- 历史消息中仍可能存在 `planRuns` 字段。当前前端不执行历史 Plan 字段；后端仅保留读写兼容，避免旧 SQLite 数据导致接口失败。
