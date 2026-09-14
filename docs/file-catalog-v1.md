# 系统定义文件化 — 可执行规格

> 状态：已确认，第一至四期已落地。原文即实现合同。
> 若审核改了第 2 节锁定决策，先改本文再写代码，禁止实施时自行改布局或再引入平行工具。

本文是实现合同，不是方向稿。出现「必须 / 禁止 / 不得」的句子，实施时不得简化或另做一套。

---

## 1. 产品合同

### 1.1 模型只面对一种方案

模型了解或修改系统定义时，**只允许**使用现有文件工具（Read / Write / Edit / Glob / Grep）操作约定目录中的文件。

禁止：

- 新增 `ListAgents`、`GetAgentConfig`、`UpdateAgent`、`ReloadCatalog`、`ListProjectCapabilities` 或任何与文件平行的系统 CRUD 工具
- 让模型走 HTTP `/api/agents` 当「另一种改法」
- 页面写 SQLite、模型写文件的长期双权威
- 把 Bash 当作改 catalog 的官方方法（Bash 不得把 catalog 根当 cwd，也不得因此扩大 shell 范围）

允许保留的工具（不是改系统定义的入口）：AskUser、FinalAnswerStart、DelegateTask、Plan Mode、Team/群聊控场、Read/Write/Edit/Glob/Grep/Bash/HttpRequest/WebFetch/MCP/知识库。第四期后禁止再向模型注入 `ScheduleJob` / `PauseJob` / `ListJobs` / `ConfirmJob` 作为改 Job 定义的平行入口。

### 1.2 了解系统 vs 修改系统

| 意图 | 唯一做法 |
|---|---|
| 了解当前有什么 | Read 当前 Project 的 `.agents/INDEX.md` |
| 修改持久 Agent | Edit/Write Workspace catalog 里对应 `profiles/<id>.md` |
| 修改定时任务 | Edit/Write `.agents/jobs/<id>.md`（项目目录或 catalog_root） |
| 学会约定 | 读内置 Skill `yinli-catalog`（说明书，不是 API） |
| 定义坏了 | 再 Read INDEX 的「解析错误」段，改文件，不要删库 |

### 1.3 人与页面

HTTP `/api/agents` 与 Agents 页必须成为**同一棵文件树的门面**。保存、删除、导入、智能创建最终都写 profile 文件。列表从扫描结果来。禁止再 `upsert_agent` 用户正文。

---

## 2. 锁定决策（实施按此执行）

审核若推翻，改本节后再开发。

| ID | 锁定值 | 含义 |
|---|---|---|
| D1 | Workspace 数据目录为持久 Agent 权威 | 跨 Project 共享保持不变，文件工具增加**受信任 catalog 根** |
| D2 | 目录名 `profiles/` | 禁止把持久 Chat Agent 放进 `.agents/agents/`（那是 Subagent 兼容扫描路径） |
| D3 | ScheduleJob 第一期不动 | 第四期已把 Job 定义收到文件并撤回模型侧平行工具；第一期禁止顺手改调度工具 |

系统 Agent 身份仍由代码定义：`default`、`system-websearch-job`。`group-studio-host` 继续不进 Agents catalog。

---

## 3. 路径与文件名（一字不差）

以下路径全部 `Path.resolve()` 后使用。`USER_DATA_ROOT` 沿用 `src.config.runtime_paths.USER_DATA_ROOT`。

### 3.1 Workspace catalog 根（权威）

```text
<USER_DATA_ROOT>/workspaces/<workspace_id>/.agents/
  profiles/<agent-id>.md
```

- `<workspace_id>` 与 SQLite `workspaces.id` 一致，默认 `"default"`
- 注意：已有 `USER_DATA_ROOT/workspace`（单数，默认 Project 根）。本规格用 **`workspaces`（复数）**，禁止写成 `workspace/`
- `ensure_runtime_directories()` 必须一并创建 `USER_DATA_ROOT/workspaces`

系统 Agent 的**用户覆盖**（可改提示词/绑定，不可改身份）固定放在：

```text
<USER_DATA_ROOT>/workspaces/_system/.agents/profiles/default.md
<USER_DATA_ROOT>/workspaces/_system/.agents/profiles/system-websearch-job.md
```

`_system` 不是 SQLite Workspace。禁止在 `workspaces` 表插入 `_system`。

### 3.2 Project 生成物

```text
<project_root>/.agents/INDEX.md
<project_root>/.agents/.gitignore
```

`.gitignore` 必须至少包含一行（若文件已存在且已有该行则不重复写）：

```text
INDEX.md
```

禁止把 profile 权威副本再写进 Project `.agents/profiles/`（那会双权威）。Project 树第一期只生成 INDEX。

### 3.3 禁止使用的路径

| 路径 | 原因 |
|---|---|
| `<project>/.agents/agents/*.md` | Subagent 兼容扫描，见 `SUBAGENT_COMPAT_PROJECT_DIRS` |
| `<project>/.agents/subagents/` | 已有 Subagent 主目录 |
| `<USER_DATA_ROOT>/data/agents.json` | 旧 json_store 遗物，迁移后只读不写 |
| 任意 `models.json`、`data/secrets/` | 密钥 |

### 3.4 文件名规则

- 文件名必须是 `<id>.md`，与 frontmatter `id` 完全一致
- `id` 必须匹配：`^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`（覆盖现有 UUID 和模型起的短名）
- 禁止：空格、`.`、`/`、中文文件名、大小写混用冲突（同一目录按 Unicode NFC 规范化后大小写不敏感视为冲突：Windows 上必须在扫描时用 `casefold` 查重）
- 忽略：点文件、`*.tmp`、`*.bak`、非 `.md`

API 新建 Agent：`id = str(uuid.uuid4())`，与今天一致，文件为 `<uuid>.md`。  
模型新建：frontmatter `id` + 文件名，例如 `researcher.md`。  
迁移：保留 SQLite 原 `id`（多为 UUID），避免 `chat_sessions.agent_id` 失效。

### 3.5 大小与编码

- 编码：UTF-8，无 BOM
- 单文件上限：256 KiB（含 frontmatter）。超过则解析失败，不截断入库
- YAML：只允许 `yaml.safe_load`；结果必须是 `dict`

---

## 4. Profile Markdown 规格

### 4.1 完整例子（chat）

```markdown
---
id: researcher
name: 研究助手
role: chat
description: 检索与综述
avatar: ""
skills: []
mcp: []
knowledge_tools: []
tags: []
max_react_iters: 40
guardrails:
  filesystem_scope: ""
  shell_blacklist:
    - "rm -rf /"
    - sudo
  network_enabled: true
  human_approval: false
  tool_permission_mode: accept_edits
  tool_permission_profile: confirm_destructive
  execution_backend: local
  docker_image: python:3.11-slim
  persistent_sandbox: false
  timeout_seconds: 300
job_toolkit:
  required: []
  optional: []
created_at: "2026-09-10T00:00:00+00:00"
updated_at: "2026-09-10T00:00:00+00:00"
---

你是研究助手。用中文回答。
```

### 4.2 字段表

| 字段 | 必须 | 默认 | 规则 |
|---|---|---|---|
| `id` | 是 | 无 | 与文件名一致；系统 id 仅允许出现在 `_system` 目录 |
| `name` | 是 | 无 | 1–50 字符，strip 后非空 |
| `role` | 是 | 无 | 只允许 `chat` 或 `job` |
| `description` | 否 | `""` | 最长 200 |
| `avatar` | 否 | `""` | 原样保存，不校验 URL |
| `skills` | 否 | `[]` | 字符串 id 列表，去空白，保序去重 |
| `mcp` | 否 | `[]` | 对应现 API `mcpTools` |
| `knowledge_tools` | 否 | `[]` | 对应 `knowledgeTools` |
| `tags` | 否 | `[]` | 字符串列表 |
| `max_react_iters` | 否 | `40` | 整数 1–`MAX_REACT_ITERS`（当前 40 上限以 `runtime_types` 为准） |
| `guardrails` | 否 | 与现 `Guardrails()` 默认值相同 | 见 4.3 |
| `job_toolkit` | `role=job` 时是 | 空对象 | 复用 `parse_job_toolkit` + `validate_job_agent_config` |
| `created_at` | 否 | 文件出生时间 UTC ISO | 只在首次写入时设，之后 API 更新不得改 created_at |
| `updated_at` | 否 | 文件 mtime UTC ISO | 每次成功写文件更新 |

禁止写入文件的字段（读到则忽略并在 INDEX 记 warning）：

- `planConfig` / `plan_config`（旧 Plan Runtime 已下线）
- `modelConfig.primary` / `temperature` / `top_p` / `max_tokens`（模型在 `models.json`）
- `status`（响应里恒为 `"idle"`）
- `workspaceId`（由目录决定，不写进 YAML，避免和文件夹不一致）
- `systemManaged`（由 id 派生）

未知 YAML 键：忽略，INDEX 记 `unknown_keys`，不失败。

### 4.3 guardrails 子字段

键用 **snake_case**（文件里不用 camelCase）。缺省等于当前 `Guardrails` 默认值。

`filesystem_scope` 空字符串表示「未设置，运行时用 Project root」。文件里禁止写 `/`。若写了 `/`，加载时按空字符串处理并 warning（与现 `normalize_guardrails` 一致）。

`role=job` 时加载后仍必须走 `_apply_job_agent_fields`：强制 `human_approval=false`、`tool_permission_profile=trusted_tools`、`tool_permission_mode=bypass`，MCP/HTTP/Skill 以 toolkit 为准覆盖列表。

### 4.4 系统 Agent 覆盖文件的额外禁令

`default.md`：

- 禁止 `role: job`
- 禁止删除该文件来「去掉默认助手」；文件不存在 = 使用代码内置，不视为删除系统 Agent
- 可以改 `system_prompt`、`skills`、`mcp`、`knowledge_tools`、`guardrails`、`max_react_iters`

`system-websearch-job.md`：

- 禁止 `role: chat`
- 禁止把 `job_toolkit.required` 改到不含 `WebFetch` 与 `HttpRequest`
- 网络必须保持开启；加载时若被改关，强制改回并 warning

覆盖文件与代码内置的合并算法：

1. 以代码 `_build_default_agent()` / `_build_default_websearch_job_agent()` 为底
2. 用覆盖文件的允许字段 overlay
3. 再跑系统禁令
4. `workspace_id` 在 list-by-workspace 时对系统 Agent 视为对每个 workspace 可见，对象上仍报 `workspaceId="default"`（与今天 list 过滤一致）

### 4.5 映射到现有 `AgentResponse`

实施时写两个纯函数（禁止把解析逻辑散落在路由里）：

```text
backend/src/catalog/profiles.py
  parse_profile_markdown(text: str, *, path: Path, workspace_id: str) -> ProfileParseResult
  dump_profile_markdown(agent: AgentResponse) -> str
```

`AgentResponse` 字段对应：

- `mcpTools` ← `mcp`
- `knowledgeTools` ← `knowledge_tools`
- `modelConfig` ← 仅 `{ maxReactIters }`；其它历史键不读不写。API 若仍收到多余键，丢弃
- `planConfig` ← 永远默认空对象，不写文件
- `sourcePath` ← 文件绝对路径（**新增**，见 8.2）
- `sourceKind` ← `profile` 或 `system` 或 `system_overlay`

正文（frontmatter 后）= `system_prompt`。正文前后只 strip 一次尾部多余空行，保留用户段落格式。dump 时正文末尾恰好一个 `\n`。

### 4.6 原子写盘

```text
1. 校验 dump 出的文本能 round-trip parse
2. 写入同目录 `.<id>.md.tmp`
3. os.replace(tmp, final)   # POSIX/Windows 都用 replace
4. 失败则删除 tmp，抛错，不得留下半截正式文件
```

禁止先删正式文件再写。

---

## 5. Catalog 模块（新包，唯一存储实现）

### 5.1 文件清单（第一期必须新增）

```text
backend/src/catalog/__init__.py          # 只导出稳定 API，见 5.2
backend/src/catalog/paths.py
backend/src/catalog/models.py
backend/src/catalog/profiles.py
backend/src/catalog/loader.py
backend/src/catalog/index.py
backend/src/catalog/migrate_agents.py
```

禁止把扫描/解析写进 `routes/agents.py`。路由只调 catalog。  
禁止 `catalog` import `src.server.routes.*`（打破现有「调度 import routes.agents」的方向：调度改为 import catalog）。

### 5.2 稳定 Python API（调用方必须改到这里）

```python
# 替换所有 get_registered_agent / list_registered_agents
def get_registered_agent(agent_id: str) -> AgentResponse | None: ...
def list_registered_agents() -> list[AgentResponse]: ...
def list_workspace_agents(workspace_id: str) -> list[AgentResponse]: ...
def save_agent(agent: AgentResponse) -> AgentResponse: ...
def delete_agent(agent_id: str) -> None: ...  # 系统 Agent 抛 PermissionError
def invalidate_catalog() -> None: ...
def catalog_fingerprint() -> str: ...
def refresh_project_index(project_root: Path, *, workspace_id: str) -> Path | None: ...
def workspace_catalog_root(workspace_id: str) -> Path: ...
def system_overlay_root() -> Path: ...
```

`routes/agents.py` 里现有的 `get_registered_agent` / `list_registered_agents` 必须变成对 catalog 的薄包装（或删除后改所有 import）。第一期 **必须改 import 的文件**（不得继续从 sqlite 字典读用户 Agent）：

- `backend/src/server/routes/agents.py`
- `backend/src/server/routes/agent_builder.py`（可继续调 `create_agent` 路由函数，只要后者写文件）
- `backend/src/server/routes/chat/run_context.py`
- `backend/src/server/routes/chat/handlers/runs.py`
- `backend/src/server/routes/chat/handlers/resume.py`
- `backend/src/scheduler/feasibility.py`
- `backend/src/scheduler/firing_toolkit.py`
- `backend/src/scheduler/chat_bridge.py`
- `backend/src/scheduler/capability_catalog.py`
- `backend/src/scheduler/service.py`
- `backend/src/tools/scheduled_job_tools.py`

测试里 `monkeypatch("src.server.routes.agents.get_registered_agent", ...)` 继续有效的前提是包装函数留在 `routes.agents`。**必须保留** `src.server.routes.agents.get_registered_agent` 这个符号，内部转调 catalog，以免 10+ 个测试和调度补丁点同时爆炸。允许测试继续 patch 这一层。

### 5.3 `paths.py` 必须实现

```python
WORKSPACES_DIRNAME = "workspaces"
SYSTEM_WORKSPACE_DIRNAME = "_system"
AGENTS_DIRNAME = ".agents"
PROFILES_DIRNAME = "profiles"

def workspace_catalog_root(workspace_id: str) -> Path:
    # USER_DATA_ROOT / workspaces / workspace_id / .agents

def profiles_dir(workspace_id: str) -> Path:
    # catalog_root / profiles

def system_profiles_dir() -> Path:
    # USER_DATA_ROOT / workspaces / _system / .agents / profiles

def project_index_path(project_root: Path) -> Path:
    # project_root / .agents / INDEX.md
```

`workspace_id` 必须匹配 `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`，否则抛 `ValueError`。禁止用用户输入直接拼接未校验的 `..`。

### 5.4 `loader.py` 扫描算法

进程内缓存：`_cache: dict` 含 `fingerprint` 与 `agents: dict[str, AgentResponse]`、`errors: list`、`warnings: list`。

fingerprint 计算：

```text
对每个已扫描文件：realpath + size + mtime_ns + sha256[:16]
加上系统 Agent 代码版本常量（DEFAULT_AGENT_PROMPT_VERSION_TAG 与 DEFAULT_WEBSEARCH_JOB_AGENT_VERSION_TAG）
排序后 sha256 取 16 位
```

加载步骤（每次 `ensure_loaded()`）：

1. 若 fingerprint 未变，返回缓存
2. 扫描 `USER_DATA_ROOT/workspaces/*/ .agents/profiles/*.md`（含 `_system`）
3. 跳过不是目录的 `workspaces` 子项
4. 解析；失败记 error，不进入 `agents`
5. id 与文件名不一致 → error，不进入
6. 全局 id 冲突（跨 workspace 或与系统 id 冲突）：
   - 系统 id 出现在非 `_system` 目录 → error，该用户文件不进入
   - 两个用户文件同 id → 路径排序后前者进入，后者 shadowed error
7. 合并系统 Agent：代码内置 + `_system` 覆盖
8. 更新缓存

禁止启动时全量 watch 线程。只在 list/get/save/delete/Run 开始时 `ensure_loaded()`。

`save_agent`：写文件 → `invalidate_catalog()` → `invalidate_agent(agent_id)`（factory 缓存）→ 必要时 `_pause_jobs_*`（仍由 routes 层在更新成功后调用，catalog 不依赖 scheduler）。

`delete_agent`：系统 id 抛错；删文件；pause jobs；invalidate。

### 5.5 INDEX.md 生成

`refresh_project_index(project_root, workspace_id)`：

1. `ensure_loaded()`
2. 渲染 Markdown（模板见 5.6）
3. 确保 `<project>/.agents/` 存在
4. 合并 `.gitignore` 的 `INDEX.md` 行
5. 原子写入 `INDEX.md`
6. 若 `project_root` 不可写：返回 `None`，不抛到 Chat；记 warning 日志。Run 不得因此失败

调用点（第一期全部都要接）：

- `build_chat_run_context` 在 readiness 之前或之后均可，但必须在创建 Agent 工厂之前，且失败静默
- `save_agent` / `delete_agent` 成功后：若调用方持有 `project_root` 则刷新；HTTP API 无 Project 时**不写 INDEX**（下一轮 Chat 会写）
- 禁止每秒轮询写 INDEX

INDEX 对模型展示的 profile 路径必须是 **Read 工具能用的绝对路径**（LocalWorkspace 下即 host realpath）。

### 5.6 INDEX.md 固定模板

生成器必须输出下列标题结构（中文标题一字不差），便于模型按标题找：

```markdown
# YINLI Catalog

- workspace: <id>
- project: <id>
- project_root: <abs>
- catalog_root: <abs>
- generated_at: <ISO>
- fingerprint: <hex>

## 如何操作系统

- 了解系统：只读本文件。
- 修改 Agent：编辑「持久 Agent」表中的 path。
- 修改定时任务：编辑或新建 `.agents/jobs/<id>.md`；desired_status=active 才会调度。
- 不要修改 models.json、密钥目录、本 INDEX（下次运行会覆盖）。
- 不要把 Chat Agent 写到 .agents/agents/（那是 Subagent）。

## 系统 Agent（身份只读）

| id | name | role | overlay_path | notes |
| ... | 无覆盖时 overlay_path 写 `（代码内置，创建 overlay 即生效）` |

## 持久 Agent

| id | name | role | path |
| 仅当前 workspace 的用户 profiles |

## 解析错误

- `path`: message
无错误时写 `（无）`

## Skills / MCP / HTTP 工具 / Jobs

后续期已落地：INDEX 列出 Skills、MCP、HTTP 工具和 Jobs。改这些定义只 Edit 对应文件。
```

第一期 INDEX **不**列出 Skill/MCP 详情（避免假装已经文件化）。第二至四期已补章节。

---

## 6. 文件工具受信任根（D1 的实现代价）

### 6.1 现状问题

`resolve_workspace_path` 只有一个 `scope_dir`。catalog 在 `USER_DATA_ROOT/workspaces/...`，一般不在 Project root 内。`Path.resolve()` 会拆穿 symlink，因此禁止用「项目内 symlink 指到 catalog」绕过。

### 6.2 必须改的函数

`backend/src/tools/scoped_paths.py`：

```python
def resolve_workspace_path(
    path: str | None,
    *,
    base_dir: str | None = None,
    scope_dir: str | None = None,
    extra_scopes: list[str] | None = None,
) -> str:
```

规则：

1. `scope` = resolve(`scope_dir` or `base_dir` or WORKSPACE_DIR)
2. `base` = resolve(`base_dir` or scope)，且 base 必须在 scope 内（保持现状）
3. 目标 `resolved` 计算方式不变（相对路径相对 base）
4. **允许**当且仅当：`resolved.is_relative_to(scope)` **或** 存在某个 extra，使 `resolved.is_relative_to(Path(extra).resolve())`
5. extra 必须是已存在的目录；创建 profile 时 catalog 应先 `mkdir`
6. extra 禁止为 `/`、禁止为 `$HOME`、禁止为 `USER_DATA_ROOT` 本身。只允许：
   - `workspace_catalog_root(workspace_id)`（即 `.../workspaces/<id>/.agents`）
   - `system_overlay_root()`（`.../workspaces/_system/.agents`）
7. 相对路径若第一段不是绝对路径，仍相对 **project base** 解析，不能把 extra 当第二 cwd。模型应使用 INDEX 里的绝对路径编辑 profile
8. `resolve_existing_workspace_path` / `resolved_tool_input` 同样传入 `extra_scopes`

### 6.3 必须改的工具类

`ScopedRead` / `ScopedWrite` / `ScopedEdit` / `ScopedGlob` / `ScopedGrep`：

- `__init__(..., extra_scopes: list[str] | None = None)`
- 所有 `_resolve_*` 传入 `extra_scopes`

`GuardedBash`：**禁止**接收 extra_scopes，cwd 仍是 project/session workspace。禁止为了改 Agent 去扩 shell。

### 6.4 工厂注入

`_create_default_toolkit` / `get_or_create_agent` / `MainAgentRuntimeSpec` 增加：

```python
extra_filesystem_scopes: list[str] = []
```

`run_context.build_chat_run_context` 在组装 spec 时填：

```python
extra = [
  str(workspace_catalog_root(ctx.workspace_id)),
  str(system_overlay_root()),
]
```

仅 Chat 单 Agent / Team Member / 群聊 Worker 这些会改用户定义的会话需要 extra。  
**禁止**给 memory 提炼 Agent、ReadinessAgent、SkillEvolution 草稿 Agent 加 catalog 写权限（它们 `include_default_tools=False` 或自有工具，保持现状即可，不要新传 extra）。

`build_runtime_agent_cache_key` 必须加入 `catalog_fingerprint()`，否则改文件下一轮仍用旧 prompt 缓存。

### 6.5 Docker（第一期明确行为，禁止临场发挥）

现有：`DockerWorkspace` + Skills 必须 `persistent_sandbox`；skill seed 到 `/workspace/skills`。

第一期 Docker：

- **不**把 host catalog bind 进容器（上游 DockerWorkspace 没有稳定的额外 mount API，禁止为这件事改 AgentScope）
- 若 `execution_backend=docker`：在 INDEX 的「如何操作系统」增加一行：`当前是 Docker 沙箱，持久 Agent 文件只读投影，修改请改用本地执行或到 Agents 页面`
- 将当前 workspace 的 profiles **只读复制**到持久 sandbox 的 `/workspace/.agents/profiles/`（若该次运行启用 persistent_sandbox）；不把容器内对副本的修改同步回 host
- 非持久 Docker：不 seed profiles，INDEX 仍尝试写在 **host project_root**（模型在容器内可能读不到）。此为已知限制，第一期不修

LocalWorkspace 是第一期验收主路径。Docker 只加上述只读投影测试，不承诺容器内 Edit 回写。

---

## 7. 迁移（一次性，可重复执行但禁止双写）

### 7.1 标记

`json_store` namespace=`catalog` key=`agent_migration_v1`

```json
{
  "schemaVersion": 1,
  "status": "done",
  "migratedAt": "<ISO>",
  "exported": 12,
  "deletedRows": 12,
  "overlayDefault": true,
  "overlayWebsearch": false
}
```

`status`：`done` | `failed`。只有 `done` 之后加载路径才只读文件。

### 7.2 算法（`migrate_agents.py`）

在应用启动、`_load_agents()` 原位置调用 `ensure_agent_file_catalog()`：

```
if marker.status == done:
    只从文件+代码加载；return
try:
    读取 sqlite agents 全部行
    对每个非系统行：
        校验/补默认值 → dump markdown → 写入对应 workspace profiles/<id>.md
        若目标文件已存在：比较 fingerprint；相同跳过；不同则写 `<id>.conflict-<utc>.md` 并 warning，权威保留已有文件（防止二次迁移覆盖模型已改文件）
    对系统行 default / system-websearch-job：
        若与代码模板在 prompt/skills/mcp/guardrails/max_react_iters 上有差异
        → 写 _system overlay
        否则不写 overlay
    再从文件加载，抽查：每个导出 id 都能 get_registered_agent
    抽查失败 → marker=failed，抛启动错误（宁可起不来也不静默丢 Agent）
    抽查成功 → DELETE FROM agents（整表用户+系统行；系统改由代码提供）
    写 marker done
except:
    marker=failed（若能写）
    raise
```

禁止：导出后仍 `upsert_agent`。  
禁止：marker=done 后读 `agents` 表。  
`resource_catalog_records.upsert_agent` 可保留函数但生产路径不得调用；加注释。启动后若误调用，应断言 marker 未 done 或直接 raise。

开发库可重复跑：marker=done 时跳过。测试用临时 USER_DATA_ROOT + 临时 sqlite。

### 7.3 失败回滚

marker≠done 时，加载逻辑保持 **旧 sqlite `_load_agents`**，文件即使写了一半也不作为权威。这样失败可修脚本重跑。

第一期禁止做自动「从文件导回 sqlite」。

---

## 8. HTTP API 行为（路径不变，语义绑文件）

现有路由前缀 `/api/agents` 不变。

| 方法 | 行为 |
|---|---|
| GET `?workspaceId=` | `list_workspace_agents` + 系统 Agent；不含解析失败文件 |
| GET `/{id}` | 成功返回；解析失败返回 **409** `{detail: "agent_profile_invalid: ..."}`；没有文件且非系统 **404** |
| POST | uuid 新文件；`workspaceId` 决定目录；201 |
| PUT `/{id}` | 读现有（含系统 overlay）→ 合并 → 系统禁令 → 写文件（系统写 overlay 路径） |
| DELETE `/{id}` | 系统 403；用户删文件 204 |
| GET `/{id}/export` | 仍返回 `agent-workstation.agent.v1` JSON（内存对象，不改 schema_version） |
| POST `/import` | 按现冲突策略分配 id/name，**写文件**不写库 |

`remove_mcp_tools_from_agents` / `remove_skills_from_agents` / `remove_knowledge_tools_from_agents`：遍历 catalog 用户 Agent + 系统 overlay，改列表后 `save_agent`，并保持现有 pause Job 副作用。必须在 mcp/skills/tools 删除路由里继续调用。

智能创建 `agent_builder.py`：确认后仍调 `create_agent`，不得自己 upsert sqlite。

### 8.1 列表不返回的东西

解析失败的 profile 只出现在 INDEX 错误段，不出现在 GET `/api/agents`。前端不会凭空多出坏卡片。

### 8.2 `AgentResponse` 新增字段（向后兼容）

```python
source_path: str = Field(default="", alias="sourcePath")
source_kind: str = Field(default="profile", alias="sourceKind")
```

前端 `types/agent.ts` 增加可选字段，页面可以不展示。禁止因此改编辑器布局（第一期）。

---

## 9. 内置 Skill `yinli-catalog`

### 9.1 包位置

源码：`artifacts/skills/yinli-catalog/SKILL.md`  
启动 seed：复制到 `SKILL_PACKAGES_ROOT/yinli-catalog/`（若不存在或源更新则覆盖；这是系统包，允许覆盖用户同名目录——**禁止用户注册同名 `yinli-catalog`**，`register_user_skill` 撞名 409）

### 9.2 运行时注入

第一期：`run_context` 在 `select_runtime_skills` 之后，若本轮是单 Agent Chat（非 scheduled unattended、非 Team 综合），把该 Skill **追加**进 `used_skills` / `skill_paths`，即使 profile.skills 未列出。

目的：模型不依赖用户先绑定才能学会改文件。

群聊 Host（无默认工具）不注入。Job unattended 不注入（无人改 catalog）。

### 9.3 SKILL.md 正文必须写清

- INDEX 路径：当前项目 `.agents/INDEX.md`
- profiles 路径：INDEX 里的 `catalog_root` + `/profiles/`
- 新建 Agent 的 YAML 最小模板（id/name/role/正文）
- 禁止写入 `.agents/agents/`
- 禁止改 `models.json` 和密钥
- 改完无需调用特殊工具，下一轮自动生效
- Docker 限制一句话

---

## 10. 系统提示 / 默认 Agent 文案

禁止把整份目录说明书复制进每个 Agent 的 `system_prompt`（那会和 Skill 双份）。默认助手提示词保持现网，不在第一期改 `DEFAULT_AGENT_FINAL_DELIVERY_RULES`。

INDEX + `yinli-catalog` 是唯一教材。

---

## 11. 第一期明确不改

- `skills` 表正文双写、MCP、HTTP、binding 表
- `ScheduleJob` 工具组、`scheduled_jobs` 表
- Chat SSE 契约、群聊、Memory、Plan middleware
- 前端 Agent 编辑器分区、绑定弹窗
- 把 `filesystem_scope` 扩成整个 `USER_DATA_ROOT`
- 给 Bash 加 extra_scopes
- 文件 watcher 守护进程
- 从 Project git 提交 Workspace profiles（它们在 userData，本来就不在用户项目仓库）

---

## 12. 第一期代码改动清单（实施按此勾）

### 12.1 新增

见 5.1，外加：

- `artifacts/skills/yinli-catalog/SKILL.md`
- `backend/test/test_catalog_paths.py`
- `backend/test/test_catalog_profiles.py`
- `backend/test/test_catalog_loader.py`
- `backend/test/test_catalog_migrate_agents.py`
- `backend/test/test_catalog_index.py`
- `backend/test/test_scoped_paths_extra_scopes.py`
- `backend/test/test_agents_file_catalog_api.py`（创建/更新/删除走文件、系统 Agent 403）

### 12.2 修改

- `backend/src/config/runtime_paths.py`：`WORKSPACES_CATALOG_ROOT = USER_DATA_ROOT / "workspaces"`，`ensure_runtime_directories` 创建它
- `backend/src/tools/scoped_paths.py` 及五个 Scoped* 工具
- `backend/src/agents/factory.py`、`runtime_types.py`（若 spec 类型在此）、`runtime_builder.py`、`run_context.py`、`runtime.py` 仅在需要传 extra_scopes 时
- `backend/src/server/routes/agents.py`：存储替换；保留 HTTP 与 `get_registered_agent` 符号
- `backend/src/server/main.py` 或 database 启动：调用 `ensure_agent_file_catalog()`
- `frontend/src/types/agent.ts`：可选 `sourcePath` / `sourceKind`
- `docs/file-catalog-v1.md`（本文定稿）
- `docs/README.md` 索引
- `docs/project-todo.md` 增加后续 2–4 期条目
- `AGENTS.md`：持久 Agent 权威改为文件；`get_registered_agent` 来自 catalog
- `docs/architecture-overview.md` 一小节

### 12.3 测试必须覆盖的断言

1. UUID 与短 id 都能 round-trip
2. 文件名与 id 不一致 → 不进列表 + INDEX 有错
3. 迁移后 sqlite `agents` 表为空，旧 id 仍能 get
4. 迁移时目标文件已存在且内容不同 → conflict 副本，不覆盖
5. 改 profile 后 cache key 变，下一轮 system_prompt 已变（可用 factory 缓存测）
6. extra_scopes 内绝对路径 Read/Write 成功；`USER_DATA_ROOT/models.json` Write 失败
7. Bash 不能依赖 extra_scopes
8. 系统 Agent 删除 403；改 default 角色为 job 被拒绝
9. list `workspaceId=a` 看不到 workspace b 的 profile，但看得到 default 系统 Agent
10. 跨 workspace 同 id 冲突：一份生效一份 shadowed
11. INDEX 不可写时 Chat `build_chat_run_context` 仍成功
12. `yinli-catalog` 出现在单 Agent skill_paths，不出现在 unattended denylist 路径
13. 现有 `test_default_agent.py` 行为保持：系统 Agent 各 workspace 可见
14. `monkeypatch get_registered_agent` 的调度测试仍可 patch 路由符号

跑：`uv run pytest` 相关文件 + 全量；前端 `npm run build`（类型可选字段不得打破现编辑器）。

---

## 13. 第二期：Skill 去双写（第一期禁止提前做，但规格已定以免跑偏）

实施第二期时另开 PR，仍遵守「模型只有文件」。

### 13.1 权威

- 包目录 `SKILL.md` 是正文权威
- 禁止再把完整正文写入 `skills.content`；该列可留空字符串
- 发现顺序与现 `discover_external_skills` 一致：项目 `.agents/skills` 默认启用；用户级默认禁用
- 原生 `SKILL_PACKAGES_ROOT` 与项目/用户外部包合并进同一 catalog 函数，放到 `src/catalog/skills.py`，`routes/skills.py` 变薄

### 13.2 项目内 Skill 默认真源

项目 `.agents/skills/<name>/` 对当前 Project **默认可用**，不再要求先 `PUT` binding。  
Workspace 级 / 用户级 Skill 仍需启用或 binding（与现外部用户级默认 `enabled=False` 一致）。

`project_resources.filter_ids` 对「来源为当前 project_root 下 `.agents/skills`」的 id 必须放行。

### 13.3 内置办公包

`artifacts/skills/word-documents` seed 到 `SKILL_PACKAGES_ROOT`，作为系统包，禁止删除。图表围栏约定作为独立 `yinli-charts` Skill 说明书，前端渲染不在第二期强制绑定。

### 13.4 INDEX 第二期增加章节 `## Skills`，列出 name、path、enabled、source。

---

## 14. 第三期：MCP / HTTP

- 定义文件：`<workspace_catalog>/.agents/mcp/<id>.yaml` 与 `tools/<id>.yaml`
- YAML **禁止**出现 `headers` 里的 Authorization、`auth_config` 明文；只允许 `auth_ref: secret:<id>`
- 现有行迁移时把密钥写入 secret_store，文件留 ref
- 模型可 Edit URL/描述/schema；缺密钥时运行失败信息与现网一致
- INDEX 增加 MCP/HTTP 节

---

## 15. 第四期：Job 定义

- 定义：`<workspace_catalog>/.agents/jobs/<job-id>.md` 或项目 `.agents/jobs/`
- 删除模型侧 `ScheduleJob`/`PauseJob` 作为平行入口：改定义 = Edit 文件
- `scheduled_job_tools.py` 从单 Agent extra_tools 移除
- Jobs 页 / REST 是文件门面：GET 展示文件定义 + sqlite 实例；改提示词/调度/Agent 写文件；确认/暂停/恢复/归档只改实例

### 15.1 权威划分

| 种类 | 权威 | 路径 |
|---|---|---|
| 定义 | Markdown | `.agents/jobs/<id>.md` |
| 运行实例 | SQLite `scheduled_jobs` | 见 15.2 |
| firing 历史 | SQLite `scheduled_job_runs` | 不变 |

禁止把 sqlite 定义列当编辑入口。get / list / firing 组装 `ScheduledJob` 时，若定义文件存在，**文件覆盖** name、summary、agent_id、frozen_prompt、schedule_*、timezone、skill_ids、tool_scope、missed/failure/delivery policy。sqlite 里同名列只是派生缓存，供 NOT NULL 与 UNIQUE(project_id, name) 继续工作，不得单独当权威。

不采用 `status.yaml`：桌面 ticker 已依赖 sqlite 到期扫描，实例留表。

### 15.2 实例字段表（sqlite `scheduled_jobs`）

| 字段 | 必须 | 含义 |
|---|---|---|
| `id` | 是 | 与定义文件名 / frontmatter `id` 一致 |
| `workspace_id` / `project_id` | 是 | 运行归属；与文件一致 |
| `status` | 是 | `pending_confirmation` / `active` / `paused` / `archived` / `completed` |
| `pause_reason` | 否 | `user` / `project_archived` 等 |
| `next_run_at` / `last_run_at` | 否 | UTC ISO |
| `consecutive_successes` / `consecutive_failures` | 是 | 连续计数 |
| `max_consecutive_failures` / `max_firings_per_day` / `required_successes` | 是 | 运行限额 |
| `capability_contract` | 是 | confirm 时冻结的能力合同 |
| `created_from_session_id` | 否 | 登记来源 |
| `model_config_id` | 否 | firing 用的模型快照 id |
| `created_at` / `updated_at` / `archived_at` | 是/否 | 实例时间戳 |

文件 frontmatter `desired_status: active|paused` 只表达「要不要进入调度」：`active` 时把 `pending_confirmation` confirm 为 `active`，或把用户暂停 resume；已 `active` 且文件改为 `paused` 则 pause。它不是 sqlite `status` 的拷贝。

### 15.3 定义文件字段

frontmatter：`id`、`name`、`agent_id`、`schedule_kind`、`schedule_expr`、`timezone`、`desired_status`、`summary`、`skill_ids`、`tool_scope`、`missed_policy`、`failure_policy`、`delivery_policy`、`task_intent`。正文 = `frozen_prompt`。单文件上限与 profile 相同（256 KiB）。解析失败记 INDEX，不删文件。

---

## 16. 并发、缓存、失败（第一期就必须遵守）

- 无文件锁。后写覆盖先写。API 与模型同时改同一 profile，后 `os.replace` 赢
- 不实现 OT/合并
- factory `_agent_cache` 必须在 save/delete/fingerprint 变化后对相关 runtime id 失效；最稳妥：`invalidate_agent` 现有按 agent_id 前缀不够（runtime id 含 hash）。**必须**在 catalog fingerprint 变化时清空整表 `_agent_cache`，或 cache key 已含 fingerprint 使旧 key 自然 miss。选后者（改 `build_runtime_agent_cache_key`）+ save 时 `invalidate_agent(id)`
- 解析错误不得删除用户文件
- 写盘失败返回 500，sqlite 不得作为 fallback 成功

---

## 17. 安全清单

- extra_scopes 白名单仅两条根，见 6.2.6
- 拒绝 catalog 根内的 symlink 指向根外：扫描与 Read 都 `resolve()` 后检查 `is_relative_to(extra)`
- profile 正文当提示词注入，不当 YAML 执行
- 禁止 INDEX 里打印 API Key
- 模型把 `role: job` 写进随意 profile 时，必须通过 `validate_job_agent_config`，失败则该文件不进入可用列表

---

## 18. 文档与提交

审核通过后、写代码前：

1. 把本文复制为 `docs/file-catalog-v1.md`（状态改为「已确认，实施中」）
2. `docs/README.md` 的「后续路线」增加一条链接
3. `project-todo.md` 写第 2–4 期，不把第 1 期当未设计项

提交顺序：

1. 一个 PR：文档 + catalog 内核 + 迁移 + agents API + extra_scopes + INDEX + yinli-catalog + 测试
2. 禁止把第 2 期 Skill 塞进同一 PR
3. 提交信息：`feat: load persistent agents from workspace profile files`

---

## 19. 验收（第一期完成的定义）

同时满足才算第一期完成：

1. 重启后 sqlite `agents` 表为空（或仅空表），用户 Agent 仍出现在页面，id 不变
2. 对应磁盘上能打开 `workspaces/<id>/.agents/profiles/<id>.md`
3. 页面保存会改该文件，不写 `upsert_agent`
4. 本地 Chat 里模型 Read `.agents/INDEX.md` 能看到该 path，Edit 提示词后下一轮行为改变
5. 没有新增系统 CRUD 工具
6. 系统 Agent 仍不可删、跨 Workspace 可见
7. 全量 pytest 绿；前端 build 过
8. `models.json` 与 secret 目录 Write 被拒

---

## 20. 实施顺序（写代码时严格按此，禁止先改前端）

1. `paths.py` + 单测
2. `profiles.py` codec + 单测
3. `scoped_paths` extra_scopes + 单测
4. `loader.py` + 迁移脚本 + 迁移测试（临时目录）
5. 接 `ensure_agent_file_catalog` 到启动；`routes.agents` 改存储
6. cache key + run_context INDEX + extra_scopes 注入
7. `yinli-catalog` seed + 注入
8. API 测试 + 修 `test_default_agent` 等
9. 文档 AGENTS.md / architecture-overview
10. 全量 pytest、前端 build

未完成 1–4 不得改 `routes/agents.py` 加载路径。
