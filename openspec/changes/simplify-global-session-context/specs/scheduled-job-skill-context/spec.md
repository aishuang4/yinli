# Spec Delta

## ADDED Requirements

### Requirement: Scheduled Job has one persistent owner Session

每个 scheduled Job MUST 归属一个当前 tenant 内的持久 owner Session。Job 定义、确认记录、firing、通知和输出 Artifact MUST 通过该 Session 授权；每次 firing MUST 是 owner Session 下独立的新 Run。active Job MUST 阻止 owner Session 普通删除，系统不得创建无 Session 所有者的 Job。

#### Scenario: Job created from Chat keeps the owner
- **WHEN** 用户在 Session S 中创建并确认 Job J
- **THEN** J 保存 ownerSessionId=S，所有 firing Run 和输出都可从 S 查询

#### Scenario: Legacy Job without a source Session
- **WHEN** 迁移发现一个有效旧 Job 没有可用 `created_from_session_id`
- **THEN** 系统为该 Job 建立明确标识的持久 Session，并保留旧 Project/Workspace 来源证据

#### Scenario: Fresh tenant enables and fires a Job without any Project
- **WHEN** 数据库中不存在任何 Workspace / Project 记录，用户在 Session S 中确认并启用 Job J
- **THEN** 启用与 firing 只校验 owner Session grant 和全局 catalog，J 正常完成一次 firing，不因 Project 缺失而失败

#### Scenario: Legacy Workspace fields do not affect firing
- **WHEN** 旧 Job 记录的 `workspaceId` / `projectId` 与 owner Session 记录中的旧字段不一致，或对应 Project 已删除
- **THEN** firing 仍只按 owner Session 构建上下文和资源，不因旧字段不一致而失败，也不读取旧 Project

## MODIFIED Requirements

### Requirement: Job required Skills are explicitly frozen

系统 MUST 将 Job 的 required Skills 视为显式冻结的能力集合。冻结集合 MUST 至少记录全局 Skill ID、来源/包定位、`SKILL.md` 内容 hash 和冻结时间；系统 MUST 同时保存 `context_policy`，缺省为 `fresh`，并记录 owner Session grant 版本。`availableToAllAgents`、Agent profile、Session grant 后续变更或新安装的共享 Skill MUST NOT 自动改写冻结集合。

#### Scenario: Confirm freezes required Skill version
- **WHEN** 用户在 owner Session 中确认包含 `daily-report` 的 Job
- **THEN** Job 保存全局 Skill ID、来源、hash 和 Session grant 版本，后续 firing 使用该记录

#### Scenario: Shared switch does not add a Skill
- **WHEN** Job 已确认且未包含 `daily-report`，之后用户把该 Skill打开为所有 Agent 可用
- **THEN** Job 冻结集合不变，下一次 firing 不加载该 Skill

#### Scenario: Contract and Job Skill declarations are reconciled
- **WHEN** capability contract 认领 Skill，但旧 Job `skill_ids` 没有该 ID
- **THEN** firing 装配前按确定性规则合并两者并通过全局目录校验，不静默丢失合同能力

### Requirement: Scheduled firing validates frozen Skills before execution

每次 scheduled firing MUST 在创建 Agent Runtime 或调用模型前，重新解析并校验冻结 Skill：Skill 必须仍位于 owner tenant 全局目录、安装、启用、来源可读、被 Job Agent 显式允许，且当前 `SKILL.md` hash 必须与冻结 hash 一致。系统 MUST 复核 owner Session 仍有效且该 Job 的授权目录/资源合同没有被撤销；不再读取 Project binding。任一检查失败 MUST 使 firing 进入明确失败或暂停结果，并带机器错误码与可操作原因，不能静默跳过或按同名换包。

#### Scenario: Skill content changed
- **WHEN** Job 到期前 `daily-report/SKILL.md` hash 改变
- **THEN** firing 在模型调用前失败或暂停并要求重新确认

#### Scenario: Skill is unbound from Project
- **WHEN** 旧 Project Job 完成迁移后，owner Session 撤销了该 Job 所需 Skill 或其他资源授权
- **THEN** firing 以 capability/resource unbound 结束并走通知，不回退到旧 Project binding

#### Scenario: Frozen resource is deleted
- **WHEN** 冻结 Skill 被卸载或无法读取
- **THEN** Skill 操作本身不扫描 Job；firing 校验时 fail closed，并按 failure policy 处理

### Requirement: Job Context policy prevents cross-firing history growth

`fresh` policy MUST 为 owner Session 中的每次 firing 创建独立 Run Context，不继承 owner Chat 或上一 firing 的完整 user/assistant/tool 历史。若系统支持其他 policy，必须显式声明并限制为 owner Session 中上一 firing 的最终结果或有界摘要；不得隐式继承完整历史，也不得把历史摘要伪装成新的 Skill 正文。Context policy 变更属于 Job 配置变更，必须重新确认。

#### Scenario: Daily firing starts fresh
- **WHEN** daily Job 在同一 owner Session 中连续运行第二天
- **THEN** 第二次 firing 只获得 Job prompt、冻结 Skill、Run snapshot 和允许的有界输入

#### Scenario: Previous result is bounded
- **WHEN** Job 明确配置 `previous_result`
- **THEN** 下一 firing 最多获得上一 firing 最终结果及元数据，不获得完整工具历史

### Requirement: Preloading preserves all Job security and tool boundaries

Skill Context 预加载 MUST NOT 扩大 Job Agent 工具集合、owner Session allowed roots、Run 资源 snapshot、网络/MCP/知识库权限、trusted tool 判定或无人值守 denylist。scheduled firing MUST NOT 因预加载注入 `AskUser`、Plan、DelegateTask、ScheduleJob 或其他交互式 Job 管理工具；Skill 正文不能覆盖这些边界。

#### Scenario: Script Skill still needs Bash permission
- **WHEN** 冻结 Skill 要求执行脚本，但 Job tool scope 未允许 Bash
- **THEN** 正文可以预加载，脚本执行仍被拒绝并记录原因

#### Scenario: Working directory stays within Session grant
- **WHEN** Skill 指示 Job 写入 owner Session allowed roots 之外的路径
- **THEN** 文件工具拒绝操作，不能借历史 Project root 或进程 cwd 扩大范围

#### Scenario: Unattended denylist remains effective
- **WHEN** 预加载 Skill 正文要求调用 scheduled unattended denylist 禁止的工具
- **THEN** 工具调用继续被 denylist 拒绝，firing 不因 Session 归属改变获得例外

### Requirement: Skill Context lifecycle is observable and recoverable

系统 MUST 在 firing audit/trace 中记录 owner Session ID、firing Run ID、required Skill 集合、每个 hash、Session grant 版本、Context policy、预算、失败错误码和最终状态。校验失败、预算失败和恢复失败 MUST 进入现有 failure policy 与通知。Context 压缩、实例重建或进程重启后，若 firing 可恢复，系统 MUST 从持久化的 owner Session、Run 和 Job 记录恢复冻结 Skill Context；恢复不了时 MUST 失败并说明原因。

#### Scenario: Successful preload is auditable
- **WHEN** firing 成功完成
- **THEN** 从 owner Session 能查询本次实际使用的 Skill、hash、grant snapshot、policy 和预算结果

#### Scenario: Runtime restart does not silently lose required Skills
- **WHEN** firing 在 Context 压缩或实例重建后继续
- **THEN** required Skill 按已记录版本恢复；无法恢复则失败

### Requirement: Existing Jobs remain compatible and explicit

迁移现有没有版本 hash、Context policy 或 owner Session 的 Job 时，系统 MUST 使用兼容默认值并建立明确 owner Session；只有唯一来源证据时才能映射全局资源 ID和工作目录。存量空合同 Job MUST 继续按既有合同规则运行；迁移不得复制 Agent 全量 Skills，不得把旧 Project 全部资源自动扩大为 Job required 集合。下一次编辑/重新确认 MUST 补齐 owner Session grant snapshot 与冻结元数据。

#### Scenario: Legacy Job with no Skill metadata
- **WHEN** 系统加载只有旧 `skill_ids` 的 active Job
- **THEN** firing 只尝试显式 ID，先建立可验证 hash 或以 metadata-missing 停止

#### Scenario: Reconfirmation refreshes version
- **WHEN** 用户编辑并重新确认存量 Job
- **THEN** 系统重新计算 required Skill、owner Session grant snapshot 和 Context policy

#### Scenario: Legacy Project Job files stay recoverable
- **WHEN** Job 定义原位于外部 Project `.agents/jobs` 或 `.agent-workstation/jobs`
- **THEN** 迁移验证 Session 内副本后才停止读取旧位置，并保留来源清单用于回滚

## ADDED Requirements

### Requirement: Scheduled firing uses a preloaded-only Skill tool surface

scheduled firing MUST use a runtime-specific Skill tool surface. When at least one required Skill is frozen, the model-visible tool schema MUST NOT contain the built-in `Skill`/`SkillViewer` tool, and the runtime MUST NOT include the ordinary Chat instruction that tells the model to discover and call Skill. The required Skill body MUST already be present in `<activated-skill>` Context before the first model call. `skill_paths` or equivalent package mounts MAY remain available solely for resolving and reading `references/` and `scripts/` through existing tools. A denylist alone is not sufficient to satisfy this requirement because the viewer is a built-in tool and MUST be excluded during toolkit construction.

#### Scenario: Scheduled schema does not expose Skill viewer

- **WHEN** a firing with frozen required Skill `daily-report` constructs its Agent toolkit
- **THEN** the model-visible tool schema has no `Skill` or `SkillViewer` entry, while the Context already contains the `daily-report` `<activated-skill>` block

#### Scenario: Interactive Chat keeps on-demand Skill loading

- **WHEN** an ordinary interactive Chat constructs its Agent toolkit with shared Skill `daily-report`
- **THEN** the model sees the ordinary `Skill` tool and may read the body on demand; this behavior is not changed by scheduled Job isolation

#### Scenario: Scheduled references remain on demand

- **WHEN** the preloaded `SKILL.md` refers to `references/guide.md` or `scripts/run.py`
- **THEN** the model uses the existing Read/Bash tools and the mounted Skill path to access them, without a Skill viewer call and without receiving additional permissions

### Requirement: Job Skill registration tools expose frozen-preload semantics

The scheduled Job registration tool contract MUST make the Skill lifecycle explicit. `ScheduleJob.skill_ids` MUST represent the user-selected stable Skill IDs (the runtime catalog MUST expose those IDs alongside names and descriptions); `required_skills` MUST be server-generated confirmation metadata and MUST NOT be model-authoritative input. `ConfirmJob` MUST report that it froze each selected `SKILL.md` version and that the next firing will preload it before the first model call. No scheduled tool description or response MAY instruct the model to call `Skill` to activate a required Job Skill.

#### Scenario: Model registers a fixed Skill by stable ID

- **WHEN** the user asks for a daily Job to use `daily-report`
- **THEN** the model passes the catalog's stable `skill_id` to `ScheduleJob`, and the service generates `required_skills` during `ConfirmJob` rather than trusting a model-supplied manifest

#### Scenario: Confirm response explains preload

- **WHEN** `ConfirmJob` successfully enables a Job with `daily-report`
- **THEN** its result includes the frozen Skill ID/hash and states that the body is injected before the first scheduled model call

#### Scenario: Legacy manifest input cannot change the snapshot

- **WHEN** an old client or model supplies `required_skills` that differs from the live catalog during Job creation or update
- **THEN** the service ignores it as authoritative or rejects it with a validation error; only confirm-time server resolution can create or replace the frozen manifest

#### Scenario: Ordinary Skill wording cannot leak into scheduled firing

- **WHEN** a scheduled firing builds its system/tool instructions
- **THEN** the prompt contains the preloaded-only rule and does not contain the ordinary Chat wording "call the Skill tool" for the required Skill

