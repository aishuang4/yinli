# Spec Delta

## MODIFIED Requirements

### Requirement: Skill expose share-with-all-agents switch

每个从用户全局目录解析的已安装 Skill MUST 暴露布尔字段 `availableToAllAgents`（缺省 `true`）。该字段 MUST 与「已安装 / 已启用」分开：停用或卸载 MUST 使该 Skill 不再进入任何新 Runtime 目录，即使开关仍为打开。配置 MUST 关联 tenant 内稳定 Skill ID 并保存在全局 Skill policy 中，不得按 Workspace、Project 或 Session 复制；编辑 `SKILL.md` MUST 不重置配置。`yinli-catalog` MUST 视为对合格 Chat Runtime 锁定打开，客户端 MUST NOT 能把它关成无人可用。Skill 列表与详情 MUST 能在没有 active Session 时读写该开关。

#### Scenario: Default is shared with eligible agents
- **WHEN** 用户新安装或注册一个普通 Skill，未改「所有 Agent 可用」
- **THEN** `availableToAllAgents` 为 true，合格 Session Runtime 目录包含该 Skill，不必写进 Agent `skills:`

#### Scenario: Toggle does not replace enable
- **WHEN** 一个 Skill 已打开「所有 Agent 可用」，随后被停用
- **THEN** 新 Run 不再获得它，进行中 Run 保留自己的不可变 snapshot

#### Scenario: Catalog lock
- **WHEN** 客户端试图把 `yinli-catalog` 的「所有 Agent 可用」设为 false
- **THEN** 请求失败或保持锁定打开，合格 Chat Runtime 目录仍包含该说明书

#### Scenario: Editing preserves global switches
- **WHEN** 用户关闭 Skill 的启用或共享后编辑同一包的 `SKILL.md`
- **THEN** Skill ID 和全局开关不变，所有 Session 在下一 Run 看到同一版本状态

### Requirement: Shared skills join eligible chat catalogs without agent binding

当 `<tenant-root>/skills` 中的 Skill 已安装、已启用且 `availableToAllAgents` 为 true 时，系统 MUST 把它加入合格 Session 的下一 Run 资源 snapshot，即使该 Agent profile 的 `skills:` 未列出它。合格 Runtime MUST 仅为：单 Agent Chat、Sandbox Chat、以及以 Chat Agent 为 Leader 的 Team 本轮。注入 MUST 只提供 Skill 工具与名称、描述和无歧义加载引用；合格 Runtime MUST NOT 把全部 `SKILL.md` 预写入 system prompt。Project、Workspace、Session 和 HOME 兼容目录不参与 Skill 包加载，也不再存在 Project binding 门禁。`role=job` 的 Job Agent 不属于该共享目录；Job 只有在其冻结 required Skill 集合中明确声明时才可预加载正文。

#### Scenario: Shared skill appears for two chat agents
- **WHEN** `word-documents` 已启用并打开「所有 Agent 可用」，两个 Session 分别选择未在 profile 列出它的助手
- **THEN** 两个 Session 的下一 Run 都记录并提供同一个全局 `word-documents` Skill

#### Scenario: Closed switch still needs manual bind
- **WHEN** 同一 Skill 的「所有 Agent 可用」为 false，Agent profile 和 Session pinned Skills 都未列出它
- **THEN** 该 Session 的下一 Run 不包含它

#### Scenario: Legacy Project binding does not gate Chat Skills
- **WHEN** `word-documents` 已启用并共享，Session 未撤销它，但旧 `project_resource_bindings` 中该 Skill 为关闭
- **THEN** 下一 Chat Run 仍包含它；旧 binding 表的任何变化都不影响 Chat Skill 集合

#### Scenario: Bodies stay on demand
- **WHEN** 合格 Session 因共享开关获得多个 Skill
- **THEN** 模型先只看到名称与描述；完整说明书仍须按需读取，不得在开跑时整包写入 system prompt

#### Scenario: Job requires explicit frozen Skill
- **WHEN** Job 未冻结 `word-documents`，即使该 Skill 已对所有 Agent 可用
- **THEN** Job firing 不自动加载该 Skill；只有重新确认并纳入 required Skill 集合后才可预加载

### Requirement: Ineligible runtimes ignore the share switch

下列执行者 MUST NOT 因 `availableToAllAgents` 自动获得 Skill：`role=job` 的 Job Agent、群聊 Worker / Host、`include_default_tools=False` 的基础设施 Agent。定时任务冻结的 `skill_ids` 和 required Skill version records MUST 不因该开关被改写或追加。Team 隔离 Member Runtime MUST NOT 仅因 Leader 看到共享目录就自动挂上同一套 Skill。关闭开关 MUST 不删除 Agent profile 或 Session pinned Skills 中的显式引用；引用仍须在下一 Run 通过全局安装、启用和 Runtime 资格校验。Job 已显式冻结的 Skill 按 scheduled-job-skill-context 规则校验和预加载，但这不构成共享开关自动授权。

#### Scenario: Job firing stays frozen
- **WHEN** 某 Skill 打开「所有 Agent 可用」，一个未冻结该 Skill 的 Job 到期执行
- **THEN** 该次 firing 的技能集合不含它

#### Scenario: Explicit Job Skill is independent of share switch
- **WHEN** Job 已冻结 `daily-report`，用户随后关闭该 Skill 的「所有 Agent 可用」但保持其安装启用
- **THEN** Job 仍按冻结记录校验并加载它；共享开关本身不能删除或追加 Job 能力

#### Scenario: Group worker stays tool-only
- **WHEN** 群聊 Worker 轮到发言，且多个办公 Skill 已打开「所有 Agent 可用」
- **THEN** Worker 仍只有群聊发言工具，不自动挂这些 Skill

#### Scenario: Manual bind survives turning switch off
- **WHEN** Agent profile 或 Session pinned Skills 已显式包含某 Skill，用户关闭共享开关
- **THEN** 下一 Run 仍可在该 Skill 安装启用且执行者合格时包含它

## ADDED Requirements

### Requirement: Skill viewer behavior is scoped to the runtime

Skill discovery and body loading MUST be runtime-scoped. Interactive Chat, Sandbox Chat, and eligible Chat Team Leader runtimes MAY expose the built-in `Skill` viewer and the ordinary instruction to load a Skill on demand. A scheduled Job firing MUST use preloaded-only semantics: required Skill bodies are injected before the first model call, the `Skill`/`SkillViewer` tool MUST be absent from the model-visible tool schema, and the ordinary on-demand instruction MUST NOT be included. Package paths MAY remain mounted for explicitly requested `references/` and `scripts/` access through existing tools.

#### Scenario: Scheduled Job does not expose the viewer

- **WHEN** a scheduled Job fires with a frozen required Skill
- **THEN** the model receives the `<activated-skill>` body before its first call and cannot call the `Skill` viewer

#### Scenario: Interactive Chat remains on demand

- **WHEN** an eligible interactive Chat receives a shared Skill
- **THEN** the model sees the Skill name/description and `Skill` viewer, and the body is loaded only when the model calls it

#### Scenario: Package mount does not grant permissions

- **WHEN** a scheduled Skill references a script or reference file
- **THEN** the model can use existing Read/Bash tools only if already allowed by the Run snapshot; the mount does not add tools, filesystem roots, network access, or approval bypass

