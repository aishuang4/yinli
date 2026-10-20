# Spec Delta

## MODIFIED Requirements

### Requirement: Job required Skills are explicitly frozen

系统 MUST 将 Job 的 required Skills 视为显式冻结的能力集合。Job 的能力库存、确认与恢复 MUST 使用和交互式入口相同的文件 Skill 解析规则，不以 SQLite 登记为冻结前提。冻结集合 MUST 至少记录稳定 Skill ID、来源/包定位、无歧义加载引用、`SKILL.md` 内容 hash 和冻结时间；系统 MUST 同时保存 `context_policy`，缺省为 `fresh`。`availableToAllAgents`、Agent profile 后续变更或新安装的共享 Skill MUST NOT 自动改写冻结集合。有显式 Skill 引用却无法读取有效包时 MUST 拒绝确认，不能以空 manifest 启用。

#### Scenario: Confirm freezes required Skill version

- **WHEN** 用户确认一个包含 Skill `daily-report` 的 Job
- **THEN** Job 保存该 Skill 的稳定 ID、来源和 `SKILL.md` hash，后续 firing 使用这份冻结记录

#### Scenario: Shared switch does not add a Skill

- **WHEN** Job 已确认且未包含 `daily-report`，之后用户把该 Skill 打开为所有 Agent 可用
- **THEN** Job 的冻结集合不变，下一次 firing 不加载 `daily-report`

#### Scenario: Contract and Job Skill declarations are reconciled

- **WHEN** capability contract 认领了一个 Skill，但旧 Job 行的 `skill_ids` 没有该 ID
- **THEN** firing 在装配前按确定性规则合并两者并经统一文件解析器校验，不能让可行性检查认领的 Skill 在执行时静默丢失

#### Scenario: Managed Skill has no database row

- **WHEN** Job 显式选择了统一应用目录中有效、启用、通过 Project binding 的 Skill，但没有 Skill 数据库登记行
- **THEN** 能力检查和确认都可解析该包，冻结结果包含其真实 hash，不要求数据库登记

#### Scenario: Explicit Skill cannot be frozen

- **WHEN** Job 声明了 Skill，但引用缺失、歧义或包不可读取
- **THEN** 确认返回可识别原因并保持未启用状态，不保存一个缺少必要冻结记录的 active Job

### Requirement: Scheduled firing validates frozen Skills before execution

每次 scheduled firing MUST 在创建 Agent Runtime 或调用模型前，通过统一文件解析与读取规则重新校验冻结 Skill：包必须仍安装、启用、来源可读、处于当前 Project 允许范围、可被该 Job Agent 使用，且当前 `SKILL.md` hash 必须与冻结 hash 一致。系统 MUST 按具体冻结身份与来源定位原包，不得因同名遮蔽、加载优先级变化或数据库缓存指向别的包。任一检查失败 MUST 使 firing 进入明确的失败或暂停结果，带有机器可识别错误码和可操作的人类原因；系统 MUST NOT 静默跳过该 Skill 或继续执行一个语义已改变的 Job。

#### Scenario: Skill content changed

- **WHEN** Job 到期前 `daily-report/SKILL.md` 内容被修改，导致 hash 不匹配
- **THEN** firing 在模型调用前失败或暂停并标记需要重新确认，稳定 ID 保持不变，且不执行原任务正文

#### Scenario: Skill is unbound from Project

- **WHEN** 冻结 Skill 仍安装但已从 Job 所属 Project 的资源白名单移除
- **THEN** firing 以 capability/resource unbound 错误结束，沿现有 scheduler notification 通道通知用户

#### Scenario: Frozen resource is deleted

- **WHEN** 冻结 Skill 被卸载或其 `SKILL.md` 无法读取
- **THEN** Skill 资源操作本身不扫描或暂停 Job；firing 校验时不降级运行，Job 按 failure policy 保持或暂停调度，并保留校验失败原因

#### Scenario: Project copy uses the same name

- **WHEN** Job 冻结后在项目 `.agents/skills` 新增了一个同名 Skill
- **THEN** 项目副本不进入 catalog，firing 继续校验统一应用根中明确冻结的包

### Requirement: Existing Jobs remain compatible and explicit

读取现有没有版本 hash 或 Context policy 的 Job 时，系统 MUST 使用兼容默认值（`fresh`、空 required Skill 版本集合）并在下一次编辑/重新确认时补齐冻结元数据。旧 Skill ID MUST 通过有唯一来源证据的兼容映射解析；既有冻结 hash MUST 不因身份迁移自动更新。存量空合同 Job MUST 继续按只生成/既有合同规则运行；迁移不得把当前 Agent 全量 Skills 自动升级为 Job required Skills。无法唯一确定旧引用时 MUST 保留记录并要求处理缺失/歧义，不能按同名替换。

#### Scenario: Legacy Job with no Skill metadata

- **WHEN** 系统加载一个只有旧 `skill_ids` 的 active Job
- **THEN** firing 仍只尝试这些显式 ID，按唯一兼容映射完成可读取/hash 建立或以明确的 metadata-missing 错误停止，不自动复制 Agent profile 的其他 Skills

#### Scenario: Reconfirmation refreshes version

- **WHEN** 用户编辑并重新确认一个存量 Job
- **THEN** 系统经统一文件解析器重新计算 required Skill hash 和 Context policy，并把已确认的新版本作为之后 firing 的冻结依据

#### Scenario: Identity migration preserves a frozen version

- **WHEN** 旧 external ID 可根据冻结来源路径唯一映射到新稳定 ID
- **THEN** Job 能继续识别同一个包，原冻结 hash 不变，当前内容变化仍会触发版本失败

#### Scenario: Legacy identity has multiple candidates

- **WHEN** 旧 ID 无法唯一映射到一个获准的现存包
- **THEN** 迁移保留诊断并使依赖 Job 暂停或待确认，不选任意同名包启动模型
