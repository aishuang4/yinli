# Workspace 与 Project 设计

> 状态：Phase 0 至 Phase 3 已实现，2026-07-17。浏览器级跨 Project 自动化回归仍待补充。

## 当前实现状态

- 已新增 `workspaces`、`projects`、`project_resource_bindings`，启动时创建稳定的 `default` Workspace / Project，并回填历史空归属。
- Agent、Skill、MCP、HTTP Tool 已持久化 `workspace_id`；Session、Run、Run Event、Memory Node、Subagent Invocation 已持久化 `workspace_id` / `project_id`。
- Chat 请求只接收 `project_id`，后端反查 Workspace；现有 Session 禁止跨 Project 继续。
- `RuntimeContext` 已贯穿单 Agent、TeamRun、Subagent、Memory 和 Trace，Project root 与 Agent 文件范围取交集。
- RunState、TeamRun 文件状态和前端全局上下文已携带 `workspaceId` / `projectId`；切换 Project 会清理 Chat、RunState 和 Team Console 投影，不会取消后台 Run。
- Project 管理和资源 binding API 已上线；binding 已接入 Agent、Skill、MCP 和 HTTP Tool 的运行时裁剪。
- Chat 历史区和管理侧栏已提供 Workspace / Project 切换与创建入口；Session、Runs、Trace、Memory 和资源管理按当前上下文筛选。
- `Workspace + Project` 通过 bootstrap API 组合创建，先校验 Project root，避免无效目录留下没有 Project 的半成品 Workspace。
- 正式 Artifact 模型尚未上线，因此 Artifact ownership 随 Artifact Phase 一并实现，不把 Trace 中的轻量文件摘要误认为独立 Artifact。

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

前端上下文入口按工作流区分：

- Chat 历史侧栏按 Workspace / Project 分类展示会话；点击历史会话时自动切换到 Session 的持久归属。
- 新建对话在首次输入前选择 Workspace / Project；Chat 点击添加后直接打开目录选择器，并在当前 Workspace 下创建或切换 Project，不显示中间表单。
- 管理页侧栏切换当前资源管理上下文。
- Workspace 创建只保留在管理入口，供需要隔离 Agent、Skill、MCP 和权限的高级场景使用。

其他页面读取统一 `AppContextState`，不自行维护另一套上下文状态。

## 迁移阶段

### Phase 0：Schema（已完成）

- 新建 Workspace、Project 和 binding 表。
- 自动创建 `default` Workspace / Project。
- 给现有 Session、Run、Memory 和资源回填归属。
- 暂不改变前端行为。

### Phase 1：RuntimeContext（已完成，Artifact 除外）

- Chat 请求增加 `project_id`。
- `run_context.py` 统一解析 Project。
- TeamRun、Subagent、Memory、Trace、Artifact 继承上下文。
- 删除业务模块内部隐式猜测 `default` 的逻辑。

### Phase 2：资源绑定（已完成）

- Agent、Skill、MCP、HTTP Tool 归属 Workspace。
- Project binding 决定可用资源。
- ToolScope 和权限基于 binding 结果裁剪。

Phase 2 采用以下明确语义：

- `resource_type` 只允许 `agent`、`skill`、`mcp`、`http_tool`。
- binding 是白名单事实；资源必须属于 Project 所在 Workspace，且 binding 为 enabled 才能进入 Runtime。
- Agent 配置仍是能力上限，Project binding 只能继续收窄，不能给 Agent 增加未配置的 Skill、MCP 或 HTTP Tool。
- 内置 Skill / MCP catalog 是应用随版本提供的系统能力，不作为 Workspace 持久资源复制；它们仍需满足安装启用状态和 Agent 配置。用户注册的 Skill / MCP 必须经过 Project binding。
- Bash、WebFetch、HttpRequest 等内置系统工具不伪装成持久资源 binding，继续由 Agent guardrails、PermissionPolicy 和本轮 ToolScope 共同控制。
- 升级时为已有 Project 对当前 Workspace 资源执行一次性基线绑定；新建 Project 会快照绑定创建时已有资源。后续新增资源不会自动授权给既有 Project。
- `config_override` 在首版只持久化，不参与权限放大；后续只允许定义可验证的收窄字段。

### Phase 3：前端（已完成，Artifact 除外）

- 增加 Workspace / Project 上下文切换。
- Session、Runs、Trace、Memory、Artifacts 按 Project 筛选。
- 创建 Project 时通过 Electron 原生目录选择器选择并校验本地目录；目录名自动作为 Project 名称，绝对路径自动作为 `rootPath`。
- 历史会话按 Workspace / Project 分类，选中 Session 后自动恢复其归属。
- 新建 Agent、Skill、MCP 和 HTTP Tool 后显式绑定当前 Project。
- 切换 Project 时隔离 Session、消息、RunState、Team Console 和管理页数据。

正式 Artifact 模型尚未实现，因此 Artifact 的 Project 筛选与归属留在 Artifact Phase，不在本阶段用 Trace 文件摘要代替。

### Phase 4：治理

- Project 归档、导出和恢复。
- 浏览器级跨 Project 自动化隔离回归。
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
