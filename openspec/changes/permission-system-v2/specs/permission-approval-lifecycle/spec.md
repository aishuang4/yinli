# Spec Delta

## Purpose

将人工批准严格绑定实际调用，并把模型拒绝复核、决策、审批和执行结果作为可审计事实保存，使刷新、取消、并发恢复与后端重启不会扩大授权或重复执行，同时让用户准确理解批准了什么。

## ADDED Requirements

### Requirement: 批准绑定一次精确行动

人工批准 MUST 绑定工具身份及版本、规范化参数、最终目标、变更内容、运行归属、稳定授权版本、相关资源修订和目标版本，且只用于当前一次调用。系统 MUST 只提供“允许这一次”“拒绝”；MUST NOT 生成会话级工具允许规则。批量批准 MUST 仅覆盖界面完整列出的有限调用，不能覆盖未来调用。

#### Scenario: 相同工具新调用

- **WHEN** 用户批准 Bash 的一个精确调用，然后 Agent 提出另一条命令
- **THEN** 新命令需要独立决策与批准，不继承工具级或会话级允许

#### Scenario: 批量批准范围

- **WHEN** 用户批准卡片明确列出的三个写入调用
- **THEN** 只有三个指纹进入批准状态，第四个调用仍需独立决策

### Requirement: 批量审批必须原子处理

同一审批卡的多个 decision MUST 以一个服务端事务完成批准或拒绝。任何一条 decision 归属不匹配、已过期、已处理、Run 已终止或快照变化时，整批 MUST 不改变状态并返回逐条失败原因。重复点击 MUST 返回既有状态，不得形成部分批准。

#### Scenario: 批量中一条过期

- **WHEN** 三条待批准调用中一条在提交前过期
- **THEN** 三条都不变为 approved，用户收到过期调用信息，不产生部分许可

#### Scenario: 批量重复点击

- **WHEN** 两个请求同时批准同一批调用
- **THEN** 只有一个请求提交成功，另一个读取既有状态，工具最多按每个 decision 执行一次

### Requirement: 执行前复核与许可一次消费

批准和 execution reservation MUST 原子处理并防止双击、重复事件和并发恢复触发重复执行。实际参数与批准快照不一致、目标或文件版本改变、许可过期、能力收紧、凭据撤销或 Run 取消 MUST 阻止旧许可执行。无法确定外部操作是否完成时 MUST 记录 `unknown`，MUST NOT 自动重发或声称 exactly-once。

#### Scenario: 文件批准后被修改

- **WHEN** 用户批准覆盖某版本文件，恢复前文件内容已变化
- **THEN** 原批准不能覆盖新内容，系统重新决策并展示新快照

#### Scenario: 外部结果不明

- **WHEN** 发消息请求发送后连接断开，系统无法确定服务是否已接收
- **THEN** 记录 `unknown`，不自动重发，后续处理先对账

### Requirement: 归属校验与敏感记录隔离

审批 API MUST 验证批准者和 tenant/workspace/project/run/actor 归属。客户端 MUST NOT 替换批准快照中的工具输入。决策、审批和执行事实 MUST 在所属租户持久化并可关联。普通 UI、日志、SSE 和审计 MUST 脱敏；secret、Cookie、Authorization 和受限正文 MUST 遵守租户访问控制。

#### Scenario: 跨租户审批

- **WHEN** 用户提交另一租户的 decision 或 approval ID
- **THEN** 请求被拒绝，不改变审批状态、不执行工具、不暴露内容

#### Scenario: 客户端修改参数

- **WHEN** 批准请求携带不同于服务器快照的执行参数
- **THEN** 不能修改行动，执行只能使用服务端经复核的规范化快照

### Requirement: 模型拒绝暂停并人工复核

交互式 Chat/Sandbox 中模型 `deny` MUST 暂停当前调用，不得先把拒绝结果交回 Agent。服务端 MUST 在同一事务中保存原模型 deny decision，并为同一 call 和同一服务端快照创建一条关联原 decision 的 manual 复核 decision（`reasonCode=human_review_of_model_deny`）与 pending approval。Chat MUST 以“模型拒绝”审批卡变体显示固定拒绝原因、风险规则和“维持拒绝”“人工批准这一次”两个按钮，两者复用普通审批 answer API。批准后 MUST 重新检查当前能力、目标版本和硬边界，只能消费一次许可；维持拒绝或过期 MUST 以拒绝结果交回 Agent。原 decision MUST 永久保持 deny，不能被改写或复活。来源为 boundary、`human_only` 或 Run 已终止时 MUST NOT 创建复核 decision。Run 结束后 MUST NOT 提供事后复核。

#### Scenario: 复核模型误拒绝

- **WHEN** SmartGuard 拒绝一个目标明确且符合硬边界的行动，用户在复核卡点击“人工批准这一次”
- **THEN** 复核 decision 变为 approved，原调用进入统一 reserve/start/finish 链，Trace 同时保留原模型 deny 与人工复核 decision

#### Scenario: 维持拒绝

- **WHEN** 用户点击“维持拒绝”或复核卡过期
- **THEN** 工具不执行，Agent 收到拒绝结果，该次模型拒绝保留在累计熔断计数中

#### Scenario: 刷新后复核卡仍在

- **WHEN** 复核卡等待期间用户刷新 Chat，原执行现场仍存活
- **THEN** 同一复核卡与原拒绝原因重新显示，重复点击返回既有状态

#### Scenario: 复核不能越界

- **WHEN** 用户尝试人工覆盖文件范围、租户边界或已撤销能力拒绝
- **THEN** API 返回拒绝，不创建可执行许可

#### Scenario: 复核参数不可替换

- **WHEN** 客户端在复核卡的 answer 请求中提交新的工具输入
- **THEN** 服务端忽略/拒绝替换输入，只使用原 decision 的服务端快照

#### Scenario: Run 结束后不能复核

- **WHEN** 用户查看已结束 Run 中的模型拒绝历史
- **THEN** 只显示拒绝事实和原因，不显示可执行的复核按钮

### Requirement: 刷新与重启不重放执行

浏览器刷新且原执行现场仍存活时 MUST 恢复同一 pending interaction、decision 和 action，不新建决策或重复审核。后端重启 MUST 将待审批、已批准但未消费的许可标为 `interrupted` 或 `expired`；历史仍可查看，但 MUST NOT 展示可执行的过期按钮或自动执行旧动作。unknown 不得自动重试。

#### Scenario: 浏览器刷新

- **WHEN** 原 Run 仍在进程内等待人工，用户刷新 Chat
- **THEN** 同一审批和行动快照重新显示，重复批准返回既有状态

#### Scenario: 后端重启

- **WHEN** 后端在人工批准后、执行消费前重启
- **THEN** 未消费许可失效，历史可见，用户必须明确重新运行才创建新行动

### Requirement: 取消、拒绝和执行结果分别记录

取消、拒绝、批准和工具执行终态 MUST 是不同事实。命令响应 MUST NOT 将审批成功伪装成执行成功；工具启动前的拒绝 MUST 记录 `denied`，工具已启动但结果不明 MUST 记录 `unknown`。

#### Scenario: 用户拒绝

- **WHEN** 用户在审批卡点击“拒绝”
- **THEN** 工具不执行，decision/approval 为 rejected，执行摘要显示 denied

#### Scenario: 工具执行失败

- **WHEN** 用户批准后工具已启动但返回错误
- **THEN** approval 保持 approved，execution 记录 failed，不能把失败改写成权限拒绝

### Requirement: 部分执行必须按事实收口

系统 MUST 从 permission execution 记录和工具终态生成本轮摘要，分别保留 succeeded、failed、denied 和 unknown。一个批次中已有行动 succeeded，而后续行动 failed、denied、unknown 或因硬边界终止时，Run MUST 标记并展示 `partial`；最终答复 MUST 明确哪些目标已落盘、哪些未执行、哪些结果不明，不得根据 Agent 自述推断副作用。

#### Scenario: Skill 修改到一半被阻断

- **WHEN** 同一批次先成功修改脚本和 `SKILL.md`，后续修改或验证被拒绝
- **THEN** Run 显示 partial，最终答复列出已成功目标和被拒绝行动

#### Scenario: 执行错误与边界拒绝分开表达

- **WHEN** 一个 Edit 因 old_string 不匹配失败，随后另一个行动因硬边界拒绝
- **THEN** execution facts 分别记录 failed 与 denied，界面不能合并成同一个权限错误

### Requirement: 界面与审计采用真实决策

Chat MUST 提供 manual、model_review、full_access 三种下一轮选择；模型页 MUST 配置 SmartGuard，Agent 管理 MUST 只配置能力边界。审批卡 MUST 展示 actor、工具、精确目标、影响、风险分类、diff/外发摘要、decision 来源、固定原因、状态和过期时间。交互式模型拒绝 MUST 展示复核卡变体。Trace MUST 区分 readonly、platform_internal、low_risk、human_only、manual、model、model_error、boundary 和 full_access，并分开表达 decision 与 execution；不输出思维链或凭据。

#### Scenario: 配置入口职责清晰

- **WHEN** 用户分别打开 Chat、模型配置页与 Agent 管理
- **THEN** Chat 选择模式，模型页配置 SmartGuard，Agent 管理没有模式或审核模型字段

#### Scenario: 待批准期间修改下一轮选择

- **WHEN** 当前 Run 等待人工，用户把下一轮选择改为 full_access 并切换 Agent
- **THEN** 下一轮选择更新，当前 Run 仍显示原模式和原审批卡，旧调用不会因此获批

#### Scenario: 模型拒绝入口

- **WHEN** 交互式 Run 中 SmartGuard 返回 deny
- **THEN** 工具暂不执行，模型拒绝复核卡显示固定原因与“维持拒绝”“人工批准这一次”；批准只消费一次许可

#### Scenario: 被拒绝并非已执行

- **WHEN** 用户拒绝写文件或边界阻止行动
- **THEN** 展示拒绝来源及未执行状态，不把 approval 或 tool start 当作执行成功
