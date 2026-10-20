# Spec Delta

## ADDED Requirements

### Requirement: Studio inherits only its owner Session

群聊工作室 MUST 归属其关联 Chat Session，并继承该 Session 的 tenant、内部 Session root、working directory、allowed roots 和资源 grant。房间、成员、消息、Run、Artifact 和 Worker 文件 MUST 通过 owner Session 查询与授权，不得再解析 Workspace、Project、Project root 或 Project binding。群聊仍使用独立 `/api/group-studio` 与独立 SSE，归属收敛不得把它并入 Chat SSE 或一次性 Team Runtime。

#### Scenario: Studio files stay under Session root
- **WHEN** Session S 创建群聊房间 G
- **THEN** G 的受管文件位于 S 的群聊子目录，外部 working directory 中不生成 `.agent-workstation/group-studios`

#### Scenario: Worker uses Session allowed roots
- **WHEN** Worker 在房间中调用文件工具
- **THEN** 工具范围是 owner Session allowed roots 与 Worker guardrails 的交集，不读取旧 Project root

#### Scenario: Session grant changes between waves
- **WHEN** 人类在两轮发言之间修改 owner Session 资源 grant
- **THEN** 下一波 Worker Run 冻结新 snapshot，已经开始的 Worker Run 保持旧 snapshot

#### Scenario: Each Worker turn freezes its own snapshot
- **WHEN** 同一波中两个 Worker 先后发言
- **THEN** 每次发言各自经过 Run 创建事务冻结一份 snapshot，Worker 的资源只来自该 snapshot，不再按 wave grant 或旧 Project binding 收窄

#### Scenario: Worker without a snapshot does not start
- **WHEN** Worker 发言时 snapshot 冻结失败，例如成员 Agent 已从全局 catalog 删除
- **THEN** 该 Worker 不调用模型，房间收到带稳定错误码的群聊错误事件，该成员移出当前发言队首，房间本身继续可用

#### Scenario: Cross-Session studio access is rejected
- **WHEN** 客户端使用 Session A 的上下文读取或控制 Session B 的群聊房间
- **THEN** 系统拒绝请求，即使两个 Session 曾属于同一个 Project

#### Scenario: Fresh tenant creates a studio without any Project
- **WHEN** 数据库中不存在任何 Workspace / Project 记录，用户在 Session S 中创建群聊房间
- **THEN** 房间创建成功，请求与响应都不需要 `projectId` / `workspaceId`，新写入的 Session 记录不包含旧字段值

