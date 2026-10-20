# Spec Delta

## Purpose

统一所有执行入口的授权决定和工具执行行为，让人工批准、SmartGuard 和完全开放只改变授权决定来源，始终使用同一份精确动作快照、能力边界和执行前许可消费链路。

## ADDED Requirements

### Requirement: 执行审批只有三种模式

系统 MUST 在 Chat/Sandbox 仅提供 `manual / model_review / full_access` 三种模式，MUST NOT 在 Agent profile 保存审批模式或审核模型。新对话 MUST 默认 `manual`；session 保存下一轮选择；发送时冻结模式到 Run。切换 Agent、Team 或下一轮选择 MUST NOT 修改进行中的 Run 或批准既有 pending。

#### Scenario: 新对话默认人工

- **WHEN** 用户新建 Chat 且没有历史模式
- **THEN** 选择为 `manual`，不会从 Agent、Job 或主模型推断其他模式

#### Scenario: 会话选择和 Run 快照分离

- **WHEN** 当前 Run 等待批准，用户把下一轮选择改为 `full_access`
- **THEN** 当前 Run 仍按原模式等待，下一轮才使用 `full_access`

### Requirement: 三种模式必须共用同一执行链

所有模式 MUST 使用同一条链路：规范化工具参数并创建 `ActionSnapshot`，生成唯一 decision，执行边界和目标版本复核，原子 `reserve`，`start`，调用实际工具，最后 `finish`。模式不得替换工具参数、工具实例、执行入口或执行结果协议。AgentScope 的 `ALLOWED`、`BYPASS`、remembered rule 和 suggested rule MUST NOT 成为平台授权证据。

#### Scenario: 三种模式执行同一动作

- **WHEN** 同一个工具调用分别在 manual、model_review 和 full_access 下运行
- **THEN** 三次使用相同的规范化输入、工具身份和执行入口，差异只在 decision 的来源

#### Scenario: ALLOWED 不能绕过平台

- **WHEN** AgentScope 恢复一个标记为 `ALLOWED` 的工具调用
- **THEN** 平台仍重新取得当前 ActionSnapshot 并校验 decision、边界、目标版本和一次许可

#### Scenario: external 使用同一许可

- **WHEN** 工具通过 external executor 分发
- **THEN** external 在实际分发前使用同一 decision 领取 reserve/start，不能从 acting 或人工事件旁路执行

### Requirement: 能力边界优先于审批模式

系统 MUST 先校验 tenant、workspace、project、工具范围、文件范围、网络、沙箱、执行现场、目标版本和取消状态，再决定是否需要人工或 SmartGuard。显式禁止、能力收紧和硬边界拒绝 MUST 优先于只读直通、SmartGuard 和 `full_access`；任何模式和人工复核都不能扩大能力范围。Plan 只读和 AskUser 业务信息合同不因 `full_access` 关闭。

#### Scenario: full_access 不能越界

- **WHEN** `full_access` 访问另一租户文件、未绑定资源、关闭的网络或已取消 Run
- **THEN** 调用直接被 boundary 拒绝，不调用 SmartGuard，也不创建人工许可

#### Scenario: 人工不能覆盖边界

- **WHEN** 用户尝试批准超出文件范围或被租户拒绝的行动
- **THEN** 不产生可执行许可，记录具体 boundary 原因

### Requirement: 风险分类必须由平台确定

系统 MUST 在硬边界检查之后，用服务端工具身份、effect、目标和能力事实把每个行动确定性地归入 `boundary / trusted_readonly / platform_internal / low_risk / human_only / dangerous / unknown / insufficient_evidence` 之一，并记录风险规则 key 和描述；不能只相信工具名称、HTTP method、MCP annotations 或 Agent 说明。初始分类表以 design 决策 2 为准，未登记行为 MUST 归为 `unknown`，分类异常 MUST 归为 `unknown`。三种模式 MUST 按 design 决策 1 的矩阵决定 decision 来源：`trusted_readonly` 与 `platform_internal` 在所有模式代码 allow；`low_risk` 在 manual 需要人工、在 model_review 与 full_access 代码 allow；`human_only` 与 `insufficient_evidence` 在 manual 与 model_review 都直接需要人工；`dangerous` 与 `unknown` 在 manual 需要人工、在 model_review 调用 SmartGuard。平台日志、投影、审核记录和回复输出不递归触发授权。

#### Scenario: 平台内部协调动作

- **WHEN** Agent 在 manual 下调用 Plan 或 Team 协调工具（如 `PlanCompleteTool`、`SendMail`）
- **THEN** 代码直接 allow，不创建人工审批；子执行者后续的工具调用仍各自分类与授权

#### Scenario: Session 根内写入

- **WHEN** Agent 用 `ScopedWrite` 写入当前 Session 根内的非控制面文件
- **THEN** manual 创建人工审批，model_review 代码 allow 且不调用 SmartGuard，full_access 代码 allow

#### Scenario: Session 根外写入

- **WHEN** Agent 写入用户全局根或其他授权外部目录
- **THEN** 归为 `dangerous`，model_review 调用 SmartGuard

#### Scenario: 永不自动批准

- **WHEN** model_review 下 Agent 修改 Skill/profile/MCP 定义、确认 Job 或执行结束本应用进程的 Shell
- **THEN** 归为 `human_only`，不调用 SmartGuard，直接创建人工审批

#### Scenario: 未登记 MCP 自称只读

- **WHEN** 未经可信登记的 MCP 工具声明只读
- **THEN** manual 进入人工，model_review 进入 SmartGuard，不能因为 annotation 直通

#### Scenario: Shell 未完整解析

- **WHEN** Shell 含管道、重定向、命令替换、未解析变量或任意脚本
- **THEN** 不能认定为只读低风险，按危险或未知行动处理

#### Scenario: 平台内部记录不递归审批

- **WHEN** 系统写 permission decision、Trace 或 DisplaySnapshot
- **THEN** 这些平台事实不再次生成工具审批

### Requirement: 精确动作、资源和目标版本必须绑定

ActionSnapshot MUST 包含规范化参数、工具身份和版本、tenant/workspace/project/run/actor、目标、稳定能力版本、实际消费的资源修订和目标版本。指纹变化于参数、路径、内容、工具配置、资源修订或目标版本变化。批准或 SmartGuard allow MUST 只覆盖该指纹；secret 实值 MUST NOT 写入普通提示、SSE 或审计。

#### Scenario: 参数变化失效

- **WHEN** 批准后客户端或 middleware 修改工具参数
- **THEN** 指纹不一致，执行前拒绝，不能使用旧许可

#### Scenario: Skill 资源变化不扩大授权

- **WHEN** 当前 Run 修改已激活 Skill 的正文
- **THEN** 当前行动使用原冻结指令和相关 resourceRevision，新内容从下一次激活生效，不产生额外工具权限

### Requirement: model_review 只在风险命中时调用 SmartGuard

在 `model_review` 中，只有 `dangerous` 与 `unknown` 行动 MUST 调用 SmartGuard；`trusted_readonly`、`platform_internal`、`low_risk` MUST 不调用模型；`human_only` 与 `insufficient_evidence` MUST 直接创建人工审批而不调用模型。SmartGuard 结果只能是 `allow / deny / needs_human`，错误和非法结果按 `needs_human` 处理。该规则不改变 manual 的人工批准和 full_access 的代码 allow。

#### Scenario: 低风险智能模式不产生审核调用

- **WHEN** model_review 执行平台已证明的低风险读取
- **THEN** 代码直接 allow，审核 usage 为零，实际执行仍走 reserve/start/finish

#### Scenario: 危险动作智能审核

- **WHEN** model_review 执行 Session 根外 Write、未解析 Shell、外发或未知 MCP
- **THEN** 只调用一次 SmartGuard，结果进入同一 decision service

#### Scenario: 证据缺失不送模型

- **WHEN** model_review 行动的目标、去向或文件版本无法解析
- **THEN** 归为 `insufficient_evidence`，直接创建人工审批，审核 usage 为零

### Requirement: 审批和执行恢复不能旁路

实际工具或 external 分发前 MUST 再次检查当前能力、取消、凭据、目标版本和 action fingerprint。重复 resume、双击批准、框架恢复和并发调用 MUST 最多取得一次 execution reservation。执行结果 MUST 与 decision 分开记录；unknown 不得自动重发。

#### Scenario: 重复恢复

- **WHEN** 两个 resume 请求同时处理相同 approved decision
- **THEN** 只有一个请求从 reserved 进入 running，另一个得到既有状态且工具最多启动一次

#### Scenario: 外部结果不明

- **WHEN** 外部发送已发出但连接断开
- **THEN** execution 标为 `unknown`，不自动重发、不伪装 failed 或 succeeded

### Requirement: Team、Subagent 和 Job 继承本轮模式

Team Leader、Member 和 attached Subagent MUST 继承父 Run 的模式和能力上限，子定义只能收窄能力。委托动作本身归为 `platform_internal`，但子执行者的每个工具调用 MUST 独立分类与授权。Job MUST 保存创建时的 `executionPolicy.approvalMode`，firing 只读取该策略，不读取 Job Agent 的审批偏好。Job、Webhook 和其他无人值守入口遇人工需求、SmartGuard 异常或熔断 MUST 结束本次执行并通知，不能创建无人可处理的 Chat pending、隐式切换 `full_access` 或重试变体。

#### Scenario: Team 成员需要人工

- **WHEN** Team Member 的危险行动需要人工
- **THEN** 只挂起该 Member，其他无依赖 Member 可继续；批准后仍使用该 Member 的 ActionSnapshot 和统一许可

#### Scenario: Subagent 不能自行开放

- **WHEN** manual 父 Run 委托旧 trusted/bypass 定义的 Subagent
- **THEN** child 不能扩大父能力，非只读行动仍需父模式规定的授权

#### Scenario: Job 无人值守遇人工需求

- **WHEN** Job 的 manual 或 model_review 行动需要人工（含 `human_only`、`insufficient_evidence`）、超时或熔断
- **THEN** firing 结束并通知，旧执行现场不等待、不重放，用户明确重新运行才创建新 Run

#### Scenario: Job 遇模型拒绝

- **WHEN** Job 的 model_review 行动被 SmartGuard 返回 deny
- **THEN** 以 `DENIED` 交回 Agent，不创建人工复核，Agent 可换路径但受累计拒绝熔断约束

### Requirement: 迁移移除旧审批叠加

Agent profile MUST 只保留能力边界；旧 AgentScope mode、trusted/bypass、remembered allow rule 和 `humanApproval` 只能在导入迁移阶段读取。新 Chat 没有明确选择时默认 manual；旧定时策略迁移为任务级策略，不能把旧 Agent trusted 自动变成 Chat full_access。新运行 MUST 不双跑旧决策。

#### Scenario: 旧 trusted Agent 进入 Chat

- **WHEN** 旧 trusted Agent 在没有明确 Chat 选择的会话中运行
- **THEN** Chat 使用 manual，Agent 只提供迁移后的能力范围

#### Scenario: 迁移幂等

- **WHEN** 同一 profile、session 或 Job 重复执行迁移
- **THEN** 模式和能力不继续扩大，旧字段不会覆盖用户当前选择

### Requirement: 群聊产品保持隔离

群聊工作室 MUST 继续使用自身 API、SSE、Worker 工具和审批兜底；本变更不得把群聊回复改成 Chat 工具审批，不得写入 Chat pending，不得增加可见 Host 或新阶段。

#### Scenario: 群聊回复

- **WHEN** Worker 向群聊房间发布回复
- **THEN** 按群聊合同落消息，不创建 Chat permission decision 或审批卡
