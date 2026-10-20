# 定时任务能力合同

> 状态：2026-10-09。到期执行、通知和 Job Agent 的在线事实见 `docs/architecture-overview.md`。本文只保留能力合同的决定和禁止项。实施日记和 PR 计划已从本文删除。

定时任务是到点激活一个已有 Job Agent，不是让模型记住闹钟。Job 定义和 firing 文件在 owner Session 的 session root；Run workspace 是该次 firing 目录。`runtime.kind` 仍是 `react`，`executor.kind` 仍是 `single_agent`。禁止把 `ChatSession.kind=scheduled_job` 写进 IntentRoute，禁止顶层 `JOB_EVENT`。

## Required Skill Context

Runtime 明确区分 `interactive_on_demand` 与 `scheduled_preloaded`，模式进入 Agent 缓存键。普通 Chat 的 catalog 给出稳定 `<id>`、精确加载名称、显示名称、描述与目录，继续按需使用 `Skill` viewer；scheduled firing 使用独立 Toolkit 和说明模板，不注册内置 viewer，模型可见 schema 中不得出现 `Skill` 或 `SkillViewer`。Skill 包仍挂载，但目录和引用文件只通过本 Run 已有的受限工具访问。

`ScheduleJob.skill_ids` 从 catalog 的 `<id>` 选择稳定 ID；兼容期唯一名称可由服务端解析，歧义、未知引用和文件路径均拒绝。模型工具 schema 不再提供 `required_skills`。旧 API/文件仍可读取冻结 manifest，但创建或更新时提交的 manifest 不具权威性，不能覆盖服务端快照，旧客户端原样回传也不触发重新确认。`ConfirmJob` 回显实际冻结的 `skillId`、`contentSha256`、`frozenAt`、`ownerSessionId` 与 `ownerGrantVersion`，并明确 `skillPreload=first_model_call`，即每次 firing 首次模型调用前自动预加载。

首轮顺序固定为 preflight/hash 校验 → scheduled Toolkit 断言 → fresh Run snapshot/Context 检查 → `activate_forced_skills` → required `<activated-skill>` 的 ID/hash/正文完整性断言 → Job prompt → 首次模型调用。任一步失败均不得调用模型；预加载后到模型入口前文件再次变化也必须失败。旧缓存或模型异常发出的 viewer 调用在原生工具可用性检查处拒绝，直接绕过检查执行工具同样拒绝并审计，不允许第二次正文加载。

运行时 Skill Context 审计同时进入 trace 和 firing 的 `output_manifest.skillContext.runtime`，记录 required ID/hash、注入顺序、正文 token 估算、预算、Toolkit schema 摘要、Run snapshot 恢复检查、压缩后的补回记录与被拒 viewer 调用，不重复保存正文。首轮审计应与实际发给模型的 Context 一致。

Job 的 `skill_ids` 只表示用户明确选择的能力；确认时系统会把它与能力合同中的 `skill_ids` 按 Job 顺序合并，并冻结包内 `SKILL.md` 的来源、完整 SHA-256 和正文大小。`availableToAllAgents` 不会给 Job 自动追加 Skill。

显式选择 `<USER_DATA_ROOT>/skills` 中已启用且通过当前 Project binding 的 Skill 时，确认与 firing 都从该统一目录解析。包的稳定 ID 由 owner、逻辑来源根和相对包路径生成，编辑正文仅改变原始字节 SHA-256。确认保存稳定 ID、精确 loadName、rootKey、ownerKey 和来源路径；版本变化报告为 `skill_version_changed`。项目 `.agents/skills` 不参与 Job Skill 解析。

每次 firing 在 Agent 创建和模型调用前重新检查 Skill 是否仍安装、启用、可读、属于当前 Project，并比较 `SKILL.md` hash。版本变化、删除、停用、解绑或 Context 预算超限都会 fail-closed，写入 firing error/notification，要求编辑并重新确认；普通恢复只校验已有冻结 hash，不能顺手更新版本。Skill 的删除、卸载和停用不反向扫描 Job catalog，也不在资源操作中同步暂停 Job。显式 Skill 无法读取/有歧义时确认失败，禁止 active 且 required manifest 为空。旧 Job 只有唯一来源证明时才映射 ID，历史 hash 不变，缺少 manifest 时要求重新确认；禁止静默跳过 Skill。

通过校验的 required Skill 正文以 `<activated-skill>` HintBlock 进入本次 AgentScope 会话，稳定规则可以出现在 system prompt，但正文不能拼入 system prompt。`references/` 和 `scripts/` 继续按需 Read/Bash，不能因为正文预加载扩大 Job 的 Bash、HTTP、MCP、知识库、filesystem、审批或 unattended denylist 权限。

Job `context_policy` 当前只有 `fresh`：每次 firing 使用独立 session，不继承上一 firing 的完整消息和工具历史。firing 的 `output_manifest.skillContext` 记录 Skill ID、hash、policy、预算、校验阶段和错误码，不记录 Skill 正文。

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
