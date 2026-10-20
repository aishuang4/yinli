# Tasks

执行顺序（见 design Migration Plan）：先做不依赖 prompt 的第 7 组、2.1–2.5、4.6–4.7、5.2、6.2；prompt 已于 2026-10-19 审定，第 3 组与 4.1–4.2 可随后进行；然后 4.3–4.5；最后第 8 组。

## 1. 固定统一授权链和模式合同

- [x] 1.1 核对 SQLite、Chat session/Run、ModelCatalog、Job policy、ActionSnapshot、decision、approval、execution 和前端类型，确认三种模式字段归属；用字段映射表验证审批模式不写入 Agent profile。
- [x] 1.2 保留框架无关 authorization models、`src/runtime/authorization.py` 公开门面和集中 AgentScope adapter；用真实安装版本 fake tool/model 验证普通、并发、external、拒绝和恢复事件配对。
- [x] 1.3 按 design 决策 1 的矩阵建立参数化测试：八个 riskClass 分别验证 manual/model_review/full_access 的 decision 来源、是否调用 SmartGuard 和统一执行路径。
- [x] 1.4 增加执行链不变量回归：三种模式使用相同规范化输入、工具实例、ActionSnapshot fingerprint、reserve/start/finish 和终态记录；验证 AgentScope `ALLOWED/BYPASS`、remembered rule 和 suggested rule 不能绕过平台。

## 2. 风险分类与 SmartGuard 输入

- [x] 2.1 在授权域新增或扩展 `risk.py`（由 `policy.py` 调用），按 design 决策 2 输出 `boundary / trusted_readonly / platform_internal / low_risk / human_only / dangerous / unknown / insufficient_evidence` 及风险规则 key/description；分类异常和未登记行为归 `unknown`。
- [x] 2.2 按 design 初始工具分类表落地 `platform_internal` 与 `low_risk`：Plan/Team/Delegate 协调工具、Session 根内非控制面 Write/Edit、Memory 增改、Timeline、ScheduleJob 草稿、PauseJob、KnowledgeBaseSearch；用表驱动测试固定每个工具的分类，并验证 Session 根外写入、`MemoryDelete`、HTTP 外发为 `dangerous`。
- [x] 2.3 落地 `human_only`：控制面修改、ConfirmJob、修改审批/审核/凭据/`.agent-workstation` 配置、自终止 Shell、读凭据并外发；验证 model_review 下这些行动不调用 SmartGuard、直接创建人工审批，Job 中结束 firing。
- [x] 2.4 以 Hermes `tools/approval.py` 的 `DANGEROUS_PATTERNS` 为起点建立危险 Shell 与自终止模式清单并写入测试；命中者为 `dangerous`/`human_only` 并把规则描述作为 riskRule，其余未证明只读的 Shell 为 `unknown`；拆分 `insufficient_evidence`（目标/去向/版本无法解析）并验证其不调用模型。
- [x] 2.5 重新定义审核输入 DTO：可信区含 riskClass、riskRule、工具身份、effect、目标、执行环境、触发当前 Run 的用户原始消息（截断；Job 为冻结 Job prompt）和明确用户限制；不可信区只含精确动作；验证不携带完整历史、工具输出、Skill/网页全文、Agent 自述、思维链和凭据。
- [x] 2.6 验证审核输入与执行输入一致：禁止删除 Shell 注释或其他语义改变的清洗；middleware、AgentScope repair、external executor 改变参数时 fingerprint 必须失效。

## 3. Hermes 派生 Prompt 和三 token 审核协议

- [x] 3.1 把 design 附录 A 中已审定（2026-10-19）的 prompt 原文逐字落为 `backend/src/security/authorization/prompts/smartguard.md`，文件头记录上游 hermes-agent commit `d97007c3` 与 prompt 版本；加测试断言资源正文与附录 A 一致，任何正文修改都需重新经用户审定。
- [x] 3.2 建立唯一 prompt provider，计算 prompt version/digest；验证当前 Run 冻结版本，修改 prompt 只影响新 Run，不与旧 JSON prompt 拼接；删除 `review_contract.py` 中的旧 `PERMISSION_REVIEW_SYSTEM`。
- [x] 3.3 重写 `review_contract.py` 和 `reviewer.py` 的输出协议，去除首尾空白后严格接受小写 `allow`、`deny`、`needs_human`；拒绝 JSON、解释、Markdown、布尔值、大小写变体、`APPROVE/DENY/ESCALATE`、空响应和额外字符，非法结果转 `needs_human`。
- [x] 3.4 将平台 reason code、UI 文案、`doNotRetry`、failure source 从模型输出中移出，由风险分类和运行状态确定；删除 reviewer 中基于模型 reason_code 的证据校验和“每次模型 deny 均 doNotRetry”逻辑；验证模型不再输出 reason、confidence 或 missing evidence。
- [x] 3.5 设置 temperature 0、关闭 reasoning（供应商支持时）、短超时和 16 token 输出预算；验证超时、限流、断流、空响应、截断、非法 token、连接撤销和取消均不 allow，且不隐式 fallback 到其他模型；观测区分“空响应/截断”与“非法 token”。
- [x] 3.6 实现模型页保存时探测：用固定无害探测动作调用一次，输出预算内未返回合法 token 则拒绝保存为 SmartGuard 并显示原因；用 fake 推理模型（只返回空内容与 `finish_reason=length`）验证被拦截。

## 4. 统一 SmartGuard 决策和人工复核

- [x] 4.1 改造 `PlatformAuthorizationService` 的 model_review 分支：只有 `dangerous`/`unknown` 调用一次 SmartGuard，allow/deny/needs_human 均回到同一 decision 保存和执行前复核；验证其余 riskClass 不产生模型调用。
- [x] 4.2 保留 allow 的一次精确许可、deny 的不可改写历史和 needs_human 的 pending approval；验证同一工具新参数、目标版本或资源 revision 不复用旧许可。
- [x] 4.3 后端模型拒绝暂停复核：交互式 model deny 在同一事务中保存原 deny 并创建关联的 manual 复核 decision（`human_review_of_model_deny`）与 pending approval，adapter 挂起调用而不返回 `DENIED`；复用 `permission_records.py` 现有复核尝试构造；boundary、`human_only`、已终止 Run 不创建复核；Job 中 deny 直接返回 `DENIED`。
- [x] 4.4 前端复核卡变体：基于现有 `reviewable` 字段显示固定拒绝原因、风险规则和“维持拒绝”“人工批准这一次”，两者复用普通审批 answer API 与 resume；已结束 Run 的历史不显示按钮；刷新后同一复核卡恢复。
- [x] 4.5 验证模型拒绝复核不会改写原 decision、不会覆盖硬边界、只消费一次新许可，维持拒绝或过期才把拒绝交回 Agent，并在 Trace 中区分原 deny、复核 decision 和 execution。
- [x] 4.6 修复并重写熔断：在调用 SmartGuard 之前检查；按 `run + actor`（默认 3）与整轮 `run`（默认 6）双重累计；只读、平台内部、低风险和模型 allow 不清零，只有人工决定清零对应 actor；验证“插入 Read 不能重置”“Team 新成员不能绕过”“熔断后不再产生模型调用”。
- [ ] 4.7 修正 `service.py` 中 `reset_denials` 缺失的 `RunAuthorization` 导入，确保 Ruff/Mypy 通过。

## 5. 人工批准、批量事务和执行事实

- [x] 5.1 保留现有 ActionSnapshot、permission_decisions、permission_approvals、permission_executions 和执行前 reserve/start/finish 基础；定向测试确认人工批准不会改变参数或扩大范围。
- [x] 5.2 在 `permission_records.py` 增加批量 answer 事务（`BEGIN IMMEDIATE`）：整批校验归属、状态、过期时间、Run 和 fingerprint，任何一项失败全部回滚并返回逐条原因；测试过期一项、并发双击和部分失败。
- [x] 5.3 统一 approval、decision、execution 的状态投影：批准不等于执行成功，拒绝显示 denied，已启动但结果不明显示 unknown，错误显示 failed；验证 Trace、completion 和 DisplaySnapshot 使用同一事实，并展示 riskClass。
- [x] 5.4 验证普通工具、ALLOWED 恢复、并发调用、复核批准和 external executor 都只能取得一次 execution reservation；运行 `test_permission_records.py`、`test_permission_adapter.py` 和 external 回归。
- [x] 5.5 保留 secret 脱敏和受限参数引用；增加审计检查确认 SSE、UI、Trace、permission decision 和 SmartGuard 观测不含凭据或思维链，且 SmartGuard 可信区中的用户消息已截断。

## 6. 刷新、重启、Team、Subagent 和 Job

- [x] 6.1 保留后端重启对账和 live interaction 恢复的现有边界；验证进程内刷新恢复同一 interaction，重启后 pending/approved 未消费许可失效，unknown 不自动重放。
- [x] 6.2 明确取消顺序：先使 Run 无效，再清理 pending interaction（含复核卡）、approval waiter 和 Agent binding；测试迟到 allow、resume 和 external result 均不能执行。
- [x] 6.3 验证 Team Leader/Member、attached Subagent 和 external action 继承父 Run 模式与能力上限，委托动作为 `platform_internal` 但子执行者每个工具调用独立分类授权；不创建第二套决策或绕过统一执行链。
- [x] 6.4 验证 Job/manual/model_review 遇 needs_human（含 `human_only`、`insufficient_evidence`）、SmartGuard 异常、熔断或凭据失效时结束 firing、记录未执行原因并通知；模型 deny 以 `DENIED` 交回 Agent；不写 Chat pending、不隐式 full_access、不重试变体。
- [x] 6.5 验证 Job 明确重新运行创建新 Run、ActionSnapshot、decision 和 execution，旧 firing/许可不能复活；执行 WebSearch 系统 Job 的能力和必注入限制回归。

## 7. 删除旧评测体系、迁移配置和同步文档

- [x] 7.1 删除 `evaluation_data.py`、`evaluation.py`、`evaluate_cli.py`、`permission_evaluation_records.py`、200 条 JSON、`test_permission_evaluation.py`、`docs/permission-review-cases-to-label.json` 和 `docs/permission-review-evaluation.md`；用 `grep -rn` 确认没有运行时导入、命令入口或文档链接残留。
- [x] 7.2 删除 `require_review_validation()` 和评测 namespace 读写；`permission_review_status()` 与 Chat 发送/会话选择只检查 SmartGuard 配置、探测结果、已审定 prompt 版本和连接可用性，说明文案改为“危险或不确定的操作交给这个模型判断”。
- [x] 7.3 配置迁移：读取旧 `PermissionReviewConfig` 时忽略 JSON `schema_version`，prompt 版本改由 provider 提供，旧配置未通过探测时标为不可用；会话保存的下一轮模式为 `model_review` 但不可用时，前端标注并引导切换、后端拒绝发送并返回原因，不静默改模式；Job 使用 model_review 时在 UI 提示“遇不确定动作会结束本次运行”。
- [x] 7.4 更新 `docs/permission-system-v2.md`、`docs/permission-system-v2-data-contract.md`、`docs/project-todo.md`、`docs/README.md`、Chat SSE/权限展示文档：删除 200 条、holdout、质量门槛和评测报告引用；补充风险分类表、复核卡、熔断规则；保留 usage/延迟/故障观测说明。
- [x] 7.5 复查本变更的 proposal/specs/design，确认没有“所有非只读都审核”“严格 JSON”“评测通过才启用”“等待用户提供 prompt 原文”“事后复核 API”等旧描述；运行 `openspec validate permission-system-v2 --strict` 并记录结果。

## 8. 全链路验收与发布

- [x] 8.1 后端定向测试覆盖 manual/model_review/full_access 的相同执行链、八类风险分类、SmartGuard token parser、保存探测、异常收口、deny 暂停复核、累计熔断、批量事务和执行事实。
- [ ] 8.2 后端执行 `uv run pytest`、Ruff、Mypy，前端执行 `npm test`、`npm run build`、`npm run lint`，并执行 `git diff --check`；记录已有失败与本变更失败，不宣称未运行检查通过。
- [ ] 8.3 桌面浏览器 QA 覆盖模型配置与探测失败提示、三模式选择、当前 Run/下一轮区分、低风险与平台内部不送审、`human_only` 直接人工、模型 deny 复核卡（批准/维持/刷新/过期）、批量审批、取消、重启提示和 Job 通知。
- [x] 8.4 用无副作用工具验证完整时序：相同 tool call 在三模式下最终都进入同一实际执行函数；验证参数、目标、工具身份和执行结果没有因为模式改变。
- [ ] 8.5 验证 Team Member、attached Subagent、external executor、scheduled firing、硬边界和 partial outcome；保存可复现的日志/Trace/截图证据。
- [ ] 8.6 发布前确认 SmartGuard prompt 已经用户审定并记录版本与摘要；确认没有遗留评测代码、旧 JSON parser、隐式模型 fallback、AgentScope BYPASS 旁路、旧 Job 自动放行旁路或只读重置熔断的路径。
