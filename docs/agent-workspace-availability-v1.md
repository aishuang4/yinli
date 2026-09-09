# Agent Workspace 可用性与 Project 解耦方案 v1

> 状态：已实施 v1
> 
> 目标：面向本地个人工作台，取消 Agent 必须绑定 Project 才能使用的隐式前置条件。

## 1. 背景

当前 Agent 记录属于 Workspace，但 Chat 和群聊在运行前还会检查 `project_resource_bindings` 中是否存在对应的 Agent 绑定。创建 Agent 后，用户没有统一的绑定管理入口，因此会出现“Agent 已创建，但群聊成员管理中看不到”的情况。

现有链路如下：

```text
创建 Agent
  -> 保存 Workspace Agent
  -> 绑定 Project Agent 资源
  -> Chat / 群聊按 Project 绑定过滤
```

该模型更适合多用户、多项目的企业权限场景，不适合当前以本地个人使用为主的工作台。

## 2. 目标与非目标

### 2.1 目标

- Workspace 内创建的普通 Agent 默认可被该 Workspace 下的所有 Project 使用。
- 手动创建和智能创建的 Agent 在 Chat、群聊中的可用规则一致。
- 群聊成员候选列表由后端统一查询，前端不再拼接 Agent 列表和 Project 绑定缓存。
- 保留现有 Project 资源绑定机制，避免影响 MCP、Skill、HTTP Tool 和知识库工具。
- 兼容已有 `project_resource_bindings` 数据，不要求破坏性迁移。

### 2.2 非目标

- 本版本不删除 `project_resource_bindings` 表。
- 本版本不改变 Job Agent 的定时任务权限模型。
- 本版本不引入新的企业级 Agent 权限管理界面。
- 本版本不改变 Agent 的 Workspace 归属和跨 Workspace 隔离。

## 3. 核心规则

### 3.1 普通 Agent 可用性

普通 Agent 是否可用于 Chat 或群聊，只检查：

1. Agent 属于当前 Workspace，或是系统默认 Agent；
2. Agent 的 `role` 不是 `job`；
3. Agent 不是 `group-studio-host`；
4. 对群聊而言，Agent 尚未成为当前群成员。

普通 Agent 不再要求存在以下绑定：

```text
project_resource_bindings
  resource_type = agent
  resource_id = agent.id
  enabled = true
```

### 3.2 Project 资源绑定

Project 绑定继续用于以下资源：

- MCP Server；
- Skill；
- HTTP Tool；
- 知识库工具及其他显式项目资源。

已有 Agent 绑定记录继续保留，但不再作为 Agent 可用性的必要条件。未来若需要企业权限，可再增加显式的 Workspace 策略开关，不在本版本隐式恢复强绑定。

### 3.3 创建规则

手动创建和智能创建最终都调用统一的 Agent 持久化逻辑，创建出的 Agent 具有相同的 Workspace 归属和 `role=chat` 默认语义。

- 手动创建：不再把 Project 绑定作为创建成功后的必要步骤；
- 智能创建：`projectId` 可以继续作为创建上下文传递，但不决定 Agent 是否可用；
- 创建成功后，Agent 立即可在当前 Workspace 的 Chat 和群聊中使用。

## 4. 后端改动

### 4.1 Agent 资源查询

增加或统一 Workspace 范围的普通 Agent 查询函数，返回当前 Workspace 可用的 Agent。该函数应复用现有 Agent catalog，不读取 Project Agent 绑定作为硬过滤条件。

### 4.2 群聊候选接口

新增群聊领域接口：

```http
GET /api/group-studio/{group_id}/available-agents
```

返回：

```json
[
  {
    "id": "agent-id",
    "name": "Agent 名称",
    "description": "Agent 描述"
  }
]
```

接口在后端完成以下过滤：

- 当前 Workspace 外的 Agent；
- `role=job`；
- `group-studio-host`；
- 已经在当前群聊且状态不是 `removed` 的 Agent。

现有 `list_invitable_agents(group_id)` 作为该接口的业务实现，继续作为 `add_member()` 的最终校验依据。

### 4.3 群聊添加成员

`POST /api/group-studio/{group_id}/members` 保留后端最终校验，但将校验从“必须绑定当前 Project”改为“必须属于当前 Workspace 且为普通 Agent”。

群聊状态限制、人数上限、重复成员、Host 和 Job Agent 限制保持不变。

### 4.4 Chat 和其他运行入口

Chat、Sandbox、后台 Agent 运行时如当前仅因 Project Agent 绑定而拒绝普通 Agent，应改为使用 Workspace Agent 可用性判断。MCP、Skill、HTTP Tool 等工具仍走现有 Project 白名单。

## 5. 前端改动

### 5.1 群聊成员管理

成员管理弹窗打开时调用群聊候选接口，不再使用以下组合逻辑：

```text
useAgents()
+ useProjectResources()
+ studio.members
+ 前端本地 filter
```

前端只负责：

- 展示后端返回的候选 Agent；
- 显示加载和错误状态；
- 提交 `agentId`；
- 添加成功后刷新群聊快照。

### 5.2 Agent 管理页面

不再将“绑定 Project”作为创建 Agent 的用户必填步骤。可以展示 Agent 所属 Workspace，但暂不要求用户维护 Project 关系。

### 5.3 刷新策略

智能创建成功后，刷新 Workspace Agent 列表。进入群聊成员管理时直接重新请求 `available-agents`，避免依赖前端旧缓存。

## 6. 数据兼容与迁移

- 不删除 `project_resource_bindings` 表和已有记录；
- 不批量删除历史 Agent 绑定；
- 不要求为历史 Agent 补绑定；
- 新旧数据库均按新的 Workspace Agent 可用性规则运行；
- 旧绑定记录继续供其他资源类型使用；
- 如后续需要回滚，可恢复 Agent 绑定过滤逻辑，不影响 Agent 主表数据。

## 7. 测试计划

### 7.1 后端

- 当前 Workspace 的普通 Agent 可以出现在候选列表；
- 没有 Project Agent 绑定的普通 Agent 仍可以出现在候选列表；
- 其他 Workspace 的 Agent 不出现；
- Job Agent 不出现；
- `group-studio-host` 不出现；
- 已加入当前群聊的 Agent 不重复出现；
- `add_member()` 对不属于当前 Workspace 的 Agent 拒绝；
- 智能创建 Agent 不传或传入不同 Project 时，仍可在当前 Workspace 使用。

### 7.2 前端

- 智能创建成功后，Agent 管理列表可见；
- 打开群聊成员管理后，智能创建 Agent 可见；
- Agent 列表刷新失败时显示明确错误，不显示“暂无成员”；
- 添加成功后候选列表排除该 Agent；
- 群聊结项、归档和失败状态仍不可添加成员。

### 7.3 回归验证

- Chat 单 Agent 运行；
- 群聊添加、移除和重新添加成员；
- Project 对 MCP、Skill 和 HTTP Tool 的绑定限制；
- Job Agent 创建和定时任务执行；
- Electron 开发模式和生产构建。

## 8. 实施顺序

1. 先补后端 Workspace Agent 可用性函数和群聊 `available-agents` 接口。
2. 将 `list_invitable_agents()` 改为使用 Workspace 规则，并保留添加成员的最终校验。
3. 前端成员管理改为调用新接口，移除本地 Project Agent 绑定过滤。
4. 检查 Chat、Sandbox 和其他 Agent 运行入口中的同类 Project Agent 强校验。
5. 补充后端和前端回归测试。
6. 运行构建、群聊测试和关键页面手动验证。

## 9. 风险与控制

主要风险是某些历史流程可能错误地把 Project Agent 绑定当作权限边界。实施时需要全局搜索 `resource_type=agent`、`require_agent()` 和 Agent Project binding 的调用点，逐一确认：

- 普通 Agent 使用不再依赖绑定；
- MCP、Skill、HTTP Tool 仍依赖绑定；
- Job Agent 仍走独立调度权限；
- Workspace 边界不能被绕过。

该方案只改变普通 Agent 的默认可用范围，不改变 Workspace 隔离和其他项目资源的权限控制。
