# Spec Delta

## Purpose

定义每个本地用户或 hosted tenant 的唯一全局能力目录，使 Agent、Skill、MCP、HTTP Tool、模型、长期记忆和系统设置不再被 Workspace、Project 或运行目录复制和分叉。

## ADDED Requirements

### Requirement: Tenant root is the only global authority

系统 MUST 为每个用户解析一个唯一 `<tenant-root>`；桌面端为 `~/.agent-workstation`，hosted 为该账号隔离的 tenant 数据目录。Agent、Skill、MCP、HTTP Tool、模型、长期用户记忆、Agent 专业区和系统设置 MUST 只以该根中的全局目录或对应全局记录为当前事实源。Workspace、Project、进程 cwd、外部 working directory、HOME 兼容目录和 Session 目录 MUST NOT 产生第二套全局能力定义。

#### Scenario: Desktop global definitions have one root
- **WHEN** 桌面用户注册 Agent、导入 Skill 或新增 MCP
- **THEN** 定义写入 `~/.agent-workstation` 下的对应全局目录，外部工作目录中不生成副本

#### Scenario: Compatibility definitions are ignored after migration
- **WHEN** 历史 Project `.agents` 或旧 Workspace catalog 中仍有能力文件
- **THEN** 正常目录与 Runtime 不读取这些文件，只允许迁移器将其作为带来源的旧数据证据

### Requirement: Global catalog is independent of Session selection

全局管理 API MUST 能在没有 active Session 的情况下列出、创建、编辑、启停和删除全局能力。新写 API 和持久化记录 MUST NOT 要求 `workspaceId`、`projectId` 或伪造的 default context。Session 只保存对全局能力稳定 ID 的授权或选择，不复制能力正文和密钥。

#### Scenario: Skill management works before a conversation exists
- **WHEN** 用户尚未创建任何 Session 就打开 Skills 管理页
- **THEN** 页面能管理全局 Skill，API 不创建默认 Workspace、Project 或隐藏 Session

#### Scenario: Session grant cannot create a missing resource
- **WHEN** Session grant 引用了已从全局目录删除的 MCP 或 Skill
- **THEN** 该资源在下一次 Run 校验时明确失败，grant 本身不能使资源重新出现

### Requirement: Global identities survive Session changes

全局能力的稳定 ID MUST 由 tenant 内的全局来源和定义位置决定，不得包含 Workspace、Project、Session 或 working directory。切换、归档、复制或删除 Session MUST NOT 改变 Agent、Skill、MCP 或 HTTP Tool 的身份、正文版本和全局启停状态。

#### Scenario: One Skill is reused by two Sessions
- **WHEN** 两个 Session 都允许使用同一个全局 Skill
- **THEN** 两者引用相同 Skill ID 和版本，不生成 Session 专属 Skill 副本

#### Scenario: Session deletion preserves global catalog
- **WHEN** 用户删除一个使用过多个 Agent 和 Skill 的 Session
- **THEN** 全局 Agent、Skill 和工具定义保持不变，仅删除或归档该 Session 的引用和运行数据

### Requirement: Hosted tenants remain physically isolated

移除用户可见 Workspace / Project MUST NOT 弱化 hosted 租户隔离。每个账号只能解析自己的 `<tenant-root>`，全局 catalog、Session 根、长期记忆、密钥引用和运行文件 MUST 位于该 tenant 边界内；control plane 的登录、额度和管理员记录 MUST 继续与用户数据目录分离。

#### Scenario: Hosted user cannot reference another tenant catalog
- **WHEN** hosted 请求提交另一个 tenant 的绝对路径、资源 ID 或 Session ID
- **THEN** 系统拒绝请求且不泄露对应名称、目录、正文或存在性

#### Scenario: Removing Workspace does not merge accounts
- **WHEN** 两个 hosted 用户都拥有名为 `report` 的 Skill 或 Agent
- **THEN** 两份定义仍位于各自 tenant root，并保持互不可见

### Requirement: Global secrets never enter Session files

模型 Key、MCP credential 和 HTTP Tool secret MUST 继续保存在全局密钥存储中。Session grant、Run snapshot、迁移报告、Artifact 和外部 working directory MUST 只保存 `secret_ref` 或已脱敏状态，不得复制明文密钥。

#### Scenario: Session snapshot freezes a configured MCP
- **WHEN** Run 冻结一个使用密钥的 MCP 能力
- **THEN** snapshot 只记录 MCP ID、定义版本和 secret reference，不含明文 token

