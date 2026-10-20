# 定时任务能力合同

> 状态：2026-10-09。到期执行、通知和 Job Agent 的在线事实见 `docs/architecture-overview.md`。本文只保留能力合同的决定和禁止项。实施日记和 PR 计划已从本文删除。

定时任务是到点激活一个已有 Job Agent，不是让模型记住闹钟。`runtime.kind` 仍是 `react`，`executor.kind` 仍是 `single_agent`。禁止把 `ChatSession.kind=scheduled_job` 写进 IntentRoute，禁止顶层 `JOB_EVENT`。

## 决定

1. Job 只冻结本次认领的合同。能力长在 Agent 上。
2. 合同记录具体的 MCP / HTTP / knowledge / Skill id，以及是否允许通用 HttpRequest。不要在到期现场继承 Agent 当时的全部工具，也不要在 confirm 时整表拷贝 `tool_scope`。
3. `CheckJobFeasibility` 与 `ScheduleJob` 调用同一个 `evaluate_job_feasibility()`。判断只读真实 catalog、绑定和审批策略，不让 ChatModel 参与。门禁用 `task_intent`（用户原话），不用 `frozen_prompt`。
4. 结果只有 `ok` / `partial` / `impossible`。`impossible` 不建行。`partial` 必须问清缺口，用户同意（`accept_partial=true`）才建。
5. 对话里会弹出人工确认的能力，不算无人值守能力。`unattended_ok` 仅当审批策略为 `TRUSTED_TOOLS`。stdio MCP 一律不可无人值守。
6. `read_only` Agent 不能接写文件或外呼的 Job。
7. `builtin:HttpRequest` 不能满足 `email.send`。发信意图不得被调用方传入的 `http.call` 打开。
8. 空合同的存量 Job 按只生成处理，保留 `allow_bash`，忽略脏的外呼旗。改任务或重新确认后才扩合同。
9. 合同里的资源后来没了，或不再 `unattended_ok`：失败 `capability_unbound`，或在 confirm / resume 返回 422。禁止静默丢掉。后来新接、但不在合同里的工具不得注入。
10. `capability_gate` 缺省关闭。打开后才拒绝空头任务。`set_scheduled_jobs_enabled()` 必须合并写入，不得抹掉 `capability_gate`。门禁关闭时仍要确认执行者存在，不存在则 422，但不因此要求必须是 Job Agent，也不跑完整可行性。
11. `RecheckJobCapabilities` 省略 `task_intent` 时只用合同里的 `task_intent`。仍空则 422，禁止回退 `frozen_prompt` 或空串。
12. firing 继续用 denylist，不用窄 allowlist。最终回答走既有正文投影。Chat 里可行性结果是普通 `tool` Block。

登记顺序：先 `CheckJobFeasibility`，再补调度字段，再 `ScheduleJob`。`ConfirmJob` 时合同仍须可满足。到期装配 = 默文本地工具减去 denylist，再加上合同认领且当前仍启用、仍在 Project 白名单内、仍可无人值守的资源。

## 禁止

| 禁止 | 原因 |
| --- | --- |
| 顶层 `JOB_EVENT` 或把通知写入 Chat SSE | 通知走独立通道 |
| 到期继承 Agent 全量 MCP，或用窄 allowlist 换掉 denylist | 合同被绕过，或裁掉默认工具 |
| 把 `HttpRequest` 当成发信 | 没有 SMTP 通道 |
| 用模型感觉写「能发邮件」 | 必须落在工具表 |
| 把 HITL 成功当成无人值守 | 到期会被拒绝 |
| `scheduler` 可行性模块 import `src.server.routes.*` | 循环依赖 |
| `kind=knowledge` 去满足 `email.send` | 通道认错 |
| ReadinessAgent 选择 Job / MCP | 职责越界 |
| 恢复 `runtime.kind=scheduled` | architecture 只有 `react` |
