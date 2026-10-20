# Spec Delta

## Purpose

定义 Session 作为所有交互式和后台工作运行的唯一聚合根，统一消息、Context、文件、Artifact、Run、资源授权、群聊和 Job 的归属及恢复边界。

## ADDED Requirements

### Requirement: Every Runtime belongs to one Session

除显式 global catalog 维护操作外，每次 Chat、Team、Subagent、Plan、HITL、Sandbox、群聊 Worker 和 scheduled firing MUST 在启动前解析唯一 `SessionContext`。`SessionContext` MUST 至少包含 tenant、session ID、内部 session root、授权 working directory / allowed roots、资源 grant 版本和当前 Run ID。系统 MUST NOT 在缺少 Session 时回退到 default Workspace、default Project、进程 cwd 或安装目录。

#### Scenario: New conversation creates Session before first Run
- **WHEN** 用户在空白对话发送第一条消息
- **THEN** 系统先持久化 Session 和 SessionContext，再保存消息和创建 Run

#### Scenario: Missing Session fails before model execution
- **WHEN** Runtime 请求引用不存在或不属于当前 tenant 的 Session
- **THEN** 请求在创建 Agent 或调用模型前失败，不创建默认 Project 或临时共享上下文

#### Scenario: Fresh tenant sends the first message without any Project
- **WHEN** 全新桌面安装或新 hosted tenant 的数据库中不存在任何 Workspace / Project 记录，用户新建对话并发送第一条消息
- **THEN** Run 正常启动，`SessionContext` 只来自 Session 记录，系统不查询 Project，也不返回“Project 不存在”类错误

### Requirement: Session root owns internal runtime files

每个 Session MUST 有唯一 `<tenant-root>/sessions/<session-id>/`。上传副本、受管 Artifact、Run 文件、Plan、Team、Subagent、群聊、Job 和 Context 恢复状态 MUST 位于该 Session 根的明确子目录，或在数据库中以 `session_id` 作为强制归属。系统 MUST NOT 将内部状态写到外部 working directory 的 `.agent-workstation`、`.agents` 或其他隐藏目录。

#### Scenario: Session working on an external repository
- **WHEN** 用户授权 Session 操作 `/work/repository`
- **THEN** 内部消息附件、Run 状态和恢复文件仍写入 tenant Session root，外部仓库只出现用户明确创建或修改的业务文件

#### Scenario: Two Sessions use the same external directory
- **WHEN** 两个 Session 都授权同一个 working directory
- **THEN** 它们的内部状态、上传、Run 和 Artifact catalog 仍按 session ID 隔离

### Requirement: External directories are explicit Session grants

Session MAY 保存一个主 `workingDirectory` 和有限 `allowedRoots`。桌面端目录授权 MUST 来自用户选择或可信迁移；hosted 目录 MUST 位于当前 tenant 的允许根或容器挂载内。路径必须规范化、防符号链接越界，并与 Agent filesystem scope 取交集。修改 working directory 或 allowed roots MUST 产生新的 Session grant 版本，进行中的 Run 继续使用旧快照。

#### Scenario: User changes working directory between turns
- **WHEN** 用户在没有 active Run 时把 Session 工作目录从 A 改为 B
- **THEN** 下一 Run 使用新 grant，历史 Run 仍记录 A 且不能被重解释为在 B 执行

#### Scenario: Session grant overrides the former Project root
- **WHEN** 历史 Session 迁移前的 Project root 为 A，用户随后把工作目录授权改为 B
- **THEN** 下一 Run 的 filesystem scope 为 B 与 Agent guardrails 的交集，不再包含 A，也不再与 A 求交集

#### Scenario: Unavailable working directory fails closed
- **WHEN** Session 的工作目录在两次 Run 之间被删除或变得不可访问
- **THEN** 下一 Run 在写入 Run 记录和调用模型前以 `directory_unavailable` 失败，不回落旧 Project root、Session 内部目录、tenant root 或进程 cwd

#### Scenario: Session without a working directory uses Session artifacts
- **WHEN** Session 没有设置工作目录
- **THEN** Run 的默认工作目录是 `<session_root>/artifacts`，文件工具范围不包含任何旧 Project root

#### Scenario: Hosted Session requests host HOME
- **WHEN** hosted Session 尝试把宿主 HOME 设置为 allowed root
- **THEN** 系统拒绝授权且不挂载该目录

### Requirement: Session grants are mutable and Run snapshots are immutable

Session MUST 保存显式资源 grants，引用全局 Agent、Skill、MCP、HTTP Tool、知识库和权限配置的稳定 ID。每次 Run 开始时 MUST 校验全局资源仍存在且启用，并冻结不可变的资源、模型、授权目录和审批策略 snapshot。Session grant 的后续变化 MUST NOT 改写已开始或历史 Run；已删除或停用的全局资源 MUST 在新 Run fail closed。

#### Scenario: Resource removed after Run starts
- **WHEN** Run 已冻结 Skill A，随后用户从 Session grant 移除 A
- **THEN** 当前 Run 继续按冻结 snapshot 完成，下一 Run 不再获得 A

#### Scenario: Global resource disabled before next Run
- **WHEN** Session grant 仍引用 MCP B，但 B 在全局目录被停用
- **THEN** 下一 Run 在模型调用前报告资源不可用，不按同名切换到其他 MCP

#### Scenario: Run without a frozen snapshot does not start
- **WHEN** Chat、scheduled firing、群聊 Worker、Team 成员或 Subagent 的 Run 创建时未能写入 snapshot
- **THEN** 系统在创建 Agent 和调用模型前失败，不回退到旧 Project 范围、Project binding 或进程内默认值

#### Scenario: Fresh Session without grant rows keeps Agent resources
- **WHEN** 新建 Session 没有任何 grant 行，其 Agent 配置了已全局启用的 Skill、MCP 和 HTTP 工具
- **THEN** 首个 Run 的 snapshot 包含这些资源和全局共享 Skill，Chat 实际可用的工具与 snapshot 一致

#### Scenario: Snapshot failure leaves no Run behind
- **WHEN** Session 的 Agent 已从全局 catalog 删除，用户在该 Session 发送消息
- **THEN** 请求以 `resource_not_found` 失败，不留下 Run 记录、Run 目录或后台任务

#### Scenario: Tool execution recheck uses the Run snapshot
- **WHEN** Run 执行中触发工具授权复核，而用户刚刚修改了 Session grant
- **THEN** 复核使用该 Run 冻结的 snapshot 与资源当前的全局启用状态，不重新解析 Session grant，也不读取 Workspace / Project

#### Scenario: Session grant is authoritative over legacy binding
- **WHEN** Session grant 只启用 Skill B，而旧 `project_resource_bindings` 中只启用 Skill A
- **THEN** 下一 Run snapshot 只包含 B（仍受全局启用和 Agent 上限约束），旧 binding 不参与计算

### Requirement: Session is the query and ownership boundary

消息、Run、Trace、Plan、HITL、Team、Subagent、Skill 使用记录、timeline 原文、通知、群聊和 Artifact MUST 以 `session_id` 查询和授权。它们 MAY 冗余保存 tenant ID 供隔离和索引，但新记录 MUST NOT 依赖 Workspace / Project 列确定归属。跨 Session 汇总必须通过明确的全局查询入口，不得伪装成一个 Session 的本地数据。

#### Scenario: Chat history is not filtered by hidden Project
- **WHEN** 用户打开历史对话列表
- **THEN** 系统按当前 tenant 返回 Session，不要求先选择 Workspace 或 Project

#### Scenario: Trace request crosses Session ownership
- **WHEN** 客户端用 Session A 请求 Session B 的 Run trace
- **THEN** 系统拒绝请求，即使两者过去属于同一个 Project

### Requirement: Active dependents protect Session deletion

Session 归档 MUST 停止新 Run 但保留历史和可恢复数据。Session 存在 active Job、进行中 Run、待回应 HITL 或 active 群聊发言时，普通删除 MUST 返回可操作的冲突，不得留下无 owner 的后台任务。用户显式处理这些依赖后，删除 MUST 原子移除数据库归属、内部 Session root 和通知索引，但不得删除全局能力或外部 working directory 内容。

#### Scenario: Active Job blocks Session deletion
- **WHEN** 用户删除仍拥有 active scheduled Job 的 Session
- **THEN** 系统返回 `session_has_active_jobs` 或等价错误，并列出需要暂停或归档的 Job

#### Scenario: Deleting idle Session preserves external files
- **WHEN** Session 没有 active dependents 且 working directory 指向外部目录
- **THEN** 删除清理 tenant Session root 和数据库记录，不递归删除外部目录

### Requirement: Legacy Workspace and Project data migrates without guessing

迁移 MUST 为每个历史 Session 固化其原 Project root 为 working directory，并将有效 Project resource binding 转换为该 Session 的初始 grant。Project 专属 Agent/MCP/HTTP/Job 定义迁入全局 catalog 时，只有身份和内容唯一时才能复用 ID；同 ID 不同内容、同名多来源或越界路径 MUST 形成冲突报告并阻止对应 Session 自动启用。迁移必须可中断恢复，保留旧表和旧目录作为只读回滚证据，完成校验前不得删除来源。

#### Scenario: Historical Session keeps its former Project directory
- **WHEN** Session S 原属于 Project P 且 P root 为 `/work/a`
- **THEN** 迁移后的 S working directory 为 `/work/a`，其内部文件位于 tenant Session root

#### Scenario: Unmigrated historical Session is refused before cutover
- **WHEN** tenant 尚未完成迁移切换，某历史 Session 仍只有旧 Project 关联，没有工作目录、allowed roots 和 grant
- **THEN** 该 Session 的新 Run 以 `session_not_migrated` 失败，不按旧 Project 推测目录或资源；同一 tenant 的新 Session 正常运行

#### Scenario: Fresh tenant is cut over at startup
- **WHEN** 服务启动时 tenant 中没有任何 Workspace、Project 或旧关联数据
- **THEN** 启动门直接把该 tenant 标记为已切换并写入完成标记，不执行备份、回填或文件复制

#### Scenario: Two Workspace Agents conflict
- **WHEN** 两个旧 Workspace 存在相同 Agent ID 但正文不同，且各自有 Session 引用
- **THEN** 迁移不覆盖任一正文，报告冲突并要求显式映射，相关 Session 不静默切换 Agent

#### Scenario: File copy is interrupted
- **WHEN** 从外部 Project `.agent-workstation/sessions/S` 复制到 tenant Session root 时进程中断
- **THEN** 重跑从 staging/manifest 继续或重新校验，不产生两个已完成归属，也不删除源文件

