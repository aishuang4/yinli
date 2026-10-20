# Spec Delta

## Purpose

为危险或语义不确定的精确行动提供独立 SmartGuard，让审核模型只影响当前行动的授权来源，不改变工具参数、执行入口和平台硬边界；模型无法可靠判断时必须转人工或结束无人值守执行。

## ADDED Requirements

### Requirement: 审核模型单独配置、保存探测与运行冻结

系统 MUST 在模型配置页提供“自动审核模型”选择，复用现有模型目录和原子保存。保存为 SmartGuard 前，系统 MUST 用固定无害探测动作调用一次该模型，只有在输出预算内返回合法 token 才允许保存；空响应、截断（如推理模型耗尽预算）、非法 token 或连接失败 MUST 拒绝保存并显示原因。新 `model_review` Run MUST 冻结显式审核模型连接、超时、输出限制和 prompt 版本；模型配置变化只影响新 Run。未配置、删除、停用、探测失败或凭据失效的审核模型 MUST 阻止新智能 Run 或使当前待审核行动转 `needs_human`，MUST NOT 隐式使用主模型、其他供应商或未选择的 fallback。

#### Scenario: 审核配置独立于主模型

- **WHEN** 主 Run 使用供应商 A，模型页把 SmartGuard 配置为供应商 B
- **THEN** SmartGuard 调用 B，主 Run 仍使用 A，两者不共享历史、工具、可变上下文或运行缓存

#### Scenario: 推理模型探测失败

- **WHEN** 用户选择一个无法关闭 reasoning、在输出预算内返回空响应的模型作为 SmartGuard
- **THEN** 保存被拒绝并提示“该模型在审核预算内无法给出结果”，已有 SmartGuard 配置不变

#### Scenario: 配置变化不影响当前 Run

- **WHEN** SmartGuard Run 已开始，用户更换或停用模型页配置
- **THEN** 当前 Run 继续使用冻结 binding；凭据撤销在下一次审核或执行前生效，新 Run 使用新配置

#### Scenario: 未配置审核模型

- **WHEN** 用户在没有有效 SmartGuard 配置时选择 `model_review`
- **THEN** 前端显示模型页入口，后端拒绝新智能 Run；`manual` 和 `full_access` 仍可选择

#### Scenario: 升级后保存的模式不可用

- **WHEN** 会话保存的下一轮模式为 `model_review`，但升级后 SmartGuard 尚无可用 prompt 版本或配置未通过探测
- **THEN** 前端在模式选择处标注不可用并引导切换，后端拒绝发送并返回明确原因，不静默改为其他模式

### Requirement: SmartGuard 只审核 dangerous 和 unknown 行动

系统 MUST 先执行租户、Project、工具、文件、网络、沙箱和取消状态的硬边界检查，再进行风险分类。`trusted_readonly`、`platform_internal` 和 `low_risk` 行动 MUST 不调用 SmartGuard。`human_only` 与 `insufficient_evidence` MUST 直接转人工，不调用 SmartGuard。只有 `dangerous` 与 `unknown` MUST 进入 SmartGuard。硬边界拒绝 MUST 直接拒绝，不能交给模型或人工覆盖。

#### Scenario: 可信只读不调用模型

- **WHEN** Agent 在能力范围内读取文件或执行平台证明的只读查询
- **THEN** 代码直接产生 `allow`，不产生 SmartGuard 调用、审核 token 或审核 usage

#### Scenario: 平台内部与低风险不调用模型

- **WHEN** model_review 下 Agent 调用 Team 协调工具或写入 Session 根内的非控制面文件
- **THEN** 代码直接 allow，审核 usage 为零

#### Scenario: 危险行动进入 SmartGuard

- **WHEN** Session 根外写入、删除、外发、未知 MCP 或未解析 Shell 被分类为 `dangerous` 或 `unknown`
- **THEN** 当前精确 ActionSnapshot 只调用一次 SmartGuard，模型结果进入统一授权服务

#### Scenario: 永不自动批准类别

- **WHEN** model_review 下行动被分类为 `human_only`（控制面修改、确认 Job、修改审批或审核配置、自终止 Shell、读取凭据并外发）
- **THEN** 不调用 SmartGuard，直接创建人工审批

#### Scenario: 硬边界优先

- **WHEN** ActionSnapshot 超出租户、Project、文件、网络、沙箱或工具能力边界
- **THEN** 系统直接返回 `deny`/`boundary`，不调用 SmartGuard，不创建可执行人工许可

### Requirement: 审核输入必须是精确且最小的可信分区

SmartGuard MUST 接收分区输入：可信区包含风险分类、风险规则描述、工具身份、操作影响、解析后的目标、必要执行环境、触发当前 Run 的用户原始消息（截断；Job 为冻结 Job prompt）和平台提取的明确用户限制；不可信区只包含精确规范化动作。工具输出、网页、文档、Skill 正文、Agent 自述、完整对话历史、思维链和凭据 MUST NOT 进入任何一区，不可信材料 MUST NOT 获得授权身份。审核使用的动作输入 MUST 与最终执行输入完全一致，MUST NOT 通过删除 Shell 注释或其他语义改变的清洗生成另一份动作。

#### Scenario: 不可信文档伪造授权

- **WHEN** 动作正文中的注释或字符串声称用户已经批准外发，而可信区的用户原始消息没有该授权
- **THEN** 该文本只作为不可信数据，不能使 SmartGuard 返回可执行授权

#### Scenario: 必要证据缺失

- **WHEN** 目标、去向、文件版本或执行环境无法完整解析
- **THEN** 行动归为 `insufficient_evidence`，系统不让模型猜测，直接产生 `needs_human` 或对应硬边界拒绝

#### Scenario: 审核动作与执行动作一致

- **WHEN** AgentScope 在审核后改变工具参数、路径、目标或调用身份
- **THEN** action fingerprint 不一致，执行前复核拒绝，旧 SmartGuard 结果不能被消费

### Requirement: Hermes 派生提示词是唯一模型指令来源

系统 MUST 使用仓库内唯一一份 SmartGuard prompt 资源作为审核 system prompt。该资源 MUST 以 NousResearch hermes-agent `tools/approval_smart.py` 的守卫提示词为基础改写，记录上游 commit、自身版本和摘要，覆盖非 Shell 动作、要求小写三 token 输出，并且不依赖删除注释。实现 MUST NOT 在代码中维护第二套权限提示词，不得与旧 JSON 审核 prompt 拼接。prompt 资源 MUST 与经用户审定的正文逐字一致并固定版本；不一致或未固定版本时，SmartGuard MUST 保持不可用。运维策略如需扩展 MUST 只进入可信 system 区域，动作正文 MUST 作为不可信输入放入明确分隔区。

#### Scenario: Prompt 未审定

- **WHEN** 运行环境没有经审定的 SmartGuard prompt 版本
- **THEN** SmartGuard 不进入可用状态，不能用临时 prompt 或旧 JSON prompt 代替

#### Scenario: Prompt 版本冻结

- **WHEN** 新 Run 开始后 prompt 文件被修改
- **THEN** 当前 Run 使用冻结 prompt 版本与摘要，新 Run 才读取新版本

### Requirement: 模型只允许三个精确 token

SmartGuard 模型 MUST 只能返回 `allow`、`deny` 或 `needs_human` 三个小写 token。系统 MUST 在去除首尾空白后严格匹配单个 token，MUST NOT 接受 JSON、解释文字、Markdown、布尔值、confidence、大小写变体、`APPROVE/DENY/ESCALATE` 或任何额外字段。空响应、非法 token、截断、解析异常和供应商错误 MUST 统一转为 `needs_human`，并由平台生成内部 reason code；模型不负责输出 reason、missing evidence 或 doNotRetry。

#### Scenario: 三种有效输出

- **WHEN** 模型分别返回 `allow`、`deny`、`needs_human`
- **THEN** 平台分别产生对应来源的精确决策，不改变 ActionSnapshot

#### Scenario: 非法输出不能放行

- **WHEN** 模型返回 JSON、`true`、`APPROVE`、`Allow`、`allow.`、解释段落或空字符串
- **THEN** 平台产生 `needs_human`/`model_error`，绝不产生 `allow`

#### Scenario: 模型没有理由字段

- **WHEN** 模型只返回一个合法 token
- **THEN** UI 和 Trace 使用平台风险规则、动作摘要和固定 reason code，不等待模型生成理由

### Requirement: 决策只覆盖当前精确行动

SmartGuard `allow` MUST 只为当前 action fingerprint 创建一次许可，不得缓存为工具、命令模式、资源或会话级授权。交互式场景中 `deny` MUST 暂停当前调用并创建一次受控人工复核（见 permission-approval-lifecycle），不得先把拒绝结果交回 Agent；无人值守场景中 `deny` MUST 以拒绝结果交回 Agent。`needs_human` MUST 创建绑定原 decision 的人工审批。人工复核只能创建新的 decision，不能改写原 decision 或覆盖硬边界。

#### Scenario: SmartGuard allow 一次消费

- **WHEN** SmartGuard 允许一次 Write 或 Bash 调用
- **THEN** 该调用最多消费一次许可，同工具的新参数或同资源的新版本仍需新决策

#### Scenario: SmartGuard deny 暂停复核

- **WHEN** 交互式 Chat 中 SmartGuard 返回 deny
- **THEN** 当前调用挂起并显示模型拒绝复核卡；用户批准后仍走统一 reserve/start/finish 链，维持拒绝或过期才把拒绝交回 Agent

#### Scenario: 硬边界拒绝不可复核

- **WHEN** 原 decision 的来源是 boundary 或目标超出能力范围
- **THEN** 不创建人工复核，不产生可执行许可

### Requirement: 审核异常保守收口

审核超时、限流、断流、空响应、截断、非法 token、连接撤销、凭据失效和取消 MUST 记录脱敏故障来源，并区分“空响应/截断”与“非法 token”。交互式 Chat/Sandbox MUST 转人工；Job、Webhook 和其他无人值守入口 MUST 结束本次执行并通知。系统 MUST NOT 默认 allow、隐式换模型、无限重试、自动切换 `full_access` 或留下无人可处理的 pending。

#### Scenario: 交互式审核超时

- **WHEN** SmartGuard 超过 Run 配置的超时时间
- **THEN** 工具尚未执行，创建 `needs_human` 审批并显示超时原因

#### Scenario: Job 审核异常

- **WHEN** 定时 firing 的 SmartGuard 超时或返回非法 token
- **THEN** 本次 firing 结束，记录未执行原因并通过通知告知用户，不创建 Chat pending、不重试参数变体

#### Scenario: 取消后的迟到 allow

- **WHEN** Run 在模型响应返回前被取消
- **THEN** 迟到响应无效，不能 reserve、start 或执行工具

### Requirement: 累计拒绝熔断与运行观测

系统 MUST 在 Run 内累计模型 deny（每次在产生时计一次，维持拒绝或复核过期不额外计数）与同一 reason 的重复硬边界拒绝，分别按 `run + actor` 与整轮 `run` 计数（默认阈值 3 与 6，可配置）。熔断检查 MUST 在调用 SmartGuard 之前执行；已熔断时 MUST 直接返回 `deny` 与 `doNotRetry=true`，不再调用模型，禁止通过换工具、换参数、换表达、换执行者或重复审核尝试相同效果。只有人工决定 MUST 清零对应 actor 计数，新用户消息开启的新 Run 自然清零；只读、平台内部、低风险直通和模型 allow MUST NOT 清零。普通模型 deny 未熔断时 `doNotRetry` 为 false。系统 MUST 记录实际 provider/model、prompt 版本与摘要、耗时、输入/输出 token、riskClass、判定来源、失败原因和熔断结果；这些观测不构成模型质量启用门槛。

#### Scenario: 熔断后不再调用模型

- **WHEN** 同一 run/actor 已累计三次 SmartGuard 拒绝，Agent 又提出同一效果的变体
- **THEN** 系统不调用 SmartGuard，直接返回 `doNotRetry=true` 的拒绝，要求 Agent 报告用户

#### Scenario: 只读不能重置熔断

- **WHEN** Agent 在两次被拒之间插入一次可信只读 Read
- **THEN** 累计计数保持不变，第三次拒绝照常触发熔断

#### Scenario: 换执行者不能绕过

- **WHEN** Team 新成员继续尝试已被多次拒绝的效果，使整轮 run 合计达到阈值
- **THEN** run 级熔断生效，该 Run 内所有 actor 的后续 SmartGuard 送审直接拒绝

#### Scenario: 人工决定清零

- **WHEN** 用户在复核卡中批准一次被模型拒绝的行动
- **THEN** 清除当前 run/actor 的拒绝计数，不影响历史审计和 run 级其他 actor 的计数

### Requirement: 不使用离线评测作为启用门禁

SmartGuard 是否可用 MUST 只由审核模型配置、保存探测、连接、经审定的 prompt 版本和运行时健康状态决定。系统 MUST NOT 要求 200 条数据、holdout、评测报告或质量阈值才能启用 `model_review`。本变更 MUST NOT 新增或保留运行时读取 `EvaluationDataset`、`EvaluationReport` 或评测报告存储的路径。

#### Scenario: 无评测报告也可启用

- **WHEN** 审核模型已通过探测且 SmartGuard prompt 已审定，但不存在离线评测报告
- **THEN** 用户可以选择 `model_review`，运行时按 SmartGuard 合同执行

#### Scenario: 评测旧记录不影响运行

- **WHEN** 租户数据库中存在历史评测 JSON 记录
- **THEN** 运行时不读取、不验证、不把它当作启用依据，审核结果只来自当前 SmartGuard

## REMOVED Requirements

### Requirement: 200 条人工评测与质量门禁

**Reason**：运行时安全守卫与离线模型质量评测被错误绑定；本次采用 Hermes 式窄守卫和失败保守收口，不再维护 200 条数据、holdout 和评测报告能力。

**Migration**：删除评测数据、评测 CLI、评测报告存储、相关测试和文档引用；保留普通运行时的延迟、token、故障和熔断观测，不把它们作为启用门槛。
