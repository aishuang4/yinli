# 统一资源权限系统设计

> 状态：第一阶段实施中。目标是在不破坏现有 Agent guardrails 的前提下，把工具、Skill、MCP、知识库、文件系统和运行审批统一到一套可审计的权限语义里。

## 背景

当前权限分散在三个位置：Agent guardrails 保存网络、人工审批、工具权限配置；运行时把这些字段映射到 AgentScope `PermissionMode`；工具注入阶段又单独判断 read-only、network_enabled。功能已经能用，但后续要支持多用户、Workspace、市场插件、Workflow 和更细粒度审批时会变得难维护。

## 资源与动作

统一资源类型：

- `agent`：Agent 配置、运行实例、子 Agent 调用。
- `team`：Team 配置、成员协作、Leader/Worker 调用。
- `tool`：本地工具，如 Read、Write、Edit、Bash、HttpRequest。
- `skill`：标准 Skill 包及其脚本、参考文档、资产。
- `mcp`：MCP Server 及其暴露的工具。
- `knowledge_tool`：HTTP 知识库检索工具。
- `workspace`：本地目录或 Docker 沙箱工作区。
- `run`：一次 Chat / Sandbox / Workflow 运行。

统一动作：

- `read`：读取配置、文件、知识、历史。
- `execute`：执行工具、MCP、Skill 脚本、子 Agent。
- `write`：写文件、写记忆、保存配置。
- `delete`：删除资源或清理文件。
- `configure`：修改 Agent、Team、权限、模型、工具绑定。
- `approve`：人工审批 HITL、外部执行或高风险操作。

## 决策模型

权限决策统一为四类：

- `allow`：自动放行。
- `deny`：直接拒绝。
- `ask`：进入 HITL / AgentScope 审批。
- `inherit`：继承 Agent、Team 或 Workspace 上级策略。

第一阶段保留四种工具审批策略：

- `read_only`：只注入只读工具，禁止写入、Shell、HTTP、MCP 执行。
- `trusted_tools`：信任已绑定工具，映射到 AgentScope `BYPASS`。
- `confirm_destructive`：读操作自动，写入/执行交给 AgentScope `ACCEPT_EDITS`。
- `ask_every_time`：每次工具调用都需要确认，映射到 AgentScope `DEFAULT`。

## 与现有 guardrails 的兼容

现有字段继续持久化在 `agents.guardrails` JSON 中：

- `filesystem_scope`
- `network_enabled`
- `human_approval`
- `tool_permission_mode`
- `tool_permission_profile`
- `shell_blacklist`
- `timeout_seconds`
- `execution_backend`
- `docker_image`
- `persistent_sandbox`

运行时通过 `UnifiedPermissionProfile` 归一化这些字段，再提供给 AgentScope permission mode、工具注入、缓存 key 和运行轨迹。后续如果增加权限表，可直接把该结构拆表。

## 审计与运行轨迹

后续每次权限判断应写入 `run_events`，至少包含：

- `run_id` / `session_id`
- `agent_id` / `team_id` / `worker_id`
- `resource_type` / `resource_id`
- `action`
- `decision`
- `policy`
- `reason`
- `approved_by`
- `created_at`

第一阶段先在 workspace trace 中暴露统一权限摘要，避免大范围改动事件链路。

## 阶段计划

1. 新增统一权限模型与 guardrails 兼容转换。
2. 运行时统一通过权限模型映射 AgentScope `PermissionMode`。
3. 工具注入统一使用权限模型判断 read-only 和 network。
4. 缓存 key 纳入统一权限摘要，避免权限修改后复用旧 Agent。
5. 补回归测试，覆盖 legacy mode、read-only、trusted、human approval。
6. 第二阶段增加权限审计事件和前端权限配置重构。
7. 第三阶段预留 Workspace / 用户 / 角色表，服务多用户与市场插件。

