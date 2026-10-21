# Design

## Context

动机与行为范围见 `proposal.md` 和本 change 的五份 delta spec。

当前代码已经部分以 Session 为事实边界：`chat_messages`、`chat_runs`、Plan、Subagent、SkillRun 和 `session_context_state` 都有 `session_id`。但 Workspace / Project 后补后又成为请求参数、资源白名单、目录定位和查询过滤条件：

- `RuntimeContext` 必须先用 `project_id` 找 Project，再取得 Workspace 和 `project_root`。
- 桌面全局根是 `~/.agent-workstation`，默认 Project root 却叫 `~/.agent-workstation/workspace`；外部 Project 内又写 `.agent-workstation/sessions`、`jobs`、`group-studios`。
- Agent profile 与 Skill policy 位于 Workspace catalog，Skill 包位于 tenant 全局根，MCP / HTTP / Job 同时支持 Workspace 与 Project 文件来源。
- Chat、Trace、Memory、Job、通知和群聊表保留 Workspace / Project 列，前端 47 个模块、后端 112 个模块直接传递相关字段。
- scheduler 每次 firing 新建 `sched-*` Chat Session 并在 Project Job 目录落文件；这能实现 `fresh`，但把 Job owner、执行 Context 和文件归属分裂成三处。
- hosted tenant 已经提供账号级物理隔离，因此用户可见 Workspace 并不是安全隔离的必要条件。

这是一项跨存储、文件、API、Runtime 和前端的 breaking migration，不能用一次字段重命名完成。

## Goals / Non-Goals

**Goals:**

- 建立两个且只有两个用户数据归属：tenant 全局根与 Session 根。
- 让 Session 成为交互式与后台运行的唯一聚合根，Run 成为 Session 内不可变执行尝试。
- 把外部目录降为 Session 授权数据源，不承担系统状态和能力定义。
- 统一全局能力身份、Session grant 与 Run snapshot，消除 Workspace policy / Project binding / Runtime 自选的重复裁剪。
- 无损迁移桌面和 hosted 的历史 Session、Job、群聊、文件、资源授权、Trace 和 Memory 证据。
- 在整个迁移期间保持 tenant 隔离、权限收窄、Job fail-closed 和可回滚。

**Non-Goals:**

- 不把多个 hosted 账号合并为共享空间，也不在本变更实现组织协作或跨账号共享。
- 不把外部工作目录的全部文件复制进 Session root；只迁移系统内部状态和受管上传/Artifact。
- 不恢复持久 Team，不合并群聊工作室与一次性 Team Runtime，不改变现有 SSE 领域边界。
- 不借归属改造重写 AgentScope、Memory 提炼算法、权限模型或 Job 调度算法。
- 不在第一阶段破坏性删除旧表、旧 Project 目录或历史字段；物理清理是最后且可单独发布的步骤。

## Decisions

### 1. 术语和根目录只保留明确含义

目标术语：

| 名称 | 含义 | 是否目录 |
| --- | --- | --- |
| `tenant_root` | 当前本地用户或 hosted 账号的全局数据根 | 是 |
| `session_root` | 某个 Session 的内部状态根 | 是 |
| `working_directory` | Session 主授权数据目录，可为空 | 是，可能在 tenant 外（仅桌面） |
| `allowed_roots` | Session 额外授权目录 | 是，多值 |
| `run_workspace` | Run 在 session root 下的执行目录或容器映射 | 是 |
| `SessionContext` | tenant、Session、目录 grant、资源 grant 和 Run snapshot 的不可变运行上下文 | 否 |

目标布局：

```text
<tenant_root>/
  agents/<id>.md
  skills/<package>/SKILL.md
  mcp/<id>.yaml
  tools/<id>.yaml
  jobs/                         # 仅全局模板/系统 Job Agent 定义，不存任务实例
  models.json
  memory/
    users/<user>/habits.md
    agents/<agent>/professional.md
  policies/
    skills-policy.json
  sessions/<session-id>/
    session.json                # 可重建 manifest，不保存 secret
    uploads/
    artifacts/
    context/
    runs/<run-id>/
    team/<run-id>/
    subagents/<invocation-id>/
    group-studio/<studio-id>/
    jobs/<job-id>/
      definition.md
      firings/<firing-id>/
```

外部 `working_directory` 只包含用户已有文件和 Agent 经授权创建的业务输出，不再生成 `.agent-workstation`、`.agents/INDEX.md`、Job definition 或恢复文件。

替代方案是保留 Project 作为目录别名；这会继续存在 Session 与 Project 两个 owner，因此不采用。

### 2. `SessionContext` 替换 `RuntimeContext`

新 `SessionContext` 由公开 runtime 模块创建，所有 Runtime 只依赖它，不查询 Workspace / Project 路由：

```text
tenant_id
session_id
session_kind
session_root
working_directory | null
allowed_roots[]
directory_grant_version
resource_grant_version
agent_id
run_id
run_snapshot_id
```

交互式第一条消息之前先建立 Session。后续请求只提交 `sessionId`；后端从 Session 记录解析上下文，拒绝客户端另传路径覆盖。global catalog 管理入口使用独立 `GlobalContext`，不创建假 Session。

`session_kind` 首期允许 `chat` 与 `job-owner`。普通 Job 从 Chat 创建时复用原 Chat Session；无来源的历史 Job 或管理页直接创建 Job 时建立可见的 `job-owner` Session。群聊继续是 Chat Session 的子领域，不增加第三种顶层 owner。

### 3. Session 持久 grant 与 Run 不可变 snapshot 分层

新增规范化记录：

```text
sessions
  id, kind, title, status
  working_directory, directory_grant_version
  resource_grant_version
  created_at, updated_at, archived_at

session_allowed_roots
  session_id, normalized_path, access_mode, source, created_at

session_resource_grants
  session_id, resource_type, resource_id, enabled, config_override
  grant_version, created_at, updated_at

run_context_snapshots
  id, run_id, session_id
  directory_grant_version, resource_grant_version
  working_directory, allowed_roots_json
  resources_json, model_snapshot_json, approval_snapshot_json
  created_at
```

每次 Run 开始流程固定为：读取 Session → 校验全局资源与目录 → 计算 Agent 上限交集 → 原子写 `run_context_snapshots` → 创建 Agent。Session grant 更新只影响下一 Run；当前 Run 不热更新权限。资源全局删除/停用后，新 Run fail closed；当前 Run 依赖自己的进程内/持久 snapshot 完成，但工具仍受已冻结的 denylist 和目录边界约束。

Run 快照的唯一写入入口是 Run 创建事务（`begin_run`）。Chat、scheduled firing 和群聊 Worker 的每次 Run MUST 经过它；Team 成员和 Subagent 不另建快照，MUST 使用父 Run 快照或其子集（§16.2），父快照缺失时以 `snapshot_required` 失败；Agent factory 缺少快照时以稳定错误码失败，不得回落旧 guardrails、Project binding 或 Project root。快照库函数和单元测试存在，不等于该流程已生效，验收以生产调用点为准（见 §15）。

替代方案是每轮动态查询全局状态，容易让同一 Run 中途换 Skill/MCP 或扩大权限，因此不采用。

### 4. 全局 catalog 不按 Session 复制正文

Agent、Skill、MCP 和 HTTP Tool 只保留一个 tenant 内身份。现有 Workspace profile 迁移到 `<tenant_root>/agents`；Skill policy 从 Workspace 文件合并为 tenant 全局 policy；MCP / HTTP YAML 迁移到全局目录。

Session grant 只保存稳定 ID和有限 override。Agent profile 是能力上限；Session grant 是用户允许集合；Run ToolScope / authorization 是执行时最终收窄。`availableToAllAgents` 表示该 Skill 可进入合格 Chat 的候选集合，Run snapshot 仍记录实际获得的 Skill。

迁移冲突规则：

- 同稳定 ID 且规范正文相同：合并，保留全部来源证据。
- 同稳定 ID 但正文不同：生成 conflict，不覆盖；相关 Session 暂停使用该资源。
- 同名不同 ID：两者保留，UI 显示来源；禁止按名称猜测绑定。
- Project 专属资源：可迁入全局目录，但只给原 Project 的历史 Session 建 grant，不自动开放给全部 Session。
- secret 只迁移引用；发现明文时进入安全迁移流程并从报告脱敏。

### 5. 外部目录是授权，不是 owner

桌面端创建/编辑 Session 可通过 Electron 目录选择器设置 working directory。后端保存 realpath、设备/文件标识（平台允许时）、访问模式和用户授权时间；每次 Run 复核目录存在性与符号链接边界。hosted 只能选择 tenant 项目存储或容器可挂载目录，不能提交宿主绝对路径。

默认无外部目录的 Session 使用 `<session_root>/artifacts` 作为文件工具工作区。选择外部目录后，Read/Grep/Glob 默认 cwd 可指向 working directory，Write/Edit 的最终范围为 Session allowed roots、Agent filesystem scope 和审批策略交集。上传仍先落 Session root，用户明确要求写入外部目录时才复制或修改。

不再把 `/`、进程 cwd 或旧 `default_project_root()` 当空配置默认值。

### 6. Job owner 与 firing Context 分开

`scheduled_jobs` 增加非空 `owner_session_id`；`created_from_session_id` 迁移后废弃。Job definition 位于 owner Session 下。firing 不再创建 `sched-*` 顶层 Chat Session，而是创建 owner Session 下的 Run，并将 scheduled 输入、模型消息、工具记录和输出与 `run_id` 关联。

为避免污染普通 Chat 历史：

- 交互式 `chat_messages` 继续表示 Session 主时间线。
- scheduled firing 的模型上下文与消息保存在 Run domain event / observation 和 firing output 中，不追加为普通 Chat 消息。
- Chat 时间线可展示一条结构化“Job firing 完成/失败”摘要 Block，点击进入 Run 详情。
- `fresh` 每次从 Job prompt、required Skills、Run snapshot 和允许的 bounded previous result 构建，不读取 Chat 全历史。

active Job 对 owner Session 建删除保护。暂停/归档 Job 后可删除 Session；删除流程清理 Job 实例和内部文件前再次检查 ticker lease，防止并发 firing。

### 7. 群聊、Team 和 Subagent 继承 Session

`group_studios` 已有 `chat_session_id`，迁移后删除 API 模型中的 Workspace / Project 字段并由 Session 反查所有上下文。群聊文件从 `<project>/.agent-workstation/group-studios/<id>` 复制到 `<session_root>/group-studio/<id>`。

TeamRun 仍是 Run 内临时协作；Member 私有目录改为 `<session_root>/team/<run>/<member>`。Subagent task store 改为 `<session_root>/subagents/<invocation>`。二者只能消费父 Run snapshot 的显式子集，不重新解析全局 grant。

### 8. Timeline、Memory、Trace、通知和 Artifact 以 Session 为索引

新写数据不再把 Workspace / Project 当权限条件：

- timeline 原文由 `session_id + message/run` 定位，跨 Session 检索是 tenant 全局工具显式操作。
- 习惯和 Agent 专业区继续全局注入，不复制到 Session。
- Trace 以 `run_id` 查询并校验其 `session_id`；列表按 Session 或显式 tenant 全局视图。
- 通知保存 `owner_session_id` 和可选 `run_id/job_id/studio_id`，点击后打开 owner Session。
- Artifact 必须有 `session_id`、producer run 和受管/外部引用类型；外部引用删除 Session 时不删除源文件。
- 历史 `memory_nodes` 的 Workspace / Project 列保留只读，新的 timeline/长期记忆路径不依赖它们。

### 9. API 采用先增后减版本迁移

新增 Session API：

```text
POST   /api/sessions
GET    /api/sessions
GET    /api/sessions/{id}
PATCH  /api/sessions/{id}
POST   /api/sessions/{id}/archive
DELETE /api/sessions/{id}
GET/PUT /api/sessions/{id}/resources
GET/PUT /api/sessions/{id}/directories
POST   /api/sessions/{id}/files
```

Chat send/resume/steer、Job、Trace、通知和群聊新契约只收 `sessionId`。全局 Agent/Skill/MCP/Tools/Models API 不收 Session/Workspace/Project query。迁移过渡期旧 endpoint 可返回 deprecation headers，并仅把旧 `projectId` 用于查找已有 Session；禁止客户端通过它更改 Session working directory。

Chat SSE 顶层事件不因本变更新增。Session/Run snapshot 的用户可见变化仍经现有 Domain Event → RuntimeFrame / DisplaySnapshot 投影；管理 API 响应不作为运行 UI 的最终状态。

### 10. 前端以 active Session 取代 active Workspace / Project

移除 TitleBar 和管理页 Workspace/Project 选择器、ContextCreateModal 与 `activeWorkspaceId/activeProjectId`。应用启动只加载 tenant Session 列表和全局 catalog；空状态允许直接新建对话。

创建 Session 时：

- 桌面端可选 working directory；不选则使用内部 Artifact 工作区。
- Web 端由服务端给出可选 tenant 目录或保持内部工作区。
- Session header 展示 working directory 和权限入口，而不是全局 Project selector。

切换 Session 原子清理 Chat/Team/群聊投影、附件草稿和 active Run 订阅，再加载该 Session 的消息、资源 grant 和通知。管理页面显示全局 catalog；需要“本对话可用”时从 Session 详情进入资源面板。

### 11. Hosted 安全边界保持 tenant-first

数据库可能按 tenant 分库/目录运行，因此大多数业务表无需新增 tenant_id；所有入口仍必须由 auth context 决定数据库和 tenant root，禁止用 Session payload 选择 tenant。control DB、Key2API、账号删除和配额逻辑保持不变。

账号删除清理 tenant root 时自然包含全局 catalog 与全部 Sessions。管理员在线状态不依赖 Workspace。原 Workspace 未来如需组织协作，应作为新的 Organization 能力重新设计，不能复用此次删除的目录和字段语义。

### 12. 迁移器采用 manifest、校验和与完成门

迁移状态位于 `<tenant_root>/migrations/global-session-v1/`：

```text
backup/                 # 旧表、旧 catalog、定义文件和路径清单
inventory.json          # 发现的 Workspace/Project/Session/Job/资源
mapping.json            # old ids/paths -> global ids/session ids
conflicts.json          # 不可自动决定项
file-manifest.json      # size/hash/source/target/copy state
report.json
complete.json           # 所有验证通过后最后写
```

复制跨卷文件时先写 Session root 内隐藏 staging，fsync、hash 校验后 rename。源目录不在首轮删除。数据库更新用当前 SQLite 事务，文件提交状态在 manifest 中分步记录；任一阶段重跑必须幂等。

### 13. 兼容期不允许长期双写

Runtime 不双读。Chat、Team、Subagent、群聊、Job firing、工具授权复核和 Sandbox 只读 Session 记录与 Run 快照，任何时候都不读取 Workspace / Project 表、Project root 或 Project binding。旧数据只由 `src/session_migration/` 的迁移与回滚模块读取。（第二轮复核修订：原“允许短期双读”与 §15 的 import 边界、skills spec 的“不再存在 Project binding 门禁”相互矛盾，因此取消。）

tenant 完成切换前（`cutover_state()["cutover"] != "cutover"`），满足 §16.5 判定的“未迁移历史 Session”拒绝新 Run（`session_not_migrated`），不按旧 Project 推测目录或资源；新建 Session 不受影响。全新 tenant 由 §16.6 的启动门直接置为 cutover。

新写从第一阶段起只写 SessionContext 与新 Session 文件布局：`chat_sessions`、`group_studios`、`scheduled_jobs` 的 `workspace_id` / `project_id` 只能写 `""`。若必须兼容旧版本，写一个显式导出/回滚 manifest，而不是继续写 Project 目录。

### 14. Scheduled Job 使用独立的 preloaded-only Skill Runtime

定时 Job 的 Skill 语义与普通 Chat 不同，必须在 Toolkit 构造层隔离，而不能只靠 system prompt 或 denylist 约束模型。

- 在 Runtime spec 中增加明确的 Skill loading mode，至少区分 `interactive_on_demand` 与 `scheduled_preloaded`。scheduled mode 创建 Toolkit 时不注册 AgentScope 内置 `SkillViewer`；`skill_paths` 只用于运行目录挂载、引用路径解析和受限文件访问。
- 不把 `Skill` 加入 scheduled denylist 后就认为完成。内置 viewer 不属于普通 local tool 过滤链，必须在构造阶段关闭；同时把 mode 纳入 Agent cache key/runtime feature key，防止交互 Agent 实例复用到 scheduled 工具集或反向复用。
- 普通 Chat 继续使用现有 `AGENT_WORKSTATION_SKILL_INSTRUCTION`，其中允许模型根据名称/描述调用 `Skill`。scheduled mode 使用独立模板，只声明 required Skill 已在 `<activated-skill>` 中注入、无需调用 Skill、引用文件和脚本按需使用现有工具；模板不得包含“call the Skill tool”一类普通 Chat 指令。
- 全局 Skill catalog 的 `<skill>` 条目同时输出稳定 `id`、显示名称、描述和加载目录。`ScheduleJob.skill_ids` 只接受这些稳定 ID（兼容期可由服务端把唯一名称解析为 ID），不得要求模型猜测数据库或文件路径。
- `ScheduleJob` 的 `required_skills` 只能作为旧 API 的兼容读取字段；交互模型不应填写它，服务端在 `ConfirmJob` 内从 live catalog、Session grant 和原始 `SKILL.md` 原子生成。若外部提交的 manifest 与实时内容不一致，必须拒绝或忽略其权威性，不能覆盖冻结快照。
- firing 顺序固定为：preflight/hash 校验 → 创建 scheduled Toolkit → 恢复 Run snapshot → `activate_forced_skills` → 验证所有 required Skill 的 `<activated-skill>` 块、ID/hash/正文 → 追加 Job prompt → 首次模型调用。任一步失败都不得启动模型。
- references/scripts 仍由 Read/Bash 等已有工具访问；Skill 包挂载不会扩大工具、filesystem scope、网络/MCP、审批模式或 unattended denylist。scheduled runtime 若收到旧缓存或异常 Skill tool call，必须在工具层拒绝并记录，而不能执行第二次正文加载。

这样可以保留普通 Chat 的按需 Skill 体验，同时让 scheduled Job 的行为稳定等同于“确认后的固定 Skill Prompt 快照”。

tenant 达到以下切换门才置为 cutover（逐项对应 §16.6 第 8 步的五项检查）。切换前 Runtime 同样不读旧数据，见 §13：

- 所有非归档 Session 均有 working directory grant、resource grant version 和可读 Session root。
- 所有 active/pending Job 均有 owner Session 与可校验 Run workspace。
- 所有 active group studio 都能从 chat_session_id 构建 SessionContext。
- 文件 manifest 无 pending/corrupt，数据库计数与来源清单相符。
- 新 API、Runtime、ticker、恢复任务和前端不再查询 Workspace / Project。

### 15. 验收以生产调用链与全新 tenant 为准

2026-10-19 复核发现：Session 基础设施和单元测试齐全，但 Chat、群聊和 Job 主链路仍走 `RuntimeContext`；全局测试夹具为每个用例预置 default Project，掩盖了全新 tenant 无法发送消息的问题。为此补充以下验收规则：

- 任务勾选须附生产调用点证据（调用链或集成测试）；只调用库函数的单元测试不算完成。
- 测试夹具 MUST NOT 为所有测试自动创建 default Workspace / Project；历史数据只通过迁移 / 回滚测试显式使用的 legacy fixture 构造。
- 桌面和 hosted 各保留一条“全新 tenant：新建 Session → 首条消息 → 新建群聊 → 确认并触发 Job”的端到端用例，数据库中不存在任何 Workspace / Project 行。
- 静态护栏以 import 图为准：除 `session_migration/` 与回滚模块外，任何模块导入 `runtime_context`、`project_resources`、`project_files` 即失败；不得用字符串拼接规避搜索。
- Session 授权必须有“与旧证据相反”的回归用例：Session 目录与旧 Project root 不同、Session grant 与旧 binding 相反时，Run 快照以 Session 为准。

### 16. 复核修复实施契约（2026-10-19 第二轮）

本节是 tasks 第 13–20 节的唯一实现依据。实现中遇到本节没有覆盖的情况，先停下来修订本节，再写代码；不得自行选择方案。函数名、文件名、错误码和测试名都按本节原样使用。

#### 16.1 单一来源

| 关注点 | 唯一来源 | 位置 | 禁止 |
| --- | --- | --- | --- |
| Session 归属与目录 | `build_session_context(session_id, *, agent_id="", run_id="", run_snapshot_id="")` | `src/runtime/session_context.py`（已存在） | 读取 Project root；目录失效时回落 |
| Run 默认 cwd | `SessionContext.cwd`：`working_directory` 非空时取它，否则取 `session_artifacts_root(session_id)` | 同上，新增只读 property | 使用 `session_root()`、`tenant_root()`、进程 cwd、`Path("")`、`Path(".")` |
| Run 启动前的资源预览 | `candidate_resource_access(session_id, *, agent_id, permission_mode)` | 新文件 `src/sessions/run_access.py`，内部只调用 `select_run_candidates` | 调用 `resolve_project_resource_access`、`list_project_resource_bindings` |
| Run 启动后的资源 | `run_resource_access(run_id)`，只读 `get_run_snapshot(run_id)` | 同上 | 重新解析 grant；快照缺失时回落 |
| 快照写入 | `begin_run(session_id, run_id, *, agent_id, permission_mode, start_model)`，其余现有关键字参数不变 | `src/sessions/run_binding.py` | 任何其他函数写 `run_context_snapshots` |
| 工具执行时复核 | `RuntimeCapabilityReader.snapshot()` 读 `run_resource_access(run_id)` 与 Run 快照目录 | `src/runtime/authorization_resources.py` | 调用 `resolve_runtime_context`、`resolve_project_resource_access`、`scope_guardrails_to_project` |
| 未迁移历史 Session 准入 | `require_migrated_session(session_id)` | 新文件 `src/sessions/legacy_gate.py` | 按旧 Project 推测目录或资源 |

`RunResourceAccess` 契约（`src/sessions/run_access.py`）：

```python
@dataclass(frozen=True)
class RunResourceAccess:
    session_id: str
    agent_id: str
    skill_ids: frozenset[str]
    mcp_server_ids: frozenset[str]
    http_tool_ids: frozenset[str]

    def allows(self, resource_type: str, resource_id: str) -> bool: ...
    def filter_ids(self, resource_type: str, resource_ids: Iterable[str]) -> list[str]: ...


def candidate_resource_access(session_id: str, *, agent_id: str, permission_mode: str) -> RunResourceAccess: ...
def run_resource_access(run_id: str) -> RunResourceAccess: ...
```

- `candidate_resource_access` 的条目来自 `select_run_candidates(session_id, agent_id=agent_id, permission_mode=permission_mode)`；`run_resource_access` 的条目来自 `get_run_snapshot(run_id)["resources"]["items"]`。同一 Session、Agent、permission mode 下两者结果必须相同。
- `resourceType` 映射：`skill` → `skill_ids`；`mcp` → `mcp_server_ids`；`http_tool` 和 `knowledge` → `http_tool_ids`。
- `allows("agent", x)` 只在 `x == agent_id` 时为真；未知类型返回假。`filter_ids` 保持输入顺序并去重。
- 两个函数都不读取 Workspace / Project，也不捕获 `SessionError`。`run_resource_access` 在快照缺失时抛 `SessionError("snapshot_required", "Run 没有冻结快照")`。
- `select_run_candidates` 新增关键字参数 `agent_id: str | None = None`：为 `None` 时用 Session 记录中的 `agentId`，否则用传入的 Agent profile 计算上限。
- `begin_run` 新增必填关键字参数 `agent_id`，执行顺序固定为：
  1. `build_session_context(session_id, agent_id=agent_id, run_id=run_id)`（可能抛 `session_not_found`、`session_archived`、`directory_unavailable`）；
  2. `require_migrated_session(session_id)`：与 §16.6 的启动门在同一任务（18.2）接入，此前不调用，以免历史用户在没有迁移入口时被拒绝；
  3. 现有的 pinned grant 停用检查（`resource_disabled`）；
  4. `select_run_candidates(session_id, agent_id=agent_id, permission_mode=permission_mode)`；
  5. `freeze_run_snapshot(...)`，其中 `working_directory=str(ctx.working_directory or "")`，`agent_snapshot={"agentId": agent_id}`；
  6. `start_model(snapshot)`。

#### 16.2 各 Runtime 的启动顺序

Chat（`handlers/send.py` → `background_runs.start_chat_background_run`）：

1. HTTP 层（`POST /api/chat/send/start` 与 `POST /api/chat/send`）中，`data.project_id` 非空时返回 410 `{"code": "legacy_scope_retired"}`，与 `POST /api/sessions` 一致。
2. `ensure_session_for_message(session_id=..., agent_id=..., title=...)` 删除 `project_id` 参数；新建 Session 的 `project_id`、`workspace_id` 一律写 `""`。
3. 校验 forced skills 时使用 `candidate_resource_access(session_id, agent_id=agent_id, permission_mode=data.approval_mode)`。
4. `start_chat_background_run` 生成 `run_id` 后，在 `_persist_run` 之前调用 `begin_run(session_id, run_id, agent_id=agent_id, permission_mode=approval_mode, start_model=lambda snapshot: snapshot)`，其中 `approval_mode` 是 `start_chat_background_run` 的同名参数。异常原样上抛：不写 Run 行、不建 Run root、不创建后台 task。删除 `_freeze_run_snapshot`。
5. 后台 event stream 内，`build_chat_run_context` 的资源来源是 `run_resource_access(run.id)`。第 20.2 项之前，Session 上下文经 14.4 的过渡版 `resolve_runtime_context` 构造；之后直接用 `build_session_context(session_id, agent_id=agent_id, run_id=run.id, run_snapshot_id=snapshot["id"])`。
6. `react_runtime` 把 `frozen_resources_for_run(run.id)` 传给 `build_main_agent_runtime_spec(run_snapshot=...)`。该参数改为无默认值的关键字参数，值为 `None` 时抛 `snapshot_required`。

Scheduled firing（`scheduler/chat_bridge.py` → `scheduled_launch.py` → `start_chat_background_run`）：与 Chat 第 4–6 步相同，`agent_id` 取 Job 的 Agent，`approval_mode` 取 `job.execution_policy.approval_mode`。启动前的 required Skill 校验（`chat_bridge.py` 中 `validate_required_skill_manifest`）使用 `candidate_resource_access(session_id, agent_id=req.agent_id, permission_mode=<同一 approval_mode>)`。`build_scheduled_run_context` 用 `build_session_context(owner_session_id, agent_id=job.agent_id, run_id=run_id)` 和 `run_resource_access(run_id)`，不查询 Project，也不比较 `workspace_id`。

Team 成员：沿用父 Run 快照（`member_runtime` 现有逻辑）。父快照为 `None` 时抛 `snapshot_required`。

Subagent：`child_snapshot_subset(frozen_resources_for_run(parent_run_id), definition)`，结果为 `None` 时抛 `snapshot_required`。删除 `subagents/runtime_context.py` 中回落 `resolve_project_resource_access` 的分支。

群聊 Worker（`group_studio/worker_runtime.py::_run_worker_turn`）：

1. 先生成 `run_id`，再调用 `begin_run(owner_session_id, run_id, agent_id=<成员 Agent>, permission_mode="bypass", start_model=lambda snapshot: snapshot)`。失败时按现有“成员已静音”分支处理：移出发言队首，发出 `GROUP_ERROR`（payload 带 `code`），不调用模型。
2. `build_worker_runtime_spec` 新增必填关键字参数 `run_snapshot`：
   - 资源取 `run_resource_access(run_id)`；
   - 额外文件范围取快照 `allowedRoots` 中 `accessMode == "read_write"` 的路径；
   - 模型仍由 wave grant 冻结；
   - `filesystem_scope` 保持为房间 workspace 目录，并以 `apply_snapshot_filesystem=False` 传入快照。
3. 删除 `resolve_runtime_context`、`resolve_run_resource_access` 和 `_granted_ids` / `_narrow_ids` 的资源收窄。

工具执行复核（`RuntimeCapabilityReader.snapshot()`）：

1. `snapshot = frozen_resources_for_run(run_id)`，为 `None` 时抛 `snapshot_required`。
2. `access = run_resource_access(run_id)`。
3. 资源映射由 `access` 的三个集合构成。
4. MCP 用 `build_runtime_mcp_servers(config, allowed_server_ids=access.mcp_server_ids)`，HTTP 用 `build_runtime_knowledge_tools(config, allowed_tool_ids=access.http_tool_ids)`。全局已停用的资源不得出现在结果中：MCP 由 `scheduler/mcp_runtime.py::resolve_mcp_runtime_servers` 按 `enabled` / `status` 过滤，HTTP 由 `get_enabled_http_tools` / `get_enabled_knowledge_tools` 过滤，reader 不再另做过滤。
5. 文件范围取 `apply_run_snapshot_to_factory(snapshot, guardrails=build_runtime_guardrails(config), skill_paths=[], mcp_servers=[], knowledge_tools=[]).guardrails`。
6. 删除“运行 Project 或 Workspace 归属已变化”检查。执行 Agent 已删除的检查保留。

不属于 Session Run 的内部 Agent（Memory 抽取、readiness gate、intent router、Agent builder）继续直接调用 `get_or_create_agent`，不受快照规则约束，也不得获得 Session 资源。

#### 16.3 错误码与 HTTP 映射

新文件 `src/server/routes/session_errors.py` 提供 `session_error_http(exc: SessionError, *, default_status: int) -> HTTPException`，返回 `HTTPException(status_code=<状态>, detail={"code": exc.code, "message": exc.message})`，即响应 JSON 为 `{"detail": {"code": ..., "message": ...}}`，与现有 `_http_error` 一致。Chat、群聊、Job 和 Session 路由都改用它。表中列出的 code 在所有路由都使用表中状态；未列出的 code 使用调用方传入的 `default_status`：`sessions.py` 传 404，其余传 409，保持现状。

| code | 抛出位置 | HTTP |
| --- | --- | --- |
| `session_not_found` | `build_session_context`、`ensure_session_for_message` | 404 |
| `session_archived` | `build_session_context` | 409 |
| `directory_unavailable` | `build_session_context` | 422 |
| `session_not_migrated` | `require_migrated_session` | 409 |
| `resource_not_found` | `select_run_candidates`（Agent 不在 catalog） | 409 |
| `resource_disabled` | `begin_run` 第 3 步 | 409 |
| `agent_ineligible` | Session grant 校验 | 409 |
| `tenant_mismatch` | 现有位置 | 409 |
| `session_has_active_runs` | 现有位置（Session 删除） | 409 |
| `hosted_path_rejected` | 现有位置（hosted 目录设置） | 409 |
| `snapshot_required` | `run_resource_access`、`build_main_agent_runtime_spec`、Team、Subagent、群聊 Worker | 500 |
| `legacy_scope_retired` | Chat / Session HTTP 层 | 410 |

Run 已经创建后才出现的 `SessionError`，按现有失败路径结束 Run，错误事件中带 `code`。

#### 16.4 目录规则

- 文件工具范围 = `apply_run_snapshot_to_factory` 的结果（cwd + allowedRoots）∩ Agent guardrails。第 20 节完成后，任何模块都不再调用 `scope_guardrails_to_project`。
- 目录失效时，`build_session_context` 抛 `directory_unavailable`，Run 不启动。不得回落 Project root、`session_root()`、`tenant_root()` 或进程 cwd。
- 生产代码不得出现以 `Path("")` 或 `Path(".")` 作为目录的实参。`routes/sessions.py` 上传处现有的 `project_root=Path(".")` 一并删除。

#### 16.5 未迁移历史 Session 判定

`require_migrated_session(session_id)` 在以下五项同时成立时抛 `SessionError("session_not_migrated", "该对话来自旧版本，需完成数据迁移后才能继续", details={"projectId": <值>})`：

1. `cutover_state()["cutover"] != "cutover"`；
2. Session 记录的 `projectId` 非空；
3. Session 记录的 `workingDirectory` 为空；
4. `list_allowed_roots(session_id)` 为空；
5. `list_resource_grants(session_id)` 为空。

`sessions/legacy_gate.py` 是除 `session_migration/` 外唯一允许读取 Session 记录 `projectId` 的运行时模块，并登记在 `reference_allowlist.json` 中。只读查询（Session 详情、历史消息、Artifact 列表）不调用它。

唯一调用点是 `begin_run` 第 2 步，与启动门同时接入。同时，测试夹具 `test/conftest.py::local_tenant` 在 `yield` 前调用 `set_migration_flag(True, schema_version_value=1)`，模拟启动门已执行的 tenant；hosted tenant 由 `ensure_tenant` 中的启动门切换，hosted 夹具不改。需要 legacy 阶段的用例显式调用 `set_migration_flag(False, schema_version_value=0)`，并且不经过 lifespan，因为 lifespan 会运行启动门。`fresh_tenant` 夹具不设置该 flag。

#### 16.6 迁移执行入口

新文件 `src/session_migration/runner.py`：

```python
def run_tenant_migration(*, dry_run: bool = False) -> dict[str, Any]: ...
def ensure_tenant_cutover() -> dict[str, Any]: ...
```

`run_tenant_migration` 按以下固定顺序执行，每一步结果写入 `migration_dir()/report.json`（dry-run 除外），函数幂等。

数据库连接规则：`conn` 一律用 `from src.storage.database import _connect, _lock` 后的 `with _lock, _connect() as conn:` 打开。`set_migration_flag` 会自己开连接写入，因此不得在持有 `conn` 的 `with` 块内调用，以免 SQLite 写锁冲突。具体做法：第 2–4 步共用一个连接；第 3 步命中时先关闭连接，再写 flag；第 5 步先在连接外调用 `set_migration_flag(True)`，再新开连接执行 `write_backup`、第 6 步和第 7 步；第 8 步的检查用新连接；第 9 步在所有连接关闭后执行。

1. `cutover_state()["cutover"] == "cutover"` 时返回 `{"status": "already_cutover"}`。
2. `inventory = collect_inventory(conn, tenant_root=tenant_root())`。
3. 全新 tenant 判定：`inventory["counts"]["workspaces"] == 0`、`inventory["counts"]["projects"] == 0`、`inventory["sessions"]` 与 `inventory["jobs"]` 中没有 `project_id` 非空的行，且 `inventory["definitions"]` 中没有 kind 为 `project-agents` 或 `project-internal` 的条目。成立时：
   - 非 dry-run：`set_migration_flag(True, schema_version_value=1)`，写 `migration_dir()/complete.json`（内容 `{"ok": true, "fresh": true}`），返回 `{"status": "fresh_cutover"}`；
   - dry-run：只返回该结果。
4. `conflicts = precheck(conn, tenant_root=tenant_root())`。dry-run 返回 `{"status": "dry_run", "inventory": ..., "conflicts": ...}`，不写任何文件。
5. `set_migration_flag(True)`（armed），然后 `write_backup(conn, tenant_root=tenant_root(), destination=migration_dir() / "backup")`。
6. `conflicts["blocking"]` 为真时返回 `{"status": "blocked", "conflicts": conflicts["conflicts"]}`，tenant 保持 armed。
7. 依次执行：
   - 按资源类型分别调用 `migrate_catalogs(roots, destination, migration_dir() / "catalog" / <kind>)`（参数含义与 `test_session_migration_section11.py::test_same_catalog_merges_and_conflict_blocks` 一致）。来源根 `S` 依次为：每个 Workspace 的 `catalog.paths.workspace_catalog_root(ws_id)`（跳过 `_system`），以及每个 `root_path` 为目录的 Project 的 `<root_path>/.agents`。固定映射如下：

     | kind | roots | destination |
     | --- | --- | --- |
     | `agents` | 各 `S / "profiles"` | `global_agents_root()` |
     | `mcp` | 各 `S / "mcp"` | `global_mcp_root()` |
     | `tools` | 各 `S / "tools"` | `global_tools_root()` |
     | `jobs` | 各 `S / "jobs"` | `global_jobs_root()` |
     | `subagents` | 各 `S / "agents"` | `subagent_root()` |

     `S / "skills"` 不在这里处理，由已有的 `migrate_tenant_skills()` 负责（启动门在它之后运行）。任一 kind 的返回值中 `conflicts` 非空时，立即返回 `{"status": "blocked", "conflicts": <该返回值的 conflicts>}`，不执行后续步骤；
   - `backfill_session_context(conn)`；
   - `backfill_owners(conn)`；
   - 对 `inventory["definitions"]` 中 kind 为 `project-internal` 的每个文件调用 `stage_file(source, <目标 Session root>, <relative>, migration_dir() / "file-manifest.json")`，路径映射固定为：
     - `<root>/.agent-workstation/sessions/<sid>/<rel>` → `session_root(<sid>)`，relative 为 `<rel>`；
     - `<root>/.agent-workstation/jobs/<job_id>/<rel>` → 该 Job owner Session 的 `session_root`，relative 为 `jobs/<job_id>/<rel>`；
     - `<root>/.agent-workstation/group-studios/<studio_id>/<rel>` → 该群聊 `chat_session_id` 的 `session_root`，relative 为 `group-studio/<studio_id>/<rel>`；
     - 其他路径不复制，记入 `report.json` 的 `unmappedFiles`，保留原位，不阻断切换；
   - 只有 precheck 产出显式映射时，才执行 `apply_mapping_to_grants(...)`。
8. `ready` 需以下五项全部为真：
   - (a) 没有“Project root 有效但未写入 working directory”的 Session；
   - (b) 每个 active / pending Job 都有 owner Session；
   - (c) 每个 active 群聊的 `chat_session_id` 都能通过 `build_session_context`；
   - (d) 文件 manifest 中没有 pending / corrupt 条目；
   - (e) 迁移后的 Session 数等于 inventory 中的 Session 数。
9. 调用 `attempt_cutover(ready=ready, pause=lambda: None, resume=lambda: None, marker=migration_dir() / "complete.json", switch_reads=lambda: set_migration_flag(True, schema_version_value=1))`。启动门执行时 ticker 尚未启动，CLI 要求后端已停止，因此 `pause` / `resume` 都是空操作。
   - 切换成功返回 `{"status": "cutover", "checks": <五项结果>}`；
   - `ready` 为假或切换失败返回 `{"status": "not_ready", "checks": <五项结果>}`，tenant 保持 armed，未迁移 Session 按 §16.5 拒绝新 Run。

`ensure_tenant_cutover()` 调用 `run_tenant_migration(dry_run=False)`。出现异常时记录日志并返回 `{"status": "failed", "error": <异常类名>}`，不向上抛出。

接入点：

- `server/main.py` 的 lifespan：对 `startup_tenants()` 中每个 tenant，在 `migrate_tenant_skills()` 之后、`bootstrap_tenant_scheduler()` 之前调用。
- `tenancy/lifecycle.py::ensure_tenant`：新 hosted tenant 在 `init_db()` 之后调用。
- 现有 `GET /api/migrations/global-session-v1`：响应增加 `cutoverState`。

CLI（新文件 `src/session_migration/__main__.py`）：

- 用法：`python -m src.session_migration {status,dry-run,run,rollback} [--tenant <id>]`。桌面默认 local tenant；hosted 必须给 `--tenant`。
- `rollback` 调用 `rollback_to_legacy(migration_dir() / "backup", migration_dir() / "rollback-export", sessions_root(), set_legacy_read=lambda: set_migration_flag(False))`，参数含义与 `test_session_migration_section11.py::test_rollback_exports_legacy_copy_without_touching_session` 一致。
- 输出 JSON。退出码：`already_cutover`、`fresh_cutover`、`cutover`、`dry_run` 以及 rollback 成功为 0，`blocked`、`not_ready` 为 2，异常为 1。
- `run` 和 `rollback` 只能在后端停止时使用，这一点写入 CLI 帮助文本。

#### 16.7 测试规则

- 全新 tenant 夹具：`backend/test/fresh_tenant.py` 中的 `fresh_tenant` fixture（不 autouse），进入和退出时都断言 `workspaces`、`projects` 两表行数为 0。实现方式沿用 13.1 删除的 `test_fresh_session_runtime.py::fresh_db`：把 `runtime_paths.USER_DATA_ROOT` 与 `settings.database_url` 切到新的 tmp 目录，清空 `_ensured_tenants`，再执行 `ensure_tenant_directories()` 与 `ensure_db()`，以此覆盖 autouse 的 `local_tenant`。辅助函数：
  - `install_skill(name, *, shared)`，返回 catalog ID；
  - `register_agent(agent_id, *, skills=(), mcp=(), http=())`，写入 `skills`、`mcpTools`、`knowledgeTools`；
  - `write_mcp(id)`、`write_http_tool(id)`，写到 `global_mcp_root()` / `global_tools_root()`；
  - `capture_model_boundary(monkeypatch)`；
  - `fake_model_binding(monkeypatch)`：把 `src.config.runtime_settings.resolve_model_binding` 与 `src.server.routes.chat.handlers.send.resolve_model_binding` 替换为返回固定 binding 的替身，binding 结构沿用 `fresh_db` 所在文件中的 `SimpleNamespace`；
  - `wait_run(run_id, timeout=5.0)`。
- 模型边界：验收用例中，只允许替换 `src.runtime.agent_spec.get_or_create_agent`（Chat、Team、群聊）和 `src.subagents.runner.get_or_create_agent`（Subagent）。替身记录实参后抛 `ModelBoundaryReached`。被验证链路上的其他函数一律不得替换，包括 `start_chat_background_run`、`begin_run`、`select_run_candidates`、`candidate_resource_access`、`run_resource_access`、`build_session_context`、`build_chat_run_context`、`build_scheduled_run_context`、`build_main_agent_runtime_spec`。
- 如果用例在到达模型边界前被与本变更无关的环境检查（模型、密钥、网络）拦截，只能在 `fresh_tenant.py` 中新增替身，并先在本节登记函数名再使用；资源与上下文链路上的函数一律不得替换。
- strict xfail：13.2 写入的验收用例先标 `@pytest.mark.xfail(strict=True, reason="待 <任务号>")`，负责任务完成时删除标记。用例已通过但标记未删时，strict 会让它失败。
- 历史数据只能通过 `backend/test/legacy_fixtures.py` 中的显式 fixture `legacy_project` 构造，且只用于迁移、回滚和隔离测试。
- 直接调用 `build_main_agent_runtime_spec`、`build_worker_runtime_spec`、`build_subagent_runtime_context` 的单元测试，新增或改动的快照一律用 `backend/test/snapshot_support.py::freeze_test_snapshot` 经 `begin_run` 生成，不得手写快照字典；`test_factory_run_snapshot.py` 这类专门测试 `freeze_run_snapshot` 本身的用例除外。
- 禁止：
  - 用字符串拼接旧字段名（`"workspace" + "_id"`、`"project" + "Id"` 等）；
  - 为通过护栏而向 `reference_allowlist.json` 或 `LEGACY_IMPORT_ALLOWLIST` 新增条目。这两个清单只能删减，例外只有 `session_migration/`、`sessions/legacy_gate.py`、`test/legacy_fixtures.py`、`test/test_legacy_scope_isolation.py`。
- 环境性失败基线（Mac）：`test_model_catalog.py` 中 3 个用例和 `test_native_skills_runtime.py::test_local_bash_cancellation_kills_child_processes`，原因均为 `PermissionError`。除此之外的任何失败都视为任务未完成。

#### 16.8 RuntimeContext 移除映射（第 20 节）

| 旧写法 | 新写法 |
| --- | --- |
| `resolve_runtime_context(project_id=..., session_id=..., run_id=..., agent_id=...)` | `build_session_context(session_id, agent_id=..., run_id=...)` |
| `runtime_context.project_root` | `session_ctx.cwd` |
| `runtime_context.session_id` / `run_id` / `agent_id` | `SessionContext` 同名字段 |
| `runtime_context.with_run_id(x)` | `dataclasses.replace(session_ctx, run_id=x)` |
| 传给 Skill catalog 的 `workspace_id` / `project_id` / `project_root` 实参 | 删除实参；第 20.3 项删除形参，`catalog_for` 只保留 `refresh` |
| Memory 调用中的 `workspace_id` / `project_id` 实参 | 删除实参，使用 Memory 模块默认值（tenant 全局） |
| `RunAuthorization` 的 `workspaceId` / `projectId`、Trace / 初始事件中的同名字段 | 删除字段 |
| `get_or_create_agent(project_id=...)` | 删除形参 |
| `load_subagent_catalog(workspace_dir=runtime_context.project_root)` | `workspace_dir=session_ctx.cwd` |
| `scope_guardrails_to_project(g, root)` | 删除调用，文件范围由 §16.4 决定 |
| `project_files.py` 中的 Session 文件函数 | 移到 `src/sessions/files.py`，删除 `project_root` 形参 |

涉及模块（第二轮复核时统计）：`agents/session_context.py`、`agents/factory.py`、`runtime/authorization_resources.py`、`subagents/runtime_context.py`、`group_studio/service.py`、`group_studio/worker_runtime.py`、`scheduler/chat_bridge.py`、`scheduler/service.py`、`server/routes/chat/{run_context,react_runtime,completion,leader_execution,initial_events,scheduled_launch,background_runs}.py`、`server/routes/chat/handlers/send.py`、`team_runtime/service.py`、`skills/registry.py`、`memory/retrieval.py`。

## Risks / Trade-offs

- [多个 Session 指向同一外部目录，用户文件仍可能互相影响] → 明确展示目录授权，内部状态严格隔离；Run snapshot 记录路径版本，文件写操作继续审批和审计。
- [去掉 Project 后缺少多会话共享资源集合] → 共享能力放全局 catalog；需要跨会话复用文件时显式选择同一目录或引用 Artifact，不引入隐藏 owner。
- [Workspace 合并时 Agent/MCP ID 冲突] → 内容相同才自动合并；其他冲突阻止自动授权并提供映射报告。
- [跨卷复制大量历史 Session 文件耗时] → 后台分批、可恢复 manifest、按 Session 校验；切换前旧源保持只读可用。
- [Job firing 与 Session 删除竞争] → ticker lease + 删除前依赖门 + 数据库事务；active Job 默认阻止删除。
- [Run 归属 owner Session 后污染 Chat] → scheduled 消息只存在 Run/Trace，不追加普通 Chat 主消息流。
- [API 迁移期间前后端版本不一致] → capability/version endpoint、deprecation header、先发布兼容后端再切前端，最后移除旧字段。
- [普通 Chat 的 Skill 描述泄漏到 scheduled firing，模型重复调用 Skill] → Runtime-specific toolkit mode、scheduled 专用模板、构造阶段关闭内置 viewer、cache key 隔离和 schema snapshot 测试。
- [模型伪造 required_skills 或仅传 Skill 名称导致冻结不一致] → catalog 输出稳定 ID，manifest 只由 ConfirmJob 服务端生成，旧字段只读兼容并做不一致拒绝测试。
- [hosted 路径授权扩大] → hosted 从服务端目录枚举创建 grant，拒绝客户端绝对路径，Docker 挂载只来自 snapshot。
- [回滚后新 Session 数据旧版本无法识别] → migration backup 同时生成旧版可导入清单；切换窗口内不清除旧表/目录。
- [Session 基础设施完成但未接入生产调用链，测试仍全绿] → 按 §15 以生产调用点和全新 tenant 端到端用例验收；legacy fixture 只供迁移测试显式使用。
- [先删除 default bootstrap、后切换 Runtime，导致全新 tenant 无法发送消息] → Phase 7 设前置门：Phase 3/4 验收证据齐全前不得删除 bootstrap；若已删除，优先补完 Runtime 切换，而不是恢复 default Project。
- [修补旧 `RuntimeContext` 代替切换入口，造成两套资源来源不一致（第二轮复核实际发生）] → §16.1 规定每个关注点只有一个函数；第 20 节删除旧模块前，import 棘轮测试只允许清单缩小。
- [迁移启动门失败导致历史对话暂时不可继续] → 失败时 tenant 保持未切换，只拒绝未迁移的历史 Session（`session_not_migrated`），新 Session 照常可用；修复后重跑 CLI。
- [方案触及当前其他进行中的 OpenSpec change] → 合并时以 SessionContext 为后续基线，permission-system-v2 的审批 snapshot 和 Skill 文件加载保留能力语义，仅替换归属字段。

## Migration Plan

### Phase 0: Inventory and guardrails

1. 固定旧版写入清单和字段使用矩阵，增加禁止新增 Workspace/Project 依赖的 import/AST 测试。
2. 对每个 tenant 生成只读 inventory、备份和冲突预检；有冲突时不写新事实。
3. 建立迁移 feature flag 与 schema version。flag 只决定启动门（§16.6）和未迁移 Session 准入（§16.5），不切换 Runtime 的读路径。

### Phase 1: Add Session model and roots

1. 扩展 Session schema、directory grants、resource grants、Run snapshots 和迁移记录。
2. 引入 `SessionContext`、Session path service 和 Session ownership 查询。
3. 新建 Session 的接口接受旧客户端字段但不使用（`project_id` 非空返回 410 `legacy_scope_retired`，见 §16.2、§16.3），只写新字段和 tenant Session root。
4. 文件/路径 API 加 checksum、symlink、tenant 和 allowed-root 测试。

### Phase 2: Move global catalogs

1. 将 Agent、Skill policy、MCP、HTTP Tool 和 Job Agent 定义收敛到 tenant 全局目录。
2. 实施 ID/正文冲突规则和 source mapping；先更新 catalog 管理 API，再更新 Runtime resolver。
3. 移除全局管理 API 的 Workspace/Project 参数，保留只读兼容 adapter。

### Phase 3: Migrate Runtime ownership

1. Chat send/resume、Agent factory、权限、Context governance、Team、Subagent 和 Sandbox 改用 SessionContext。
2. 每个 Run 创建 snapshot；恢复路径只从 run/session 记录重建。
3. Trace、timeline、Memory 审计、通知和 Artifact 改按 session_id 查询。
4. 更新 Chat SSE 契约中受影响的 payload 字段，再切前端消费。
5. 验收门：Chat、群聊 Worker 和 scheduled firing 的生产调用链不再导入 `runtime_context` / `project_resources`；每个生产 Run 都有快照记录；全新 tenant 端到端用例通过（见 §15）。

### Phase 4: Migrate Jobs and group studio

1. 回填 Job owner Session；无来源 Job 建专用 `job-owner` Session。
2. firing 改为 owner Session 下 Run，迁移 Job 文件并停止创建 `sched-*` Session。
3. 将 group studio 和 Worker 文件/权限改用 owner Session；验证独立 API/SSE 不变。
4. 启用 Session 删除依赖门与 ticker 并发测试。
5. 为 Job Runtime 切换 `scheduled_preloaded` Skill mode：关闭 Skill viewer、使用 scheduled 专用工具描述、把 mode 纳入缓存键，并完成 Job 创建 → ConfirmJob → firing → 首次模型调用的端到端验证。
6. 对每次 firing 记录实际预加载 Skill ID/hash/Context 注入结果；验证 Skill 正文不进入 system prompt、普通 Chat 仍保留 Skill viewer、references/scripts 仍按权限访问。

### Phase 5: Frontend cutover

1. 移除 Workspace/Project selector、创建弹窗、store 和 hooks。
2. 重做新对话/Session working directory 入口和 Session 资源面板。
3. Chat、Jobs、Trace、Memory、通知、群聊、Skills/Agents/MCP/Tools 全部切新 API。
4. 桌面与 hosted 分别完成空用户、历史用户、多 Session、外部目录和无目录 QA。

### Phase 6: Tenant-by-tenant cutover

1. 停止目标 tenant 新 Run，等待/中断 active Run，暂停 ticker lease。
2. 运行文件复制、数据库回填、资源映射和全量验证。
3. 写 `complete.json` 后把该 tenant 置为 cutover（`set_migration_flag(True, schema_version_value=1)`），恢复 ticker。执行入口见 §16.6：每次启动自动执行，也可用 CLI 手动执行。
4. 观察错误率、missing owner、路径拒绝、Job failure 和文件校验指标。失败时 tenant 保持未切换：新 Session 照常运行，未迁移历史 Session 拒绝新 Run（§16.5），修复后重跑。

### Phase 7: Remove legacy behavior

1. 全部 tenant 稳定、且 Phase 3 第 5 项验收门与 §15 的全新 tenant 用例通过后，停止旧 API 和旧字段写入，删除 default Workspace/Project bootstrap。
2. 删除 Project catalog 扫描、binding、`default_project_root()` 和外部 `.agent-workstation` 写路径。
3. 经过一个发布周期和用户备份确认后，另行迁移删除旧表/列；源目录只提供显式清理工具，不自动递归删除用户 Project。
4. 更新 AGENTS、架构、文件 catalog、Memory、Job、群聊文档和安装/卸载说明。

### Rollback

切换期回滚通过 `python -m src.session_migration rollback` 把 tenant migration flag 置回 false，并生成旧版可导入副本，再回到旧版本运行。新版本不保留旧读路径（§13）。旧表、Project/Workspace 定义和外部目录均未删除，新版本也不向旧字段写入非空值。新 Session root、grants、Run snapshots 与文件 manifest 作为新增数据保留。需要回退旧版本时，先停止服务和 ticker，使用 backup/mapping 生成旧版可识别的只读导入副本；禁止让新旧版本同时写同一 Job 或 Session。
