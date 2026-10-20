# 权限 V2 数据合同与接入映射

本表对应 `permission-system-v2` 任务 1.1。当前阶段只建立独立领域合同；后续接线前不改变旧 Runtime 的行为。AgentScope 固定为 2.0.5。

| 归属 | 当前权威 / 缺口 | V2 Python / API 字段 | 持久化与默认值 |
| --- | --- | --- | --- |
| Chat 请求 | `routes/chat/models.py::ChatMessage`，当前仅有执行 `mode` 和 `modelConfigId` | `approval_mode` / `approvalMode` | 缺失时读 session 明确选择，否则 manual；不能由 Agent 推导 |
| Chat 选择 | `ChatSession`、`storage/chat_session_records.py`，当前无审批字段 | `approval_mode` / `approvalMode` | 租户 `chat_sessions.approval_mode`；新/历史无选择均 manual，切 Agent/Team 保留，切 session 恢复 |
| 本轮授权 | `agents/runtime_types.py`、`runtime/agent_spec.py`、`chat_run_records.py` | `RunAuthorization`：`tenantId/workspaceId/projectId/sessionId/runId/actorId/approvalMode/capabilityVersion/policyVersion/reviewerConfigVersion` | `chat_runs.authorization_json` 冻结；下一轮选择不能修改活跃 Run；缓存含 session/actor/模式/能力/adapter 版本 |
| Agent 能力 | `catalog/agent_models.py::Guardrails`、`AgentGuardrails`、`frontend/src/types/agent.ts`、Workspace Markdown profile | `operation_scope` / `operationScope`，另保留文件/网络/工具/沙箱限制 | profile 是权威，不写 Agent 正文到 SQLite；审批模式/审核模型/框架枚举不进入新 Agent 导出 |
| 审核配置 | `config/model_catalog.py::ModelCatalog`，当前无审核用途绑定 | `permission_review` / `permissionReview`，含 `modelConfigId/timeoutSeconds/maxOutputTokens/schemaVersion/promptVersion` | 租户 models.json；沿现有 `/api/settings/models`、etag/原子保存；缺省 null，不推断主模型，删除/停用显示不可用 |
| 定时策略 | `scheduler/models.py::ScheduledJob`、`catalog/job_files.py`、scheduled_jobs；当前存在 unattended overlay | `execution_policy` / `executionPolicy.approvalMode` | Job 文件与实例保存创建 Run 的模式；模型参数不能覆盖；无来源且无显式策略则 manual；firing 独立冻结 |
| 精确行动 | 当前 SDK ToolCallBlock 不含平台归属、目标版本 | `ActionSnapshot`：`schemaVersion/authorization/callId/toolName/toolVersion/normalizedInputJson/targets/effect` | 租户 decision 内的版本化快照；输入为不可变规范 JSON，目标/内容版本均参与 fingerprint；凭据实值不进入快照 |
| 权威决策 | 旧 profile、SDK engine 与 Trace 各自解释 | `AuthorizationDecision`：`id/actionFingerprint/verdict/source/reasonCode/reason` | `permission_decisions`：call + 行动版本唯一，关联 Run/actor；来源为 readonly/manual/model/model_error/boundary/full_access |
| 人工审批 | 进程内 PendingInteraction；rememberApproval 生成宽泛规则 | `ApprovalRecord`：`id/decisionId/status/approvedBy/expiresAt` | `permission_approvals`：pending/approved/rejected/cancelled/expired/interrupted；仅精确行动，不记整个工具 |
| 执行事实 | 当前工具 start/end，不能证明一次许可消费 | `ExecutionRecord`：`id/decisionId/status` | `permission_executions`：reserved/running/succeeded/failed/unknown；一次事务占位，外部 unknown 不重发 |

三类权限事实表均包含稳定主键、created_at/updated_at、decision 外键和唯一约束，通过租户数据库连接与 `storage/permission_records.py` 访问。产品域只经 `src.runtime.authorization` 公开入口调用，不直接依赖 SDK 或 Chat 私有模块。前端新增合同落在独立 authorization types；Agent 类型只接能力字段。

## 迁移矩阵

| 旧配置 | Agent 能力处理 | Chat / 定时策略处理 |
| --- | --- | --- |
| read_only | 保留 `operationScope=read_only`，不能写文件/用 Bash/HTTP/MCP，保留原网络与工具限制 | Chat 无明确选择为 manual，不因批准扩大只读能力 |
| trusted_tools / bypass | 备份旧值并移除审批字段，保留能力 | 不映射开放 Chat；既有定时实例原有效策略迁为任务级 full_access |
| confirm_destructive / accept_edits | 保留能力，移除审批字段 | 既有定时实例任务级 manual |
| ask_every_time / humanApproval=true | 保留能力，移除审批字段 | 既有定时实例任务级 manual；humanApproval 优先于 trusted |
| 未知 / 缺失 | 保留可识别能力，记录迁移证据 | 无明确选择 Chat 和旧任务 manual |
| 已有明确 session / 任务策略 | 不覆盖 | 幂等保留，系统 WebSearch 的角色与必注入不能重写策略 |

字段别名检查：新 API 使用 camelCase，Python 使用 snake_case；版本字段首版为 1，旧版本拒绝静默修复。tenant 从服务端执行上下文取得，不接受模型提供。批准者从认证上下文取得，不能信任请求正文。敏感原文另用受限引用；普通快照、提示和日志不存 Key/Cookie/Authorization。
