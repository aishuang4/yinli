# Spec Delta

## Purpose

规定 Skill 如何进入 Chat Runtime 目录：启用表示包可用，「所有 Agent 可用」开关决定要不要绕过单个 Agent 绑定；正文仍按需读取本地说明书。

## ADDED Requirements

### Requirement: Skill expose share-with-all-agents switch

每个已安装 Skill MUST 暴露布尔字段 `availableToAllAgents`（缺省 `true`）。该字段 MUST 与「已安装 / 已启用」分开：停用或卸载 MUST 使该 Skill 不再进入任何 Runtime 目录，即使开关仍为打开。`yinli-catalog` MUST 视为对合格 Chat Runtime 锁定打开，客户端 MUST NOT 能把它关成无人可用。Skill 列表与详情 MUST 能读写该开关（锁定项除外）。

#### Scenario: Default is shared with eligible agents

- **WHEN** 用户新安装或注册一个普通 Skill，未改「所有 Agent 可用」
- **THEN** `availableToAllAgents` 为 true，合格 Chat 目录包含该 Skill，不必写进 Agent `skills:`

#### Scenario: Toggle does not replace enable

- **WHEN** 一个 Skill 已打开「所有 Agent 可用」，随后被停用
- **THEN** 任何 Agent 的 Runtime 目录都不再包含它

#### Scenario: Catalog lock

- **WHEN** 客户端试图把 `yinli-catalog` 的「所有 Agent 可用」设为 false
- **THEN** 请求失败或保持锁定打开，合格 Chat Runtime 目录仍包含该说明书

### Requirement: Shared skills join eligible chat catalogs without agent binding

当 Skill 已安装、已启用且 `availableToAllAgents` 为 true 时，系统 MUST 把它加入当前 Project 下合格 Runtime 的 Skill 目录，即使该 Agent profile 的 `skills:` 未列出它。合格 Runtime MUST 仅为：单 Agent Chat、Sandbox Chat、以及以 Chat Agent 为 Leader 的 Team 本轮。注入 MUST 只提供 Skill 工具与名称 / 描述目录；MUST NOT 把全部 `SKILL.md` 预写入 system prompt。项目包仍按当前 Project 可见范围裁剪。

#### Scenario: Shared skill appears for two chat agents

- **WHEN** `word-documents` 已启用并打开「所有 Agent 可用」，用户用未在 profile 列出该 Skill 的助手 A 与助手 B 分别发起单 Agent Chat
- **THEN** 两轮 Runtime 目录都包含 `word-documents`

#### Scenario: Closed switch still needs manual bind

- **WHEN** 同一 Skill 的「所有 Agent 可用」为 false，助手 A 的 `skills:` 未列出它
- **THEN** 该轮 Runtime 目录不包含它

#### Scenario: Bodies stay on demand

- **WHEN** 合格 Chat 因开关获得多个共享 Skill
- **THEN** 模型先只看到名称与描述；完整说明书仍须按需读取，不得在开跑时整包写入 system prompt

### Requirement: Ineligible runtimes ignore the share switch

下列执行者 MUST NOT 因 `availableToAllAgents` 自动获得 Skill：`role=job` 的 Job Agent、群聊 Worker / Host、`include_default_tools=False` 的基础设施 Agent。定时任务冻结的 `skill_ids` MUST 不因该开关被改写或追加。Team 隔离 Member session MUST NOT 仅因 Leader 看到共享目录就自动挂上同一套 Skill（Member 仍只使用自己被赋予的技能）。关闭开关 MUST 不删除 Agent profile 里已手动添加的同一 Skill。

#### Scenario: Job firing stays frozen

- **WHEN** 某 Skill 打开「所有 Agent 可用」，一个未冻结该 Skill 的 Job 到期执行
- **THEN** 该次 firing 的技能集合不含它

#### Scenario: Group worker stays tool-only

- **WHEN** 群聊 Worker 轮到发言，且多个办公 Skill 已打开「所有 Agent 可用」
- **THEN** 该 Worker 仍只有群聊发言工具，Runtime 不挂这些 Skill

#### Scenario: Manual bind survives turning switch off

- **WHEN** 助手 A 的 `skills:` 已包含某 Skill，用户把该 Skill 的「所有 Agent 可用」关掉
- **THEN** 助手 A 的下一轮 Chat 目录仍包含它
