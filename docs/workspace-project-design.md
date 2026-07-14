# Workspace 与 Project 设计

> 状态：待实现设计，2026-07-14。本文定义平台归属模型，不代表当前代码已经支持多 Workspace / Project。

## 目标

当前系统已经具备 Agent、TeamRun、Subagent、Memory、Run 和 Trace，但多数资源仍接近全局作用域。Workspace / Project 的目标是建立统一归属和隔离边界，而不是只增加两个前端下拉框。

```text
Workspace = 资源、权限、Secret 和策略的隔离边界
Project   = 一项实际工作的目录、上下文和数据边界
Session   = Project 内的一段对话
Run       = Session 中的一次可恢复执行
```

## 领域关系

```text
Workspace
  -> Projects
  -> Agents
  -> Skills
  -> Subagent definitions
  -> MCP / HTTP tools / Secrets
  -> Workspace policy

Project
  -> resource bindings
  -> project root / instructions
  -> Chat sessions / Runs / TeamRuns
  -> Memory / Artifacts / Trace
```

Agent、Skill、MCP 和 Subagent definition 属于 Workspace。Project 通过 binding 启用这些资源，避免把同一 Agent 复制到多个项目。

## 核心数据

第一阶段新增：

```text
workspaces
  id, name, description, status, created_at, updated_at

projects
  id, workspace_id, name, description, root_path,
  instructions_file, status, created_at, updated_at

project_resource_bindings
  project_id, resource_type, resource_id, enabled,
  config_override, created_at, updated_at
```

以下记录必须增加 `workspace_id` / `project_id`：

- `chat_sessions`
- `chat_runs`
- `run_events`
- `memory_nodes`
- `subagent_invocations`
- Artifact 记录

Agent、Skill、MCP、HTTP Tool 只增加 `workspace_id`，Project 使用 binding 关联。

## RuntimeContext

所有运行在创建 Agent 之前解析统一上下文：

```python
@dataclass(frozen=True)
class RuntimeContext:
    workspace_id: str
    project_id: str
    project_root: Path
    session_id: str
    run_id: str
    agent_id: str
```

执行顺序：

```text
Chat request
  -> resolve Project
  -> validate Workspace ownership
  -> build RuntimeContext
  -> resolve project resource bindings
  -> resolve permission intersection
  -> inject project instructions and memory
  -> execute single Agent / TeamRun / Subagent
```

TeamRun 和 Subagent invocation 必须继承父 Run 的 Workspace / Project，不能自行指定。

## 权限规则

最终权限取交集：

```text
Workspace policy
  ∩ Project policy
  ∩ Agent config
  ∩ Run temporary grant
```

下层只能收窄权限。Project root 是默认文件边界，访问边界外路径必须经过明确授权。

## API 与前端

推荐显式资源路径：

```text
GET  /api/workspaces
POST /api/workspaces
GET  /api/workspaces/{workspaceId}/projects
POST /api/workspaces/{workspaceId}/projects
GET  /api/projects/{projectId}
PATCH /api/projects/{projectId}
GET  /api/projects/{projectId}/resources
PUT  /api/projects/{projectId}/resources/{type}/{resourceId}
```

Chat 请求只传 `project_id`，后端从 Project 反查 Workspace，不同时信任前端传入两个归属字段。

前端只提供两个全局上下文入口：

- 工作台级 Workspace 切换。
- Chat 历史区的 Project 切换。

其他页面读取统一 `AppContextState`，不重复实现下拉框。

## 迁移阶段

### Phase 0：Schema

- 新建 Workspace、Project 和 binding 表。
- 自动创建 `default` Workspace / Project。
- 给现有 Session、Run、Memory 和资源回填归属。
- 暂不改变前端行为。

### Phase 1：RuntimeContext

- Chat 请求增加 `project_id`。
- `run_context.py` 统一解析 Project。
- TeamRun、Subagent、Memory、Trace、Artifact 继承上下文。
- 删除业务模块内部隐式猜测 `default` 的逻辑。

### Phase 2：资源绑定

- Agent、Skill、MCP、HTTP Tool 归属 Workspace。
- Project binding 决定可用资源。
- ToolScope 和权限基于 binding 结果裁剪。

### Phase 3：前端

- 增加 Workspace / Project 上下文切换。
- Session、Runs、Trace、Memory、Artifacts 按 Project 筛选。
- 创建 Project 时选择和校验本地目录。

### Phase 4：治理

- Project 归档、导出和恢复。
- 跨 Project 隔离回归。
- Electron `userData` 数据迁移。
- 后续再评估多用户和 RBAC。

## 不变量

- 每个 Session 和 Run 必须属于一个 Project。
- TeamRun、Subagent invocation 必须继承父 Run 的归属。
- Project A 不得读取 Project B 的 Session、Memory、Artifact。
- Agent 只能使用当前 Project 已绑定的资源。
- Project 权限不能突破 Workspace 上限。
- 切换 Project 不得复用上一 Project 的 Chat、Team Console 或 Memory 状态。
- 删除 Project 默认归档，不直接级联删除历史 Run。

第一版只做单用户、多 Workspace、多 Project 和严格数据归属，不同时引入云同步、复杂 RBAC 或多租户服务端。
