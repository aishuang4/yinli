# scheduled-job-skill-context Specification

## Purpose

为无人值守的 scheduled Job 提供可验证、可恢复且权限受控的 Skill Context 生命周期，使每次 firing 都能稳定获得确认时承诺的 `SKILL.md` 指令，同时避免跨周期对话无限膨胀或绕过现有资源边界。

## Requirements

### Requirement: Job required Skills are explicitly frozen

系统 MUST 将 Job 的 required Skills 视为显式冻结的能力集合。冻结集合 MUST 至少记录 Skill ID、来源/包定位、`SKILL.md` 内容 hash 和冻结时间；系统 MUST 同时保存 `context_policy`，缺省为 `fresh`。`availableToAllAgents`、Agent profile 后续变更或新安装的共享 Skill MUST NOT 自动改写冻结集合。

#### Scenario: Confirm freezes required Skill version

- **WHEN** 用户确认一个包含 Skill `daily-report` 的 Job
- **THEN** Job 保存该 Skill 的 ID、来源和 `SKILL.md` hash，后续 firing 使用这份冻结记录

#### Scenario: Shared switch does not add a Skill

- **WHEN** Job 已确认且未包含 `daily-report`，之后用户把该 Skill 打开为所有 Agent 可用
- **THEN** Job 的冻结集合不变，下一次 firing 不加载 `daily-report`

#### Scenario: Contract and Job Skill declarations are reconciled

- **WHEN** capability contract 认领了一个 Skill，但旧 Job 行的 `skill_ids` 没有该 ID
- **THEN** firing 在装配前按确定性规则合并两者并校验，不能让可行性检查认领的 Skill 在执行时静默丢失

### Requirement: Scheduled firing validates frozen Skills before execution

每次 scheduled firing MUST 在创建 Agent Runtime 或调用模型前，重新解析并校验冻结 Skill：Skill 必须仍安装、启用、来源可读、处于当前 Project 允许范围、可被该 Job Agent 使用，且当前 `SKILL.md` hash 必须与冻结 hash 一致。任一检查失败 MUST 使 firing 进入明确的失败或暂停结果，带有机器可识别错误码和可操作的人类原因；系统 MUST NOT 静默跳过该 Skill 或继续执行一个语义已改变的 Job。

#### Scenario: Skill content changed

- **WHEN** Job 到期前 `daily-report/SKILL.md` 内容被修改，导致 hash 不匹配
- **THEN** firing 在模型调用前失败或暂停并标记需要重新确认，且不执行原任务正文

#### Scenario: Skill is unbound from Project

- **WHEN** 冻结 Skill 仍安装但已从 Job 所属 Project 的资源白名单移除
- **THEN** firing 以 capability/resource unbound 错误结束，沿现有 scheduler notification 通道通知用户

#### Scenario: Frozen resource is deleted

- **WHEN** 冻结 Skill 被卸载或其 `SKILL.md` 无法读取
- **THEN** Skill 资源操作本身不扫描或暂停 Job；firing 校验时不降级运行，Job 按 failure policy 保持或暂停调度，并保留校验失败原因

### Requirement: Required Skill content is preloaded into the firing Context

校验通过后，系统 MUST 在 scheduled firing 的第一次模型调用前，将每个 required Skill 的 `SKILL.md` 正文以 `<activated-skill>` Context 内容加入本轮 AgentScope 会话。预加载内容 MUST 明确 Skill 名称、版本/hash 和执行规则，并 MUST 让模型知道正文已启用、无需再次调用同名 `Skill` 工具；正文 MUST 进入会话 Context，而非全局 system prompt。预加载不得改变 Skill 工具对 `references/`、示例文件和脚本的按需读取语义。

#### Scenario: First model call sees the frozen instructions

- **WHEN** 一个包含 `daily-report` 的 firing 通过全部校验并开始运行
- **THEN** 第一次模型调用前的 Context 已包含 `daily-report` 的完整（或预算规则允许的明确表示）`SKILL.md` 正文及其 hash

#### Scenario: Preload is not a system prompt mutation

- **WHEN** 同一 Job 连续触发多个 firing
- **THEN** Skill 正文通过 firing/session Context 注入和恢复机制提供，基础 system prompt 仍可复用，不能把每份 `SKILL.md` 永久拼入 system prompt

#### Scenario: Skill package remains on demand

- **WHEN** `SKILL.md` 引用了 `references/guide.md` 或 `scripts/run.py`
- **THEN** Context 只预加载 `SKILL.md`；模型仍须通过受限的 Read/Bash 等现有工具按需访问引用文件，不能因预加载获得额外权限

### Requirement: Job Context policy prevents cross-firing history growth

`fresh` policy MUST 为每次 firing 创建新的会话 Context，不得继承上一 firing 的完整 user/assistant/tool 历史。若系统支持其他 policy，必须显式声明并限制为上一 firing 的最终结果或有界滚动摘要；不得隐式继承完整历史，也不得把历史摘要伪装为新的 Skill 正文。Context policy 变更属于 Job 配置变更，必须重新确认后生效。

#### Scenario: Daily firing starts fresh

- **WHEN** 一个 daily Job 连续运行第二天
- **THEN** 第二次 firing 只获得 Job prompt、冻结 Skill Context、当前输入和允许的运行上下文，不获得第一天的完整工具记录

#### Scenario: Previous result is bounded

- **WHEN** Job 明确配置 `previous_result`
- **THEN** 下一次 firing 最多获得上一 firing 的最终结果（按长度上限裁剪）及元数据，不获得其完整对话或工具输出

### Requirement: Context budgets fail closed

系统 MUST 对单个 Skill 正文、全部预加载 Skill 和模型 Context 保留区设置可观测的预算。超过预算时 MUST 返回可区分的 `skill_context_budget_exceeded` 或等价错误，并按 Job failure policy 处理；系统不得静默截断成未标记的正文、假装成功或删除 required Skill 后继续运行。预算计算 MUST 与当前 Runtime Context Governance 使用同一 token 计数来源。

#### Scenario: One Skill exceeds its limit

- **WHEN** 单个 `SKILL.md` 超过 Job 允许的单 Skill 上限
- **THEN** firing 在模型调用前失败并指出需要拆分/缩短 Skill，required Skill 不被静默截断

#### Scenario: Combined Skills exceed total budget

- **WHEN** 多个 required Skills 的正文总量超过 Context 预算
- **THEN** firing 失败或要求重新配置预算，审计记录包含每个 Skill 的大小和总预算使用量

### Requirement: Preloading preserves all Job security and tool boundaries

Skill Context 预加载 MUST NOT 扩大 Job Agent 的工具集合、filesystem scope、Project resource scope、网络/MCP/知识库权限、trusted tool 判定或无人值守 denylist。scheduled firing MUST NOT 因预加载注入 `AskUser`、Plan、DelegateTask、ScheduleJob 或其他交互式 Job 管理工具；Skill 正文中的指令不能覆盖这些运行时边界。

#### Scenario: Script Skill still needs Bash permission

- **WHEN** 冻结 Skill 的说明要求执行 `scripts/build.py`，但 Job tool scope 未允许 Bash
- **THEN** Skill 正文可以被预加载，但执行脚本仍被拒绝并记录权限原因

#### Scenario: Unattended denylist remains effective

- **WHEN** 预加载 Skill 正文要求调用被 scheduled unattended denylist 禁止的工具
- **THEN** 工具调用被现有 denylist 拦截，firing 不因 Skill 正文而获得例外

### Requirement: Skill Context lifecycle is observable and recoverable

系统 MUST 在 firing audit/trace 中记录 required Skill 集合、每个 hash、校验结果、Context policy、预加载大小/预算、失败错误码和最终 firing 状态。校验失败、预算失败和恢复失败 MUST 进入现有 Job failure policy 与通知流程。Context 压缩、实例重建或进程重启后，若 firing 仍可恢复，系统 MUST 从持久化的 firing/job 记录恢复冻结 Skill Context；恢复不了时 MUST 失败并说明原因。

#### Scenario: Successful preload is auditable

- **WHEN** firing 成功完成
- **THEN** 审计/trace 能查询本次实际使用的 Skill ID、hash、policy 和 Context 预算结果

#### Scenario: Runtime restart does not silently lose required Skills

- **WHEN** firing 在 Context 压缩或 Agent 实例重建后继续运行
- **THEN** required Skill 正文按已记录的冻结版本重新进入 Context；无法恢复时 firing 失败而不是继续无 Skill 执行

### Requirement: Existing Jobs remain compatible and explicit

读取现有没有版本 hash 或 Context policy 的 Job 时，系统 MUST 使用兼容默认值（`fresh`、空 required Skill 版本集合）并在下一次编辑/重新确认时补齐冻结元数据。存量空合同 Job MUST 继续按只生成/既有合同规则运行；迁移不得把当前 Agent 全量 Skills 自动升级为 Job required Skills。

#### Scenario: Legacy Job with no Skill metadata

- **WHEN** 系统加载一个只有旧 `skill_ids` 的 active Job
- **THEN** firing 仍只尝试这些显式 ID，先完成可读取/hash 建立或以明确的 metadata-missing 错误停止，不自动复制 Agent profile 的其他 Skills

#### Scenario: Reconfirmation refreshes version

- **WHEN** 用户编辑并重新确认一个存量 Job
- **THEN** 系统重新计算 required Skill hash 和 Context policy，并把新版本作为之后 firing 的冻结依据
