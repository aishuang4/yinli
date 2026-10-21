# 引力 YINLI · 上下文归属与存储重构 — 设计方案与实施方案

版本：v1.6（2026-10-20），2026-10-21 起作为已落地的存储合同。
阶段 1–4 已在 `refactor/context-storage` 提交。阶段 5 删掉旧 JSON 导入、`migrate_agents` 和启动时的目录搬迁，并用 `backend/test/test_context_storage_ratchet.py` 挡住这些路径回流。`health` 没有锁等待或库体积指标，本阶段不拆 `jobs.db` / `audit.db`。封存提示、`yinli db reset --dev` 和发布说明已落地，见 `docs/context-storage-release-note.md`。
`openspec/changes/archive/2026-10-21-simplify-global-session-context` 与 `2026-10-21-unify-file-based-skill-loading` 不再作为实现依据。
相关前置调研：《定时任务技能调度调研报告》《开源 Agent 项目与会话存储设计对照调研》《Hermes 存储设计调研与融合方案》

---

## 0. 决策记录

| # | 决策 | 依据 |
|---|---|---|
| D1 | 删除 Workspace 层，租户（tenant）直接是顶层 | 8 个主流 Agent 无一在 Project 之上再设一层；托管已有 `tenants/<usr>/` |
| D2 | 保留 Project，定义为"用户命名的工作区 + 若干授权目录" | Hermes 桌面端 `projects.db`（`id/slug/name/primary_path/folders[]`）是同构先例 |
| D3 | Project 的 id 是由名字派生的 slug，不用 uuid；重名拒绝 | 用户要求；Hermes 同样 `slug` + 唯一 `name` |
| D4 | 不再有 Project 资源白名单（resource binding）。Project 只回答"允许读写哪些目录" | 资源可用性收敛为：全局启停 → Agent 上限 → 本轮/本 Job 显式选择 |
| D5 | 会话、Run、消息、Trace 的事实源是 SQLite（WAL），每租户一个 `state.db`；JSONL 只做导出物 | Hermes 从 JSONL 收敛到 SQLite 的理由（多来源写、全文搜、单一写者）对本系统同样成立；Trace 已在 SQLite |
| D6 | 不按 Project 分库；按聚合分库（会话 / 项目 / 任务 / 审计）可选 | 无业界先例；调度器扫描、FTS、迁移在分库后全部变复杂；Hermes 本身就是按聚合分库 |
| D7 | 定时任务定义与到期索引放租户级，Job 引用 Project 与 cwd | Claude Code `jobs/`、Hermes `cron/`、Codex `queue_1.sqlite` 都在全局根 |
| D8 | 系统状态不写进用户目录；只读用户目录下的 `.agents/skills` 等配置 | 所有被调研 Agent 一致 |
| D9 | 不采用 OpenSpec 流程，按本文档直接实施 | 用户要求 |
| D10 | **不兼容旧数据**。新 schema 从 v1 起步，旧 `agent_workstation.db`、`workspaces/` 目录整体封存到 `backups/legacy-<ts>/`，代码不读取；不写迁移脚本、不做双写、不做过渡 endpoint | 用户决定；产品尚无需要保全的线上数据，省掉的迁移工作量约占原计划三分之一 |
| D11 | 托管模式每个租户只有一个固定工作目录 `tenants/<usr>/projects/<slug>/`，不开放目录选择；多目录授权只在桌面端提供 | 用户决定；避免托管端接受任意路径与多 mount 的复杂度 |
| D12 | MCP 与 HTTP Tool 是租户全局能力，不分 Project；事实源仍是 `state.db` 的 `mcp_servers / http_tools` 表，本次不改存储介质；删除项目目录级 `.agents/mcp`、`.agents/http-tools` 发现 | 用户决定；与 Skill 的"全局启停 → Agent 上限 → 本次选择"三层一致 |
| D13 | 不做阶段 0 止血，直接从阶段 1 开始 | 用户决定；`master` 上的定时任务 Skill 问题随阶段 3 一并解决 |

### 0.1 开发分支与工作流

**分支**
- 从 `master` 切出长期分支 `refactor/context-storage`，整个重构只在这条分支及其子分支上进行；`master` 在重构期间只接 bugfix。
- 切分支前先处理当前工作区的 50 个未提交改动（权限模式、模型目录、前端 auth 等）：要么先在 `master` 单独提交，要么 `git stash`，**不要**把它们混进重构分支的第一个提交。
- 每个阶段一个子分支 `refactor/context-storage/p<N>-<topic>`，以 PR 合回 `refactor/context-storage`。重构期间不再向 `master` 提交任何与定时任务 Skill 相关的止血改动（D13）。
- 每周把 `master` rebase（不是 merge）进 `refactor/context-storage`，避免最后合并时再次出现 500 文件的冲突。
- 阶段 4 完成、§3 各阶段验收全部通过后，`refactor/context-storage` 以一次 merge commit 合回 `master`，并打 tag `v<next>-context-storage`。合并后旧库会被封存，发布说明必须写明。

**PR 规则**
- 一个 PR 只做一个关注点，改动文件数超过 40 必须拆。
- PR 描述里写明对应本文档的章节号与"完成定义"条目；没有章节号的改动不合。
- 必须通过：`pytest`（含 `test/test_import_boundaries.py`）、`mypy`、前端 `tsc` 与 `vitest`。新增模块要加入 `test_import_boundaries.py` 的允许清单，禁止为了通过而删除规则。
- 禁止在重构分支上做与本文档无关的"顺手改"。

**不做 feature flag**
- 因为 D10 决定不兼容旧数据，新旧模型无法在同一个库上共存，所以不做运行时开关，不做灰度；一次切换。

---

## 1. 背景与目标

### 1.1 现状问题
1. 三层归属（Workspace → Project → Session）里 Workspace 只剩租户含义，Project 承担了资源白名单、文件根、Job 目录三种职责；"这次运行能不能用这个 Skill"要过 Workspace 策略、Project binding、Agent profile、`availableToAllAgents`、`forced_skill_ids`、Job toolkit required/optional、capability contract 共七层判定。
2. 定时任务是 Chat 的平行实现（`build_scheduled_run_context` vs `build_chat_run_context`），Skill 注入链路存在四处断点（见《定时任务技能调度调研报告》§三）。
3. `50afd14` 把 Session 隔离、权限 v2、Job Skill 冻结混在一个 531 文件提交里，被 `9a29428` 整体 revert；OpenSpec 文档被恢复而代码没有，`tasks.md` 的 `[x]` 不可信。
4. 存储层 `database.py` 2300+ 行，19 个存储层外文件自己写 SQL，`busy_timeout=30000` 且无写重试策略。

### 1.2 目标
- 归属模型收敛为 **Tenant → Project（目录授权）→ Session → Run**，Job 挂在 Tenant 下引用 Project。
- 能力判定收敛为**一个函数**，Chat / Team / 群聊 / Job 共用，只以 `mode` 区分。
- 存储按 Hermes 的"事实源 / 派生索引 / 运行时账本"三分法整理，给出并发写、备份、恢复、迁移的明确规则。
- 定时任务的 Skill 在确认时冻结、firing 前校验，不再静默无技能运行。

### 1.3 非目标
- 不重写 AgentScope 运行时、记忆算法、权限审批模型、调度算法。
- 不做跨租户共享。
- 不兼容、不迁移旧数据：不写旧表 → 新表的转换脚本，不回填，不保留旧列、旧 endpoint。启动时若检测到旧版库或 `workspaces/` 目录，整体移入 `backups/legacy-<ts>/` 后按新 schema 初始化（见 §2.5「旧数据处理」）。
- 不实现会话跨 Project 移动（含 `default` → 命名 Project），仅在 §2.1 保留设计意图。

---

## 2. 设计方案

### 2.1 概念模型

| 概念 | 定义 | 身份 | 生命周期 |
|---|---|---|---|
| Tenant | 一个本机用户或一个托管账号的全部数据 | 桌面固定 `local`；托管 `usr_…` | 随账号 |
| Project | 用户命名的工作区：`name`、`slug`、`primary_path`、`folders[]`（授权目录及读写模式）、`display_color` 等 | `slug`（由 `name` 派生，租户内唯一） | 用户创建/归档/删除 |
| Session | 一次对话；归属一个 Project；记录 `cwd`（取自 Project 的 primary_path 或用户切换的授权目录） | uuid | 不可跨 Project 迁移（`default` → 命名 Project 的单向移动列为后续增强，第一版不开放） |
| Run | Session 内一次不可变执行尝试（交互一轮、Job 一次 firing、群聊一次 Worker 运行） | uuid | 开始时写快照，结束后只读 |
| Job | 定时任务定义：prompt、schedule、`project_slug`、`cwd`、冻结的 `required_skills`、tool scope | uuid | 租户级 |
| Agent / Skill / MCP / HTTP Tool / Model | 全局能力目录；MCP / HTTP Tool 不分 Project（D12） | 稳定 id | 租户级 |
| Memory | 用户习惯、Agent 专业区（全局）；Project 记忆（可选，挂 Project） | — | — |

被删除的概念：Workspace、`project_resource_bindings`、`default_project_root()` 隐式回退、`sched-*` 顶层会话。

默认 Project：租户初始化时创建 `slug=default, name=默认项目`，`primary_path` 为 `<tenant_root>/projects/default/`。用户未选择项目时，新建会话一律归入 `default`（由前端在创建请求里显式传 `project_slug=default`，后端不做隐式回退；缺字段仍返回 400）。它与其它 Project 没有任何代码特判，只是不可删除、不可归档。业界对照：OpenCode 把非 git 目录统一归 `global` 项目，Claude Code / Codex 以 HOME 作为事实上的默认项目，ChatGPT / Claude.ai / Hermes 桌面端则允许会话先在项目外、之后移入。

会话移入项目（后续增强，第一版不实现）：保留"把会话从 `default` 单向移入某个命名 Project"的想法，届时规则预计为：仅 `default → 命名 Project`；有运行中的 Run 或该会话是某个 Job 的 owner Session 时拒绝；只更新 `chat_sessions.project_slug` 与 `cwd` 并写一条系统事件；会话文件在 `sessions/<id>/` 下不随项目走；历史 Run 快照不变，下一轮 Run 起按新项目的目录授权与 `.agents/skills` 生效。第一版的数据模型已为此预留（会话文件不放项目目录、Run 快照自包含），不需要额外字段。

### 2.2 目录布局

```
<tenant_root>/                           桌面 ~/.agent-workstation；托管 <USER_DATA_ROOT>/tenants/<usr_…>/
  data/
    state.db (+ -wal -shm)               事实源：projects / sessions / runs / messages / trace / jobs …
    control.db                           仅托管：账号、Key（已存在，不变）
  agents/<id>.md                         Agent profile 权威（从 workspaces/<ws>/.agents/profiles 迁入）
  skills/<package>/SKILL.md              Skill 包（已存在）
  mcp/<id>.yaml  tools/<id>.yaml         MCP / HTTP Tool 的 YAML 导入/导出目录（可选；事实源仍是 state.db 表，见 D12）
  memory/
    users/habits.md
    agents/<agent>/professional.md
    projects/<slug>/project_memory.md    可选
  projects/<slug>/                       桌面：仅当 Project 没有外部目录时作为 primary_path；托管：每个 Project 唯一且固定的工作目录（D11）
  sessions/<session_id>/
    uploads/  artifacts/  runs/<run_id>/  大体积工具输出按文件落地，库里存路径 + sha256
  jobs/<job_id>/firings/<firing_id>/     Job 产出
  exports/                               按需导出的 JSONL（派生物）
  backups/                               `sqlite3.backup()` 产物；`legacy-<ts>/` 存放封存的旧版库与旧目录，代码不读取
```

用户外部目录（如 `~/code/myapp`）只作为 Project 的 `folders[]` 成员被授权读写；系统不在其中创建任何目录或文件，只读取 `.agents/skills`、`AGENTS.md` 这类用户维护的配置。

### 2.3 数据模型（`state.db`）

#### 事实源表
```sql
projects(
  slug TEXT PRIMARY KEY,                -- 由 name 派生：NFC 归一化、小写、空白→-、去非法字符、≤64
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  primary_path TEXT NOT NULL,           -- realpath
  color TEXT, icon TEXT,
  status TEXT NOT NULL DEFAULT 'active',  -- active | archived
  created_at TEXT, updated_at TEXT, archived_at TEXT
)
project_folders(
  project_slug TEXT REFERENCES projects(slug) ON DELETE CASCADE,
  path TEXT NOT NULL,                   -- realpath
  access_mode TEXT NOT NULL,            -- read | readwrite
  is_primary INTEGER NOT NULL DEFAULT 0,
  device_id TEXT, inode TEXT,           -- 平台允许时记录，用于检测目录被移动
  granted_at TEXT,
  PRIMARY KEY (project_slug, path)
)
chat_sessions(... 去掉 workspace_id；project_slug NOT NULL；cwd NOT NULL；source NOT NULL  -- chat | scheduled_job | group_worker | subagent)
chat_runs(... project_slug, cwd, run_snapshot_json)     -- 快照：agent 上限、本轮资源集合、目录授权版本、模型、审批策略
chat_messages(... message_uid TEXT UNIQUE, active INTEGER DEFAULT 1, api_content TEXT)
trace_observations(... session_id, project_slug 冗余列，payload_path TEXT, payload_sha256 TEXT)
scheduled_jobs(... 去掉 workspace_id / project_id；project_slug NOT NULL；cwd NOT NULL；
               required_skills_json NOT NULL DEFAULT '[]';  -- [{id, source_path, sha256, frozen_at}]
               context_policy TEXT NOT NULL DEFAULT 'fresh')
scheduled_job_runs(... run_id REFERENCES chat_runs)
```
`agents`、`mcp_servers`、`http_tools`、`memory_*`、`group_*`、`plan_*`、`workflow_*`：去掉 `workspace_id`，`project_id` 改 `project_slug`（群聊、plan 走 session 反查即可，不再冗余）。

`mcp_servers`、`http_tools`：保持为事实源表，去掉 `workspace_id`，不加 `project_slug`（D12）。

Skill 不再有事实源表：Skill 正文与元数据来自目录扫描（见 §2.10），`skills` 表删除。保留的只有策略覆盖记录 `skill_overrides(skill_id, enabled, available_to_all_agents, installed, updated_at)`——即现在 JSON 文档命名空间 `external_skill_overrides` 的内容，去掉其中的 `workspaceId` 字段后升级为正式表，属于事实源。

#### 派生表（可随时重建）
- `messages_fts`（FTS5 外部内容表，触发器同步；排除 `source IN ('scheduled_job','subagent')`）
- `session_summaries`、`run_dashboard` 等聚合视图
- `exports/*.jsonl`

#### 运行时账本（恢复时不复制）
- `run_leases`（运行中 Run 的 owner epoch）、`job_firing_leases`（ticker lease）、`session_admissions`（每会话输入 FIFO）

删除的表：`workspaces`、`project_resource_bindings`、`project_job_recipients`（并入 `scheduled_jobs.delivery_json`）。

### 2.4 Project 与目录授权

**命名与 slug**
- `name` 校验：NFC 归一化；禁止 `/ \ : * ? " < > |`、首尾空白、首字符 `.`、`..`；长度 1–64；大小写不敏感唯一；保留 `default`、`_system`、`tmp`。
- `slug = slugify(name)`；如果 slug 冲突（如"项目 A"与"项目-A"）同样拒绝，让用户改名。
- 第一版 **name 创建后不可改**；需要改名就新建一个 Project（`yinli project rename` 作为后续增强，需原子改 slug 与目录）。

**授权**
- `folders[]` 里每条是 realpath + `access_mode`。Run 开始时校验目录存在、不是符号链接逃逸、仍在授权列表；任一失败 Run 以 `directory_grant_invalid` 失败。
- 文件工具最终范围 = Project folders ∩ Agent filesystem scope ∩ 审批策略（现有 `RuntimeContext` 的"Project root 与 Agent scope 取交集"保留，只是 Project root 变成多目录）。
- Write/Edit 只允许 `readwrite` 目录；Read/Grep/Glob 允许 `read`。
- 上传先落 `sessions/<id>/uploads/`，用户明确要求才复制进授权目录。

**托管模式（D11）**
- 每个 Project 固定只有一个目录 `tenants/<usr>/projects/<slug>/`，由服务端在创建 Project 时建立；`folders[]` 恒为这一条且 `readwrite`。不接受客户端提交的任何路径，`POST /api/projects/{slug}/folders` 在 hosted 下返回 405。
- Docker 挂载恒为单条 `<该目录>:/workspace:rw`，与现有 `hosted_workspace.py` 一致；多目录与 `:ro` 挂载只在桌面端 Docker 沙盒中实现。

### 2.5 存储策略

**连接与并发（照 Hermes 参数起步，可调）**
```
PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=1000;  PRAGMA journal_size_limit=67108864;
PRAGMA foreign_keys=ON;    PRAGMA wal_autocheckpoint=1000;
```
- 所有写事务 `BEGIN IMMEDIATE`。
- 应用层重试预算：普通写 20 s；transcript/trace 写 60 s（超时则本轮以 `persistence_failed:locked` 结束）；心跳/活动写 0.5 s。抖动：前 2 s 内 20–150 ms，之后 250 ms–1 s。
- 每 50 次成功写 `PRAGMA wal_checkpoint(PASSIVE)`。
- 后端进程是唯一写者。Electron、CLI、调度器 ticker、群聊 Worker 全部经后端 API / 进程内调用写库，不另开连接写。
- UI 浏览、搜索、导出使用只读连接（`?mode=ro`），busy 等 5 s 仍锁则 503。
- 现有 `busy_timeout=30000` 改掉；现有 `_TenantLockProxy` 进程内锁保留作为写串行化手段（单进程下它就是"单一写者"的实现）。

**备份与恢复**
- `yinli db backup`：`sqlite3.backup()` 到 `backups/state-<ts>.db`，不复制 `-wal/-shm`。
- `yinli db recover`：复制事实源表到新库，重建派生表与 `schema_version`，**不复制**运行时账本表。
- 文档明令禁止 `cp` 活库。

**Schema 版本（面向本方案落地之后的演进，不用于兼容旧库）**
- 新库从 `schema_version = 1` 起步。之后简单加列走声明式 `reconcile_columns()`（对比 `SCHEMA_SQL`，`ALTER TABLE ADD COLUMN` 幂等）；数据迁移与索引变更走版本门控块；大回填分块（每批 ≤ 2000 行），用 `state_meta` 游标跨次续跑。
- 现有 `database.py` 里针对旧表的 `_ensure_column` 调用、`legacy_migration`、`migrate_agents`、`assign_owners` 之类的兼容代码全部删除。

**旧数据处理**
- 启动时 `ensure_db()` 检查 `data/state.db` 是否存在且 `schema_version ≥ 1`。若发现旧版 `agent_workstation.db`（无 `schema_version` 表或有 `workspaces` 表）或旧 `workspaces/` 目录，整体移动到 `backups/legacy-<ts>/`，打一条 warning 日志和一次性 UI 提示，然后按新 schema 初始化空库与默认 Project。
- 不提供导入旧数据的工具；用户如需旧对话，可用任意 SQLite 客户端打开封存文件。
- 全局能力（Agent profile、Skill 包、MCP/HTTP 定义）不是"数据"而是配置：Agent profile 从 `workspaces/<ws>/.agents/profiles/` 手工或由一次性脚本复制到 `agents/`（脚本只做文件复制，不改格式，失败就让用户手动放）；Skill 包目录 `skills/` 位置不变。

**大对象**
- Trace / 工具输出超过 64 KB 的 payload 落 `sessions/<id>/runs/<run_id>/<obs_id>.bin`，表里存路径与 sha256；UI 按需读取。

**按聚合分库（可选，第二阶段后）**
- 若 `state.db` 体积或锁竞争成为问题，按 Hermes 的方式拆 `jobs.db`、`audit.db`，仍然每租户一套；不按 Project 拆。

### 2.6 能力裁决：一个函数

```python
def resolve_capabilities(
    *, tenant, project: Project, agent: AgentProfile,
    requested: RequestedCapabilities,        # 用户/模型本轮显式点名的 skill/mcp/tool
    mode: Literal["interactive", "frozen"],  # Chat/Team/群聊 = interactive；Job firing = frozen
    frozen: FrozenCapabilities | None = None # Job 确认时的冻结集合
) -> CapabilitySet                           # 不可变；写入 run_snapshot_json
```
判定顺序固定为三层：
1. **存在性**：全局目录中已安装且启用。Skill 的存在性 = 扫描缓存命中（`skills/registry.py::_skills`）且 `skill_overrides` 未禁用；MCP / HTTP Tool 的存在性 = `mcp_servers / http_tools` 表中存在且启用，全租户可见，不看 Project（D12）。不查任何 Skill 表。
2. **上限**：Agent profile 的 `skills / mcp / tools / filesystem scope / guardrails`；`availableToAllAgents=true` 的 Skill 可绕过 profile 进入 interactive 候选集，**不进入** frozen。
3. **本次选择**：interactive 取 `requested ∪ 候选集`（on-demand，给 `Skill` viewer 工具）；frozen 只取 `frozen.required_skills`，逐个按 `source_path` 定位原包、重读 `SKILL.md` 比对完整 sha256、校验仍启用且在 Agent 上限内，任一失败抛稳定错误码（`skill_missing / skill_moved / skill_hash_changed / skill_disabled / skill_not_allowed`），**不降级运行**。frozen 模式不触发 `reload_skill_cache()`，只读文件校验。

删除的判定：Workspace 策略、Project binding、`_merge_file_catalog_skills` 与 `select_runtime_skills` 的分叉、`_resolve_frozen_runtime_skills`。`build_chat_run_context` 与 `build_scheduled_run_context` 合并为 `build_run_context(mode=...)`，prompt 段落（含 `_build_skill_context_prompt`）只写一份。

### 2.7 定时任务

- `ScheduleJob` 工具只接受显式 Skill 引用；`ConfirmJob` 服务端把三处来源——用户显式 `skill_ids`、Job Agent toolkit 的 required 层 Skill、feasibility 合同 `claimed` 中的 Skill——按确定规则合并去重，经 `resolve_capabilities(mode="frozen")` 校验后写入 `required_skills_json`，每项为 `{id, name, source_scope, source_path, content_sha256(完整 64 位), frozen_at}`，并把最终列表返回给用户确认。这些字段全部可以直接从扫描结果 `DiscoveredSkill` 取得，只需把 `content_hash` 从 16 位前缀改为保留完整摘要。
- firing：`firing_toolkit` 不再自己拼 Skill；`build_run_context(mode="frozen", frozen=job.required_skills)`。校验失败 → firing 失败、Job `pause_reason=<错误码>`、走现有通知通道。
- firing 产生的是 owner Session 下 `source='scheduled_job'` 的 Run，不再建 `sched-*` 顶层会话；`context_policy=fresh` 时只从 Job prompt + 冻结 Skill + 允许的上一次结果构建上下文。
- 每次 Run 启动把最终 `CapabilitySet.skills` 写进 `run_snapshot_json` 与 trace；为空时记 warning。
- `scheduled_jobs` 带 `project_slug` + `cwd`，调度器只扫这张表。

### 2.8 API 变更

| 旧 | 新 |
|---|---|
| `GET/POST /api/workspaces`、`/workspaces/{id}/projects`、`/workspaces/bootstrap` | 删除 |
| `GET /api/projects/{id}` | `GET /api/projects`、`POST /api/projects {name, primary_path?, folders?}`、`GET/PATCH/DELETE /api/projects/{slug}`、`POST /api/projects/{slug}/archive` |
| 新增（预留，第一版不实现） | `POST /api/sessions/{id}/move {project_slug}`，见 §2.1 会话移入项目 |
| `GET/PUT /api/projects/{id}/resources/...` | 删除 |
| `POST /api/projects/{id}/files` | `POST /api/projects/{slug}/folders`（仅桌面；Electron 目录选择器返回路径。托管返回 405，见 D11） |
| Chat/Trace/Memory/Job/群聊请求携带 `workspaceId/projectId` | 只携带 `projectSlug`（创建 Session 时）或 `sessionId`（后续） |
| `/api/traces?workspace_id=…` | `?project_slug=…` |
| 新增 | `GET /api/projects/{slug}/export`（JSONL 包）、`POST /api/db/backup`、`GET /api/db/health`（WAL 大小、checkpoint、锁状态） |

不设过渡期：旧 endpoint 直接删除，前后端同一版本发布。

### 2.9 前端变更

- 删除 `features/workspaces/*`、`ContextCreateModal`、`appStore.activeWorkspaceId`；`activeProjectId` → `activeProjectSlug`。
- TitleBar 的上下文入口改为 Project 切换器（列表 + 新建 + 归档）；Project 设置页展示 `folders[]` 并可增删授权目录。
- Chat 顶部显示当前 Project 与 cwd；会话列表按 Project 过滤；Trace / Memory / 定时任务页同样按 Project 过滤。未选择项目时顶部显示「默认项目」，新建会话自动落 `default`。第一版不提供"移入项目"操作。
- 定时任务详情页展示冻结 Skill 列表（id、来源、hash 前 8 位、冻结时间）及其来源（显式 / 必注入 / 合同认领）。
- 影响面：33 个文件引用 `workspaceId`，41 个引用 `projectId`，以 `useWorkspaceProjects.ts` 替换为 `useProjects.ts` 为入口统一收口。

### 2.10 Skill 文件化加载的衔接

现状（`src/skills/registry.py`、`src/catalog/skills.py`）：
- 扫描目标 = 租户 `skills/`（`runtime` scope，id = 包目录名）+ 租户根与每个 Project 根下的 `.agents/skills | .agent/skills | .claude/skills`（`user` / `project` scope）+ 扩展根；只扫一层。
- 每个 `DiscoveredSkill` 带 `id`、`source_scope`、`source_kind`、`source_path`、`content_hash`、`resources`。
- 结果放进程缓存 `_skills`；启动、导入、更新、删除时 `reload_skill_cache()` 全量重扫。
- `enabled / availableToAllAgents / installed / workspaceId` 覆盖记录存 SQLite JSON 文档命名空间 `external_skill_overrides`。
- `scan_skill_directories` 规则是"同名目录后出现的覆盖先出现的"，Project 目录排在租户目录之后，因此 Project 下同名包会遮蔽租户包。

需要随本方案调整的点：
1. **扫描根来源**：`_project_scan_roots()` 现在读 `list_projects().rootPath`，改为读 `project_folders` 中所有授权目录（read 即可，Skill 只读不写）。交互式 Run 只把当前 Project 的 folders 加入扫描；不同 Project 的 `.agents/skills` 互不可见。
2. **缓存原子替换**：`reload_skill_cache()` 目前先 `_skills.clear()` 再填充，并发中的 `get_installed_skill()` 会短暂落空。改为先构建新字典再整体替换引用。
3. **遮蔽规则显式化**：优先级固定为 `project > user(.agents) > runtime(skills/)`，并在 Skill 列表 API 里返回被遮蔽的候选（`shadowed_by`），避免用户困惑。Job 冻结记录绑定 `source_path`，不受后来出现的同名包影响。
4. **hash 完整保留**：`content_hash` 保留完整 sha256（展示时截取前 8 位），供 Job 冻结比对与 trace 记录使用。
5. **策略记录去 Workspace**：`external_skill_overrides` 去掉 `workspaceId`，升级为 `skill_overrides` 表；`_skill_from_discovered` 里写死的 `workspaceId: "default"` 删除。
6. **frozen 校验不依赖缓存**：firing 时按冻结的 `source_path` 直接读文件比对，不调用 `reload_skill_cache()`，也不受缓存是否过期影响；interactive 仍走缓存。
7. **capability inventory**：`scheduler/capability_catalog._skill_rows` 已通过 `get_installed_skill` 走缓存，去掉其中的 Project binding 判定后无需其它改动。

不需要改的：Skill 正文不进库、`refresh_skill_package` 每次重读 `SKILL.md`、Project 目录下的 Skill 免安装直接可用——这些与 D4、D8 一致。

### 2.11 与现有代码的映射

| 现有 | 处理 |
|---|---|
| `src/runtime_context.py`（`workspace_id / project_id / project_root`） | 改为 `RunContext(tenant_id, project: Project, cwd, session_id, run_id, agent_id, capability_set, directory_grant)` |
| `src/project_resources.py` | 删除；逻辑并入 `resolve_capabilities` |
| `src/config/runtime_paths.py` 的 `workspaces_catalog_root()`、`default_project_root()` | 删除；新增 `agents_root()`、`project_default_root(slug)`、`session_root(id)` |
| `src/storage/workspace_project_records.py` | 改为 `project_records.py`（projects / project_folders） |
| `src/server/routes/workspaces.py` | 改为 `projects.py` |
| `src/server/routes/chat/run_context.py` 两个 builder | 合并为一个，`mode` 参数 |
| `src/server/routes/chat/skill_selector.py`、`_merge_file_catalog_skills`、`_resolve_frozen_runtime_skills` | 删除，统一到 `src/runtime/capabilities.py::resolve_capabilities` |
| `src/scheduler/firing_toolkit.py` 的 required 层合并 | 保留 mcp/http/knowledge/builtin，Skill 交给 `resolve_capabilities` |
| `src/scheduler/feasibility.py::contract_from_feasibility.skill_ids` | 变为 ConfirmJob 合并输入之一 |
| `50afd14:backend/src/scheduler/skill_manifest.py`、`models.JobRequiredSkill` | 按文件摘回，适配到新接口（冻结记录、错误码、大小上限可直接复用）；`legacy_migration.py` 不要 |
| `src/storage/database.py` 连接初始化 | 按 §2.5 改 pragma，增加写重试与 checkpoint 计数 |
| `src/skills/registry.py::_project_scan_roots / reload_skill_cache` | 扫描根改读 `project_folders`；缓存原子替换；遮蔽规则显式化（§2.10） |
| `src/catalog/skills.py::content_hash` | 保留完整 sha256 |
| `src/catalog/mcp_files.py`、`http_files.py` 的 `project_root` 分支与 `project_mcp_ids / project_http_tool_ids` | 删除（D12）；租户级 YAML 目录从 `workspaces/<ws>/.agents/mcp` 改为 `<tenant_root>/mcp/`，语义不变 |
| `src/catalog/paths.py::project_mcp_dir / project_http_tools_dir / project_jobs_dir` | 删除 |
| JSON 命名空间 `external_skill_overrides` | 升级为 `skill_overrides` 表，去掉 `workspaceId` |

---

## 3. 实施方案

每个阶段独立可发布、可回滚；阶段内每个 PR 控制在一个关注点。禁止再出现跨阶段的大提交。

### 阶段 0：已取消（D13）

不做止血。原阶段 0 的"把 openspec 里不实的 `[x]` 改回 `[ ]` 并注明被本方案取代"并入阶段 1 第 0 项；定时任务 Skill 断点在阶段 3 一并解决。在此之前 `master` 上的定时任务 Skill 行为保持现状。

### 阶段 1：存储底座（1–2 周）

目的：先把 SQLite 用对，不动表结构语义。

0. 文档对齐：将 `openspec/changes/unify-file-based-skill-loading/tasks.md` 第 8 章、`simplify-global-session-context/tasks.md` 中代码不存在的任务改回 `[ ]`，两份 proposal 顶部加注"已被 `docs/context-storage-refactor.md` 取代"。
1. `database.py`：pragma 按 §2.5；写路径统一走 `write_tx()` 上下文管理器（`BEGIN IMMEDIATE` + 重试预算 + 抖动 + 成功计数触发 PASSIVE checkpoint）；读路径提供 `read_conn()` 只读连接。
2. 新增 `src/storage/maintenance.py`：`backup()`、`recover()`、`health()`；CLI `yinli db backup|recover|health`；`/api/db/*`。
3. 新增 `schema_version` + `reconcile_columns()`；把现有 `_ensure_column` 调用迁到声明式。
4. 表分类登记：在 `database_schema.py` 顶部用三个常量列出事实源 / 派生 / 账本表名，`recover()` 和结构测试据此工作。
5. Trace 大对象外置：`trace_observations` 增加 `payload_path / payload_sha256`，超过阈值落文件。
6. 验收：并发写压测（10 写线程 + 5 读线程 60 s 无 `database is locked` 逃逸）；`backup → recover` 往返后事实源行数一致、FTS 可查；`health` 能报 WAL 大小与上次 checkpoint。
7. 回滚：pragma 与 `write_tx` 可通过配置开关回到旧行为。

### 阶段 2：Project 模型切换（2–3 周）

目的：删 Workspace，Project 变成"命名 + 目录授权"，运行时不再碰 binding。

1. 重写 `database_schema.py`：一次性给出新 schema（§2.3），含 `projects`、`project_folders`、`skill_overrides`，去掉 `workspaces`、`project_resource_bindings`、`project_job_recipients`、`skills` 及所有 `workspace_id` 列；`schema_version = 1`。实现 §2.5"旧数据处理"的启动检测与封存。
2. `runtime_paths.py`：新增 `agents_root()`、`project_default_root(slug)`、`session_root(id)`；删除 `workspaces_catalog_root()`、`default_project_root()`。提供一次性脚本 `yinli agents import-legacy <dir>` 把旧 profile 文件复制到 `agents/`（只复制，不改格式）。
3. `RunContext` 重写；`resolve_project_resource_access` 删除；文件工具 scope 改为多目录交集。
4. `routes/projects.py` 替换 `routes/workspaces.py`；Chat/Trace/Memory/Job/群聊的请求模型去掉 `workspaceId`，`projectId` → `projectSlug`，不做旧字段映射。
5. 清除代码中全部 `workspace_id` 引用并把 `project_id` 改为 `project_slug`：`src/storage` 16 处、`src/catalog` 12 处、`routes/chat` 11 处、`src/memory` 8 处、`src/scheduler` 7 处等（见调研清单）。记录层直接按新表写，没有双写。
5a. Skill 扫描衔接（§2.10 第 1、2、3、5 点）：`_project_scan_roots` 改读 `project_folders`；`reload_skill_cache` 原子替换；`external_skill_overrides` → `skill_overrides` 表并丢弃 `workspaceId`；Skill 列表 API 返回 `source_scope` 与 `shadowed_by`。
5b. MCP / HTTP Tool 全局化（D12）：`mcp_servers / http_tools` 去掉 `workspace_id`；删除 `mcp_files.py / http_files.py` 的 `project_root` 分支与 `paths.py` 的 `project_*_dir`；租户级 YAML 目录改到 `<tenant_root>/mcp/`、`tools/`；`capability_catalog` 的 MCP/HTTP 行不再做 Project 过滤。
6. 托管：`ensure_tenant_directories()` 不再建 `workspaces/`，改建 `projects/default/`、`agents/`、`mcp/`、`tools/`；hosted 下创建 Project 时同步建 `projects/<slug>/`（D11）。
7. 验收：全新租户启动即有默认 Project 并可发消息；带旧库的租户启动后旧库出现在 `backups/legacy-<ts>/`、新库正常可用；`grep -r workspace_id src` 为零；目录逃逸测试（符号链接、`..`）全部拒绝。
8. 回滚：回退到上一版本二进制并把 `backups/legacy-<ts>/` 里的库挪回原位即可，因为新版从不改写旧库。

### 阶段 3：能力裁决与 Job Skill 冻结（2 周）

1. 新建 `src/runtime/capabilities.py::resolve_capabilities`；先让交互式 Chat 走它（行为等价测试：同一输入下候选 Skill 集合与旧实现一致），再切 Team / 群聊 Worker。
2. 从 `50afd14` 摘回 `skill_manifest.py`（冻结记录、`SkillManifestError`、大小上限）与 `JobRequiredSkill`，适配成 `FrozenCapabilities`；hash 与来源直接取自扫描结果 `DiscoveredSkill`，`content_hash` 改为完整 sha256（§2.10 第 4、6 点）。
3. `ConfirmJob` 实现 §2.7 的三源合并并返回最终列表；`scheduled_jobs` 启用 `required_skills_json / context_policy`（列已存在于本机库，代码补齐）。
4. 两个 run_context builder 合并；`firing` 走 `mode="frozen"`；firing 产生 owner Session 下 `source='scheduled_job'` 的 Run，`sched-*` 顶层会话这一概念连同代码一起删除。
5. 删除七层判定中的旧实现与对应测试，补新测试：hash 漂移暂停、未安装暂停、合同与必注入合并、空集合 warning、Job 不受 `availableToAllAgents` 影响。
6. 验收：定时任务端到端——创建 → 确认（看到冻结列表）→ 到期 firing → trace 中可见 Skill 已加载；修改 `SKILL.md` 后下一次 firing 暂停并通知。

### 阶段 4：前端切换（1–2 周）

1. `useProjects.ts` 替换 `useWorkspaceProjects.ts`；删除 `features/workspaces`。
2. TitleBar Project 切换器、Project 设置页（folders 增删、归档）。
3. Chat / Trace / Memory / 定时任务页改按 `projectSlug` 过滤；定时任务详情展示冻结 Skill。
4. 验收：无任何 `workspaceId` 引用；全新用户首次打开直接可聊；切换 Project 后会话列表、Trace、Job 列表同步切换。

### 阶段 5：清理与可选分库（1 周 + 按需）

1. 清点并删除所有残留的兼容代码路径（`_ensure_column` 旧调用、`migrate_agents`、`legacy_migration`、`assign_owners`、旧 JSON 命名空间读取），CI 加 grep 棘轮防止回流。
2. 删除 `openspec/changes/simplify-global-session-context`、`unify-file-based-skill-loading` 中已被覆盖的部分，归档本文档为权威说明。
3. 若 `health` 显示 `state.db` 锁等待或体积成为问题，再按聚合拆 `jobs.db` / `audit.db`。2026-10-21 检查时没有这两项指标，不拆。

### 时间线（粗估）
1 先行；2 依赖 1；3 依赖 2 的 `RunContext`；4 与 3 可并行；5 最后。去掉阶段 0、迁移、双写与过渡期后，总计约 5–6 周单人，或 3–4 周两人并行。

---

## 4. 风险与对策

| 风险 | 对策 |
|---|---|
| 再次出现跨阶段大提交导致整体 revert | 每 PR 一个关注点；CI 加 import 棘轮测试（`scheduler` 不得 import `routes`；存储层外无 SQL）；`tasks` 标完成必须附测试文件名 |
| 多目录授权扩大文件工具范围 | Write/Edit 仅 `readwrite` 目录；realpath + 符号链接边界检查；托管拒绝客户端路径 |
| 单库锁竞争 | 单一写者 + `BEGIN IMMEDIATE` + 重试预算 + PASSIVE checkpoint；`health` 暴露指标；必要时按聚合分库 |
| Job 冻结过严导致任务频繁暂停 | 只校验 Skill 包 hash，不校验 references/scripts；UI 一键"重新确认"刷新冻结 |
| 用户升级后发现历史对话"不见了" | 启动时一次性 UI 提示说明已封存位置；发布说明明确写"本版本不保留旧对话"；封存目录永不自动清理 |
| 开发机上反复出现旧库触发封存 | 封存只在检测到旧 schema 时触发，新库不会被再次封存；提供 `yinli db reset --dev` 显式清空 |
| 前端 74 个文件引用旧字段 | 以 hooks 层收口，组件只改 props 名；分页面逐个 PR |

---

## 5. 开发者必读：坑与陷阱清单

> 每一条都写了：在哪里、为什么会错、正确做法、怎么验证。实施前通读；阶段开工时再读对应分组。编号用于 PR 描述引用（如 `坑 C3`）。

### A. 过程类

**A1 把 `50afd14` 整包 cherry-pick 回来。** 那个提交 531 个文件、4.3 万行，混有 Session 隔离、权限 v2、Skill 目录、迁移脚本，和本方案多处冲突（它保留了 Project binding、用 owner Session 绑 Job、带 legacy 迁移）。只能 `git show 50afd14:<path>` 按文件摘，而且只摘 §2.11 表里点名的几个文件；摘回来的代码一律按新接口改写，不保留 `workspace_id`。验证：摘回文件 `grep workspace_id` 为零。

**A2 相信 openspec 的 `[x]`。** `unify-file-based-skill-loading` 第 8 章与 `simplify-global-session-context` 的大部分 `[x]` 对应的代码不存在。开工第一件事是把它们改回 `[ ]`，并在两份 proposal 顶部注明"已被本方案取代"。以后任何"已完成"必须附测试文件名。

**A3 在 `master` 上直接改。** 见 §0.1。重构期间任何与本方案相关的改动出现在 `master` 都要回退；`master` 只接与本方案无关的 bugfix。

**A4 用 `--include=*.py` 这类 glob 在 zsh 下 grep。** 本机默认 shell 是 zsh，未加引号的 `--include=*.py` 会因 glob 无匹配导致整条命令失败并返回空结果，容易误判"没有引用"。统一用 `rg`，或给参数加引号。

### B. 租户、路径与锁

**B1 租户上下文是 ContextVar，不是全局变量。** `runtime_paths.tenant_root()` 在 hosted 下靠请求级上下文解析，没有上下文直接抛 `TenantMissing`。任何在后台线程、调度器 ticker、asyncio task 里访问路径或数据库的代码，必须显式携带并恢复租户上下文（看 `bootstrap_tenant_scheduler` 的写法）。新加的 `resolve_capabilities`、`write_tx`、备份/恢复命令全部如此。验证：hosted 模式下跑一次定时任务 firing 与一次 `db backup`，不出现 `TenantMissing`。

**B2 `database._lock` 是 `_TenantLockProxy`，按当前租户解析到不同的 RLock，并用 threading.local 栈保证成对释放。** 不要把它换成模块级 `threading.Lock`，也不要在 `with database._lock:` 里再切换租户。§2.5 的 `write_tx()` 要建立在这个 proxy 之上，而不是绕开它。

**B3 `_system` 不是 Workspace，但它住在 `workspaces/_system/.agents` 下。** `catalog/paths.py` 的 `system_overlay_root()` 指向那里，`validate_workspace_id` 明确禁止把 `_system` 当 Workspace id。删 `workspaces/` 目录时必须先把系统 Agent 覆盖根迁到 `agents/_system/`（或 `system_overlay/`），并保留 `_merge_system_agent` 的 base + overlay 合并语义。验证：`test_system_job_agent.py` 通过，系统 Job Agent 仍在列表里。

**B4 `ensure_tenant_directories()` 的执行顺序。** `main.py` lifespan 里顺序是 `ensure_tenant_directories → reload_skill_cache → reconcile_interrupted_* → bootstrap_tenant_scheduler`。新增的"旧库检测与封存"必须放在 `ensure_db()` 之前、`ensure_tenant_directories` 之内，否则 `ensure_db` 会先在旧库上建新表，封存逻辑再也认不出它是旧库。验证：用一份旧库启动，`backups/legacy-<ts>/` 出现且新库 `schema_version=1`。

**B5 桌面与托管的根目录不同。** 桌面是 `USER_DATA_ROOT`（Electron userData），托管是 `tenants/<usr>/`。所有新路径函数都走 `tenant_root()`，不要再写 `Path.home() / ".agent-workstation"` 之类的硬编码。`control.db` 只在 hosted 存在，本方案不碰它。

**B6 macOS/Windows 文件系统大小写不敏感、路径可能是 NFD。** Project `name → slug` 和 `folders[].path` 比较前统一 `unicodedata.normalize("NFC")` + `os.path.realpath` + 大小写折叠（仅用于比较，存储保留原样）。验证：创建 `Demo` 后再创建 `demo` 返回 409；授权 `/Users/x/Code` 后 `/users/x/code` 下的文件可访问。

### C. Agent 目录

**C1 Agent profile 文件是权威，`agents` 表只是索引。** `routes/agents.py` 每次写入都是先写 `.md` 再 `upsert_agent()`；`catalog/loader.py` 按磁盘指纹 `_compute_disk_fingerprint` 决定是否重扫。不要反过来从表生成文件，也不要删掉 `upsert_agent` 以为"文件化了就不用表"——`agents` 表是查询与外键用的索引，属于派生表，`recover()` 时重建。

**C2 Job Agent 的 toolkit 与 `agent.skills` 是同步关系。** `routes/agents.py:237` 用 `bound_resource_ids(toolkit)` 把 required+optional 的 Skill/MCP/HTTP 回写进 `agent.skills` 等字段。改 Agent 数据模型时保留这一步，否则 `resolve_capabilities` 的"Agent 上限"对 Job Agent 永远为空，所有 Job 冻结都会失败。

**C3 `list_workspace_agents(workspace_id)` 的调用方。** 删除它时要找全调用方（Chat 目录、群聊 Host 选人、Job Agent 目录行 `_job_agent_catalog_line`），统一换成 `list_registered_agents()`。

**C4 profile frontmatter 里的 `workspaceId` 字段。** 旧 profile 文件带这个字段，新解析器要**忽略而不是报错**（因为 `import-legacy` 只做文件复制）。验证：导入一份旧 profile 能正常加载。

**C5 catalog fingerprint 参与运行时缓存键。** `runtime/agent_spec.py:121` 把 `catalog_fingerprint()` 拼进 runtime features cache key。Agent 目录位置变了之后指纹算法不变，但要确认 `agents/` 目录下的新文件被 `_collect_profile_files` 收进指纹，否则改 profile 不刷新缓存。

### D. 存储层

**D1 新 schema 一次写全，不要"先加列再慢慢删"。** D10 已经允许丢弃旧数据，所以 `database_schema.py` 直接改成最终形态，不保留 `workspace_id` 列、不保留旧表。任何"为了兼容先留着"的列都会在阶段 5 被 grep 棘轮拦下。

**D2 `ensure_db()` 被到处调用。** `JsonDocumentStore`、记录层、路由里都有 `ensure_db()`，它是幂等建表入口。旧库检测只能放在它内部最前面，并用一个进程级标志保证每租户只检测一次，否则每次请求都 stat 一遍文件。

**D3 `database.py:984` 的 `reset` 会触发 `reload_skill_cache()`。** 存储层反向依赖 Skill 注册表，这会在 `test_import_boundaries` 里埋雷。重构 `database.py` 时把这类回调改成事件/钩子注册，由上层订阅。

**D4 JSON 文档命名空间不止 `external_skill_overrides`。** 现有命名空间至少有 `system_settings`、`schema_migrations`、`chat_store`、`external_skill_overrides`；其中部分记录里带 `workspaceId`。逐个过一遍：`external_skill_overrides` 升级为表；`schema_migrations` 废弃（由 `schema_version` 取代）；其余删掉 `workspaceId` 字段。

**D5 只读连接要用 URI 模式。** `sqlite3.connect("file:...?mode=ro", uri=True)`。只读连接不要执行 `PRAGMA journal_mode=WAL`（在只读库上会报错），也不要 checkpoint。WAL 模式下只读连接仍需要 `-shm` 可写权限，托管部署的目录权限要留意。

**D6 `BEGIN IMMEDIATE` 与 Python sqlite3 的隐式事务冲突。** 标准库默认 `isolation_level=""` 会在 DML 前自动 `BEGIN`（DEFERRED）。`write_tx()` 必须用 `isolation_level=None`（autocommit）再手动 `BEGIN IMMEDIATE / COMMIT / ROLLBACK`，否则 `BEGIN IMMEDIATE` 会报 "cannot start a transaction within a transaction"。

**D7 `busy_timeout` 从 30000 改到 1000 会让现有测试里的并发用例暴露问题。** 这是预期行为；修的是调用方（走 `write_tx` 的重试预算），不是把 timeout 调回去。

**D8 备份不要 `cp`。** 活库 + `-wal` + `-shm` 三个文件不一致时复制出来的库可能损坏。只用 `sqlite3.Connection.backup()`。封存旧库（`legacy-<ts>/`）时例外：先对旧库执行 `PRAGMA wal_checkpoint(TRUNCATE)` 再整体 `rename`，因为那时只有我们一个进程在用它。

**D9 `trace_observations` 大对象外置后的删除。** 删会话/删项目时要同时删 `sessions/<id>/runs/<run_id>/` 下的文件；库里 `payload_path` 用相对 `tenant_root()` 的路径，不要存绝对路径（托管迁机、桌面换 userData 都会失效）。

**D10 测试夹具。** 现有测试大量依赖 `isolated_db` 夹具和 `workspace_id="default"` 的假数据。阶段 2 必须先改夹具（建默认 Project、不建 Workspace），再改测试；否则会出现几十个测试同时红、不知道哪个是真失败。

**D11 `tmp/` 与 `.mypy_cache` 下的 `.db`。** 全库搜索 `.db` 文件时会把 mypy 缓存扫进来；旧库检测只看 `data/agent_workstation.db` 与 `data/state.db` 两个固定路径，不要用 `glob`。

### E. Project 与目录授权

**E1 群聊把运行文件写进了用户项目目录。** `group_studio/files.py:19` 的 `studio_workspace()` 返回 `project_root/.agent-workstation/group-studios/<id>`，违反 D8。必须迁到 `<tenant_root>/sessions/<session_id>/group-studios/<id>/`（或 `groups/<id>/`），并更新 `ensure_studio_dirs` 的所有调用方。验证：在用户授权目录下跑一次群聊，目录里不出现 `.agent-workstation/`。

**E2 Docker 挂载。** 托管下按 D11 只有一个目录，`sandbox/hosted_workspace.py:46` 的单条 `<dir>:/workspace:rw` 保持不变，只是 `project_root` 改为 `projects/<slug>/`。桌面端 Docker 沙盒若启用多目录授权，要变成多个 mount、`read` 目录挂 `:ro`，并保证容器内路径与 `folders[].path` 有确定映射（建议 `/workspace/<index>/`）。Skill 运行根 `/workspace/skills` 保留。验证（桌面）：ro 目录内 Write 被拒绝且错误码为 `directory_grant_invalid`，不是 Docker 的 EROFS。

**E3 Project 根下的 `.agents/mcp`、`.agents/http-tools`、`.agents/jobs` 也在被扫描。** `catalog/mcp_files.py`、`http_files.py`、`job_files.py` 用 `project_mcp_dir(project_root)` 等读取项目目录下的定义文件，且写死 `workspace_id="default"`。按 D12 与 D7：三者的项目级分支全部删除，只保留 Skill 的项目级扫描。注意 `discover_mcp_files(project_root=...)` 的调用方（MCP 路由、capability_catalog）要一并去掉参数，否则 mypy 不会报错但运行时静默多扫一遍。验证：在授权目录里放一个 `.agents/mcp/x.yaml`，MCP 列表不出现它。

**E4 `realpath` 与符号链接逃逸。** 文件工具最终路径必须 `os.path.realpath` 后再做前缀比较，且前缀比较要加分隔符（`/a/b` 不应匹配 `/a/bc`）。授权目录本身是符号链接时记录其 realpath。验证：现有目录逃逸测试 + 新增"授权目录内的符号链接指向外部"用例。

**E5 默认 Project 的 `primary_path` 在租户目录里。** `projects/default/` 在 `tenant_root()` 下，而 `tenant_root()` 同时也是系统状态所在。文件工具 scope 要把 `data/`、`agents/`、`skills/`、`backups/` 排除在默认项目可写范围之外，只允许 `projects/default/` 与 `sessions/<id>/`。否则模型可以 Write 覆盖 `state.db`。验证：在默认项目里让模型写 `../data/state.db` 被拒绝。

**E6 slug 保留字。** 至少保留 `default`、`_system`、`tmp`、`sessions`、`jobs`、`backups`、`exports`、`agents`、`skills`、`data`——因为 `projects/<slug>/` 与这些目录同级，避免将来目录布局调整时冲突。

### F. 运行时守卫与能力版本

**F1 "Agent 能力或 Project 资源版本已变化"会在运行中途拦截文件写入。** `security/authorization/boundaries.py:58` 在每个工具动作前比较 `capability.version` 与 Run 授权快照里的 `capability_version`。现在这个 version 由 Agent 配置 + Project 资源绑定派生；删除 Project binding 后，如果 version 的来源没有同步改成"catalog fingerprint + 目录授权版本 + CapabilitySet hash"，要么永远不变化（失去保护），要么每次 `reload_skill_cache` 都变化（中途全部拦截——这正是本机 Codex 会话记录里出现过的现象："文件读写在改到一半时被拦住"）。正确做法：`CapabilitySet` 自带 `version = sha256(排序后的 skill ids + hashes, mcp ids, tool names, folders, agent fingerprint)`，Run 开始时写入快照，后续比较用快照内的 `CapabilitySet`，**不在 Run 中途重新解析**。验证：Run 进行中导入一个无关 Skill，Run 不被拦截；Run 进行中撤销目录授权，下一次写被拦截。

**F2 `runtime_features_cache_key` 包含 denylist 指纹与 catalog 指纹。** `scheduled_runtime.scheduled_job_unattended_feature_key` 与 `agent_spec.py` 的缓存键在合并两个 builder 时要保留区分，否则交互式与 frozen 两种模式会互相命中缓存，Job 拿到交互式的工具集（含 `AskUser`）。

**F3 `SCHEDULED_JOB_UNATTENDED_DENYLIST` 不含 `Skill` viewer，但 frozen 模式本来就不该给 viewer。** 合并 builder 后，frozen 模式下不注册 `Skill`/`activate_skill` 工具，而是把冻结的 SKILL.md 直接预加载进上下文（`skill_manifest.py` 的 24k/48k 大小上限沿用）。不要靠 denylist 实现这个区别。

**F4 `project_resources.require_agent` 对 `group-studio-host` 与系统 Agent 有特判。** 删除 `project_resources.py` 时把"系统 Agent 与群聊 Host 不需要租户内注册"这条语义搬到 `resolve_capabilities` 的 Agent 查找里，否则群聊启动失败。

### G. Skill（详见 §2.10，这里只列会出错的点）

**G1 `reload_skill_cache()` 的 clear-then-fill 窗口。** 必须改成原子替换，否则 firing 与导入并发时 Job 报 `skill_missing`。
**G2 `content_hash` 只有 16 位。** 冻结用完整 sha256；旧的 16 位字段在新库里不存在，不要留。
**G3 `_project_scan_roots()` 读的是 `list_projects().rootPath`。** 阶段 2 改表后这个函数会直接报错；要在同一个 PR 里改为读 `project_folders`。
**G4 `_skill_from_discovered` 写死 `workspaceId: "default"`**，Skill Pydantic 模型里也有该字段；两处一起删。
**G5 遮蔽规则。** `scan_skill_directories` 后者覆盖前者，顺序由 `skill_scan_targets` 决定；改扫描根时不要打乱 `runtime → user → project` 的追加顺序，否则优先级反转。
**G6 Docker 下 Skill 要复制进容器。** `agents/factory.py` 在 `skill_paths` 非空且非持久沙盒时把包复制到 `/workspace/skills`。frozen 模式预加载 SKILL.md 正文后，仍然要复制 `scripts/` 等资源，否则 Skill 里的脚本路径失效。

### H. 定时任务

**H1 合并只发生在 `ConfirmJob`。** 不要在 `ScheduleJob`、`create_job` 或 firing 里各做一次"顺手合并"，否则又回到三处口径不一致。`contract.skill_ids` 只是 ConfirmJob 的输入之一。
**H2 firing 不调用 `reload_skill_cache()`，也不调用 `list_projects()`。** 它只读 Job 快照、按 `source_path` 校验文件、构建 Run。
**H3 `notifications/dispatcher.py:29` 要求 `workspace_id` 非空。** 删 Workspace 后通知模型改为 `tenant_id + project_slug`，并同步改 `inbox.py`、`models.py` 与前端通知组件的字段名。
**H4 `project_job_recipients` 表删除后，投递目标进 `scheduled_jobs.delivery_json`。** 注意现有 UI 的"通知对象"编辑接口。
**H5 `sched-*` 顶层会话删除会影响前端会话列表过滤与 `session_studio.py`。** 搜索 `sched-` 前缀的所有判断一起删。
**H6 ticker lease 与 `reconcile_interrupted_chat_runs`。** 启动时 reconcile 会把上次中断的 Run 标记失败；新 Run 快照字段加入后，reconcile 读取旧格式快照会失败——新库没有旧数据，但要确保 reconcile 对缺字段的容错只针对"运行中崩溃"的情况，不要为旧格式写兼容。
**H7 系统 Job Agent。** `test_system_job_agent.py` 覆盖的系统级 Job Agent 来自 `_system` overlay（见 B3），其 toolkit 的 required Skill 也要能被 `resolve_capabilities` 看到。

### I. 群聊 / Team / Subagent

**I1 `RuntimeContext` 的消费者不止 Chat。** AGENTS.md 明确它传给 TeamRun、Subagent、Memory、Trace、RunState。重写为 `RunContext` 时用 mypy 找全构造点与属性访问点，特别是 `team_runtime/`、`team_runtime_v2/`、`subagents/`、`group_studio/`——这些目录各有一两处直接读 `ctx.workspace_id`。
**I2 群聊 Worker 的文件根。** 见 E1。
**I3 Subagent 的 `skill_paths` 继承。** Subagent 继承主 Agent 的 CapabilitySet 子集，不要重新解析。

### J. 记忆

**J1 `markdown_reflection.py` 把 `workspace_id` 当作全局记忆的 owner key**（`read_global_memory(item.workspace_id)`，默认 `"default"`）。改为 `tenant`（即无参数，路径由 `tenant_root()` 决定）；Project 记忆用 `project_slug`。`memory_nodes` 表的 `workspace_id` 列删除。
**J2 记忆提示缓存按会话冻结**（`memory_prompt_cache.py`）。Project 维度加入后，缓存键要含 `project_slug`。

### K. 前端

**K1 `useWorkspaceProjects.ts` 在启动时请求 `/api/workspaces` 并做 bootstrap。** 后端删掉该路由后，如果前端没有同版本切换，会出现 404 重试循环并卡在加载页。前后端必须同一个 PR 或同一次发布切换。
**K2 `settingsStore` 持久化到 localStorage。** 检查其中是否存有 `workspaceId/projectId`（当前 `appStore` 不持久化，但 `settingsStore` 持久化）；若有，升级后旧值会残留，需在读取时丢弃未知键。
**K3 74 个引用点。** 不要在组件里逐个改字段名；先改 `hooks/` 与 `types/`，让 tsc 报错清单成为待办清单。
**K4 定时任务详情页只展示 `job.skillIds`。** 改为展示冻结记录（id、来源 scope、hash 前 8 位、冻结时间），字段名 `requiredSkills`。
**K5 Electron 的后端端口每次启动变化**（`settingsStore.ts:54` 注释）。新增的 `/api/db/*`、`/api/projects` 不要在前端缓存绝对 URL。

### L. 测试与 CI

**L1 `test_import_boundaries.py` 是允许清单制。** 新模块（`runtime/capabilities.py`、`storage/project_records.py`、`storage/maintenance.py`）要加进清单；`scheduler` 不得 import `routes.chat`（现有规则），新 `resolve_capabilities` 要放在 `src/runtime/` 而不是 `routes/chat/`，否则调度器一引用就违规。
**L2 断言 `skill_unbound` 的旧测试**（`test_scheduled_unattended.py:480`、`test_scheduled_jobs.py:702`、`test_scheduled_job_resource_pause.py` 三处）要改写成新的错误码语义（`skill_missing / skill_hash_changed / …`），不是删掉。
**L3 行为等价测试。** 阶段 3 切 `resolve_capabilities` 前，先录制一组"Agent 配置 × 请求 → 旧实现候选 Skill 集合"的快照，再让新实现对照；否则无法证明交互式行为没变。
**L4 并发测试要真并发。** 阶段 1 的压测用多线程 + 真实文件库，不要用 `:memory:`（内存库没有 WAL，测不出锁问题）。

### M. 托管模式

**M1 `to_daemon_path()`。** 托管下容器挂载路径要经它转换；多目录挂载每一条都要转。
**M2 管理员删除账号 = 删租户目录。** 新布局下 `backups/legacy-<ts>/` 也在租户目录里，会一起删，这是预期；发布说明写明。
**M3 key2api 同步与 `control.db` 不受影响**，但 `ensure_tenant_directories()` 的新目录列表（`agents/ projects/default/ sessions/ jobs/ backups/`）要在新用户首次登录时创建，别漏 `sessions/`。

### N. 发布

**N1 发布说明必须写明：本版本不保留旧对话、旧库位置、如何用 SQLite 客户端打开。**
**N2 桌面端首次启动的一次性提示**要在后端封存完成后再弹，不要在前端猜。
**N3 `yinli db reset --dev` 只在非 hosted 下可用。**

---

## 6. 完成定义（Definition of Done）

每个阶段合回 `refactor/context-storage` 前，逐条勾选：

- [ ] 本阶段 §3 列出的验收项全部有自动化测试覆盖，且 CI 绿。
- [ ] `rg -n "workspace_id|workspaceId" backend/src frontend/src` 的结果与本阶段预期一致（阶段 2 起必须为零）。
- [ ] `rg -n "default_project_root|project_resource|list_workspace_agents|sched-" backend/src` 的结果与本阶段预期一致。
- [ ] 新增模块已加入 `test_import_boundaries.py` 允许清单，且没有删除任何既有规则。
- [ ] 本阶段涉及的坑清单条目（§5）在 PR 描述中逐条注明"已处理 / 不适用"。
- [ ] 桌面模式与 hosted 模式各手工跑通一次：新建默认项目 → 发消息 → 创建并确认一个带 Skill 的定时任务 → 等待一次 firing → trace 里看到冻结 Skill 已加载。
- [ ] 用一份旧库启动一次，确认被封存且新库可用（阶段 2 起）。
- [ ] hosted 模式下 `POST /api/projects/{slug}/folders` 返回 405，且授权目录下的 `.agents/mcp` 不被发现（阶段 2 起）。

## 7. 附录

### 7.1 与业界对照（摘要）
| 维度 | Claude Code | Codex CLI | Gemini CLI | OpenCode | Hermes | 本方案 |
|---|---|---|---|---|---|---|
| Workspace 层 | 无 | 无 | 无 | 无 | 无（profile = 独立 Agent 实例） | 无 |
| Project 身份 | 路径 slug | 绝对路径 | sha256(path) | git root hash | `projects.db`：slug/name/primary_path/folders[] | slug(name) + folders[] |
| 目录授权 | 按路径 trust + allowedTools | 按路径 trust_level | trusted folders | permission 表 | 项目 folders | project_folders（read/readwrite） |
| 会话事实源 | JSONL/项目目录 | JSONL/日期目录 | JSONL/项目目录 | 单 SQLite | 单 SQLite（WAL） | 单 SQLite（WAL）/租户 |
| 索引 | history.jsonl | SQLite 派生 | 无 | 同库 | FTS5 派生 | FTS5 派生 + exports |
| 定时任务位置 | 全局 `jobs/` | 全局 sqlite | — | — | 全局 `cron/` | 租户级表，引用 Project |
| 写用户目录 | 否 | 否 | 否 | 否 | 否 | 否 |

### 7.2 术语
- **事实源**：丢了就丢了的数据；备份与恢复的对象。
- **派生**：可从事实源重建的索引、视图、导出。
- **运行时账本**：描述"此刻谁在跑"的数据；恢复时不复制，避免重放副作用。
- **frozen / interactive**：Job 的能力集合在确认时冻结；交互式对话按需发现。
