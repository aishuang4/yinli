# Proposal

## Why

当前权限执行底座已经能够把人工批准、智能审核和完全开放统一到同一个动作快照与执行前复核链路，但智能审核计划仍混入了 200 条人工评测、holdout 和严格 JSON 质量门禁。这把离线模型质量验收与运行时安全决策混成了一个系统，也让实现者无法判断智能审核究竟应审核所有非只读动作，还是只审核危险和不确定动作。

本次修订采用 Hermes 式窄守卫：硬边界和确定性风险分类先处理，平台内部动作和登记的低风险动作由代码直通，只有危险或语义不确定的动作才调用独立审核模型；控制面修改等“永不自动批准”类别直接转人工。审核模型只返回三个固定结果，任何异常都转人工或结束无人值守执行。三种审批模式只改变授权决定的来源，最终工具参数和执行路径保持一致。

## What Changes

- **BREAKING**：删除 200 条人工评测数据、holdout、评测 CLI、评测报告落库和“评测通过后才能启用”的运行时门禁。审核模型配置可用并通过保存时探测即可启用；离线评测不再是本变更能力。
- 保留 `manual / model_review / full_access` 三种模式，并规定它们都必须经过同一条 `ActionSnapshot -> decision -> boundary/revalidate -> reserve -> start -> tool -> finish` 执行链。
- 建立确定性风险分类：`boundary / trusted_readonly / platform_internal / low_risk / human_only / dangerous / unknown / insufficient_evidence`，并在 design 中固定初始工具分类表；未登记行为默认 `unknown`。
- 将 `model_review` 改为 Hermes 式 SmartGuard，只处理 `dangerous` 和 `unknown`；`trusted_readonly`、`platform_internal`、`low_risk` 不调用审核模型，`human_only` 与 `insufficient_evidence` 直接转人工。
- SmartGuard 使用仓库内唯一一份“Hermes 派生 prompt”：以 NousResearch hermes-agent `tools/approval_smart.py`（commit `d97007c3`）的守卫提示词为基础，改写为覆盖非 Shell 动作、输出小写三 token、不依赖删除注释的版本，固定版本与摘要，经用户审定后才可发布。不得把完整历史、Skill、网页、文档或凭据送入模型。
- **BREAKING**：审核模型只允许返回 `allow`、`deny`、`needs_human` 三个固定 token。JSON、解释文字、布尔值、大小写变体、`APPROVE/DENY/ESCALATE`、空响应和其他内容均按 `needs_human` 处理。
- `allow` 只批准当前精确动作一次；交互式场景下模型 `deny` 不立即把拒绝结果交回 Agent，而是暂停当前调用并显示“维持拒绝 / 人工批准这一次”复核卡；`needs_human`、审核超时和审核异常进入人工审批；Job/无人值守场景遇人工需求结束本次 firing 并通知。
- 熔断改为 Run 内累计拒绝：按 `run + actor` 和整轮 `run` 双重计数，达到阈值后在调用模型之前直接拒绝；只读、平台内部、低风险和模型 allow 不清零，只有人工决定或新用户输入清零。
- 明确人工批量批准的事务语义、浏览器刷新与后端重启语义、Team/Subagent/External/Job 的统一授权边界和执行事实。
- 保留审核调用的模型身份、延迟、token、失败来源（含空响应/截断）和熔断观测，但这些观测不构成模型质量启用门槛。
- 增加升级迁移：旧审核配置去掉 JSON `schema_version` 依赖；保存的下一轮模式为 `model_review` 但 SmartGuard 不可用时明确提示，不静默切换。

## Capabilities

### New Capabilities

- `tool-authorization`：三种模式、统一动作执行链、风险分类、硬边界优先和多入口继承。
- `permission-review`：独立 SmartGuard 配置与探测、窄范围调用、Hermes 派生提示词、严格三 token 输出、累计拒绝熔断和异常收口。
- `permission-approval-lifecycle`：精确人工批准、模型拒绝暂停复核、批量事务、刷新/重启、取消和真实执行事实。

### Modified Capabilities

无。三个 capability 仍属于本变更创建的规划能力；本次更新重写其 delta 合同，不新增近似能力名称。

## Impact

- 后端授权核心：`security/authorization/effects.py`、`policy.py`、`review_contract.py`、`reviewer.py`、`review_gate.py`、`service.py`、`permission_records.py`、新增 `security/authorization/prompts/` 下的 SmartGuard prompt 资源，以及统一 AgentScope adapter。
- 后端路由与恢复：Chat 发送/恢复、模型拒绝复核卡（复用普通审批 answer API）、Team/Subagent、scheduled firing 和通知收口。
- 配置：`PermissionReviewConfig` 去掉旧 JSON schema 字段，增加保存时探测结果；prompt 版本来自 prompt provider。
- 前端：审核模式、审批卡（含模型拒绝复核变体）、执行状态、当前 Run/下一轮模式显示和 SmartGuard 不可用提示。
- 删除权限评测代码、合成数据、评测存储和相关文档（含 `docs/permission-review-cases-to-label.json`、`docs/permission-review-evaluation.md`）；历史 SQLite 评测记录不再被运行时读取，物理清理另行处理。
- 不改 AgentScope 版本，不复制 ReAct，不创建新的 runtime 分支，不改变群聊工作室产品边界。
- 实现完成后必须更新 Chat 稳定事件契约、架构文档、权限专题和项目 TODO，并执行后端、前端、导入边界和桌面浏览器验收。
