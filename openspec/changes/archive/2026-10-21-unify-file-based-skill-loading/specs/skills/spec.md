# Spec Delta

## MODIFIED Requirements

### Requirement: Skill expose share-with-all-agents switch

每个从文件目录解析的已安装 Skill MUST 暴露布尔字段 `availableToAllAgents`（缺省 `true`）。该字段 MUST 与「已安装 / 已启用」分开：停用或卸载 MUST 使该 Skill 不再进入任何 Runtime 可执行目录，即使开关仍为打开。启停/共享配置 MUST 关联稳定包身份并保存在独立文件配置中，不得由数据库登记行是否存在决定；编辑 `SKILL.md` MUST 不重置该配置。`yinli-catalog` MUST 视为对合格 Chat Runtime 锁定打开，客户端 MUST NOT 能把它关成无人可用。Skill 列表与详情 MUST 能读写该开关（锁定项除外），并使用与实际加载相同的文件解析结果。

#### Scenario: Default is shared with eligible agents

- **WHEN** 用户新安装或注册一个普通 Skill，未改「所有 Agent 可用」
- **THEN** `availableToAllAgents` 为 true，合格 Chat 目录包含该 Skill，不必写进 Agent `skills:`

#### Scenario: Toggle does not replace enable

- **WHEN** 一个 Skill 已打开「所有 Agent 可用」，随后被停用
- **THEN** 任何 Agent 的 Runtime 可执行目录都不再包含它，管理界面可以保留停用诊断

#### Scenario: Catalog lock

- **WHEN** 客户端试图把 `yinli-catalog` 的「所有 Agent 可用」设为 false
- **THEN** 请求失败或保持锁定打开，合格 Chat Runtime 目录仍包含该说明书

#### Scenario: Editing preserves switches

- **WHEN** 用户已关闭某受管包的启用与共享，随后修改其 `SKILL.md`
- **THEN** 稳定 ID 和开关不变，不能生成新 ID 并恢复默认启用

### Requirement: Shared skills join eligible chat catalogs without agent binding

当统一应用根中的文件包已安装、已启用、通过 Project binding 且 `availableToAllAgents` 为 true 时，系统 MUST 把它加入当前 Project 下合格 Runtime 的 Skill 目录，即使该 Agent profile 的 `skills:` 未列出它。合格 Runtime MUST 仅为：单 Agent Chat、Sandbox Chat、以及以 Chat Agent 为 Leader 的 Team 本轮。注入 MUST 只提供 Skill 工具与名称 / 描述 / 无歧义加载引用目录；合格 Chat Runtime MUST NOT 把全部 `SKILL.md` 预写入 system prompt。`role=job` 的 Job Agent 不属于该共享目录；Job 只有在其冻结的 required Skill 集合中明确声明时，才可在 scheduled firing Context 中预加载对应 `SKILL.md`。

#### Scenario: Shared skill appears for two chat agents

- **WHEN** `word-documents` 已启用并打开「所有 Agent 可用」，用户用未在 profile 列出该 Skill 的助手 A 与助手 B 分别发起单 Agent Chat
- **THEN** 两轮 Runtime 目录都包含 `word-documents`

#### Scenario: Closed switch still needs manual bind

- **WHEN** 同一 Skill 的「所有 Agent 可用」为 false，助手 A 的 `skills:` 未列出它
- **THEN** 该轮 Runtime 目录不包含它

#### Scenario: Bodies stay on demand

- **WHEN** 合格 Chat 因开关获得多个共享 Skill
- **THEN** 模型先只看到名称与描述及必要加载引用；完整说明书仍须按需读取，不得在开跑时整包写入 system prompt

#### Scenario: Job requires explicit frozen Skill

- **WHEN** 一个 Job 未冻结 `word-documents`，即使该 Skill 已对所有 Agent 可用
- **THEN** Job firing 不自动加载该 Skill；只有重新确认并把它纳入 required Skill 集合后才可预加载

#### Scenario: Unregistered managed package participates

- **WHEN** 统一应用根中的有效包已启用、共享并通过 Project binding，但没有数据库登记行
- **THEN** 它进入合格 Chat 目录，按需读取不因缺失登记行失败

### Requirement: Ineligible runtimes ignore the share switch

下列执行者 MUST NOT 因 `availableToAllAgents` 自动获得 Skill：`role=job` 的 Job Agent、群聊 Worker / Host、`include_default_tools=False` 的基础设施 Agent。定时任务冻结的 `skill_ids` 和 required Skill version records MUST 不因该开关被改写或追加。Team 隔离 Member session MUST NOT 仅因 Leader 看到共享目录就自动挂上同一套 Skill（Member 仍只使用自己被赋予的技能）。关闭开关 MUST 不删除 Agent profile 里已手动添加的同一 Skill。Job 已显式冻结的 Skill 则按 scheduled-job-skill-context 规则校验和预加载，但这不构成共享开关的自动授权。所有具备 Skill 能力的 Runtime MUST 使用同一文件解析与读取规则，其可用集合 MUST 继续分别按现有权限过滤。

#### Scenario: Job firing stays frozen

- **WHEN** 某 Skill 打开「所有 Agent 可用」，一个未冻结该 Skill 的 Job 到期执行
- **THEN** 该次 firing 的技能集合不含它

#### Scenario: Explicit Job Skill is independent of share switch

- **WHEN** 一个 Job 已冻结 `daily-report`，用户随后关闭该 Skill 的「所有 Agent 可用」开关，但 Skill 仍安装、启用且 Project binding 有效
- **THEN** Job 仍按冻结记录校验并加载 `daily-report`；共享开关本身不能删除或追加 Job 能力

#### Scenario: Group worker stays tool-only

- **WHEN** 群聊 Worker 轮到发言，且多个办公 Skill 已打开「所有 Agent 可用」
- **THEN** 该 Worker 仍只有群聊发言工具，Runtime 不挂这些 Skill

#### Scenario: Manual bind survives turning switch off

- **WHEN** 助手 A 的 `skills:` 已包含某 Skill，用户把该 Skill 的「所有 Agent 可用」关掉
- **THEN** 助手 A 的下一轮 Chat 目录仍包含它

#### Scenario: Explicit child reference uses the same package

- **WHEN** 一个允许使用技能的隔离子 Runtime 被显式授予一个有效 Skill 引用
- **THEN** 它使用与同 context 下其他入口相同的包身份与版本，同时不获得未被授予的其他共享技能
