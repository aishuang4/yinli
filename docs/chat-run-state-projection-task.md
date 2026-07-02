# Chat Run State Projection 改造任务

## 目标

把 Chat 前端从“直接消费后端 Runtime/SSE 原始事件”逐步迁移为“消费 RunState 中间层状态”。后端继续保留原始事件用于审计和 Trace，但 Chat 主界面只依赖稳定的状态快照和轻量工具摘要，降低断线、刷新、长工具输出导致的闪烁、卡死和内存占用风险。

## 设计原则

- Runtime 仍然负责执行，Event Hub 负责接收原始事件，Projector 负责生成前端状态。
- RunState 只保存当前 UI 需要的状态，不保存完整工具 Observation。
- 工具结果默认只生成摘要：工具名、参数预览、状态、耗时、错误摘要、结果大小和截断标记。
- 完整工具结果后续进入 Trace 或 Artifact，Chat 主界面按需打开，不作为流式状态常驻。
- 前端断线不代表 Run 失败，刷新后先读取 `/api/chat/runs/{run_id}/state` 恢复状态，再决定是否订阅运行中 Run。

## 分阶段任务

### Task 1：后端 RunState 持久化与投影

- 新增 `chat_run_states` 表，按 `run_id` 保存最新状态快照。
- 新增 `event_hub` 模块，提供 `initial_run_state()`、`project_event()`。
- 在 `background_runs._broadcast()` 中，每次事件入库时同步更新 RunState。
- 新增 `GET /api/chat/runs/{run_id}/state`，供前端刷新和断线恢复使用。

### Task 2：工具结果瘦身

- RunState 的 `tools[]` 只存摘要，不存完整 result。
- `TOOL_RESULT_TEXT_DELTA` 只累计 `outputPreview`，超过阈值截断并记录 `outputSize`。
- `TOOL_RESULT_COMPLETED` 只更新状态、错误摘要和完成时间。

### Task 3：前端恢复入口

- 新消息发送使用 `/api/chat/send/start` 创建后台 Run，不再直接把 `/api/chat/send` 原始 SSE 作为 Chat 主状态来源。
- HITL 恢复使用 `/api/chat/resume/start` 后台恢复原 Run，不再直接把 `/api/chat/resume` 原始 SSE 作为 Chat 主状态来源。
- `ChatPage` 发现 active run 时，订阅 RunState。
- UI 从 RunState 恢复 `finalAnswerContent`、`reasoningContent`、`toolCalls`、`runtimeEvents`、`hitlRequests`。
- 然后订阅 `/api/chat/runs/{run_id}/state/stream`，只接收 RunState 中间层快照，不再直接用旧 `/stream` 恢复 Chat 主状态。

### Task 4：稳定增量协议

- `/api/chat/runs/{run_id}/state/stream` 首包发送 `RUN_STATE` 完整快照，后续发送 `RUN_STATE_PATCH` 轻量增量。
- `RUN_STATE_PATCH` 允许丢失，断线后前端重新订阅并以新的 `RUN_STATE` 快照恢复。
- 前端只订阅 RunState update，不再直接处理原始 AgentScope 事件作为 Chat 主状态。
- Plan Dock 从 RunState `plans` 读取 live 状态；历史消息继续兼容旧 `runtimeEvents`。
- Team activity 从 RunState `teamActivities` 读取 live 状态；旧 `TEAM_ACTIVITY` 原始事件只进入后端投影和 Trace。
- Skill、模型调用、clarification 也进入 RunState 字段：`skillCalls`、`modelName`、`tokenUsage`、`approvals`。
- 原始事件只进入 Trace 和调试页。

## 验收标准

- 刷新页面后，running/waiting Run 能从 RunState 恢复审批、工具状态和最终答案片段。
- 大型 Read/Glob/Bash 输出不会进入 Chat 主界面完整状态。
- 后端测试覆盖 RunState 投影：final answer、reasoning、tool summary、HITL、Plan approval。
- 旧 SSE 流仍可用，现有 Plan/HITL 功能不被打断。

## Legacy 清理边界

- Chat 主链路不得再调用 `/api/chat/send` 或 `/api/chat/resume` 解析原始 SSE；只能使用 `/api/chat/send/start`、`/api/chat/resume/start` 和 `/api/chat/runs/{run_id}/state/stream`。
- `useEventStream` 主 handler 只处理 `RUN_STATE`、`RUN_STATE_PATCH`、`ERROR`、`RUN_CANCELLED`；`TEXT_BLOCK_DELTA`、`TOOL_*`、`TEAM_ACTIVITY`、`TEAM_MEMBER_EVENT`、`HITL_REQUIRED`、`SKILL_*`、`MODEL_CALL_*` 等 raw event 不再直接驱动 Chat UI。
- 历史消息回显可继续读取已落库的 `runtimeEvents`、`toolCalls`、`teamActivities`，但 live 状态应优先来自 RunState 的 `tools`、`teamActivities`、`plans`、`approvals`。
- 可以删除的旧代码必须满足：没有 `/send/start`、`/resume/start`、`state/stream` 依赖；Trace/Run Center/历史消息不再引用；有对应 RunState 字段覆盖。
