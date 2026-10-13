# Design

> **给实施者**：本文件是本 change 的权威设计。`tasks.md` 按执行顺序拆分了工作，每个任务都引用本文件的章节号（如 §5.3）。遇到本文件没覆盖的情况，按 §0 的原则处理，并在 PR 描述里写明你的判断。

## 0. 实施总原则

1. **托管模式失败即关闭（fail closed）。** hosted 下任何代码路径拿不到租户都必须抛 `TenantMissing`，绝不能回落到进程级目录或 `local`。宁可报错，不可串数据。
2. **桌面行为不变。** 桌面端（`serve_mode=desktop`）的数据位置、文件布局、API 行为在本 change 前后保持一致，现有桌面测试全部通过是每个阶段的硬门槛。
3. **隔离靠物理分开，不靠过滤。** 租户内部的代码一律当作"只有一个用户"来写，不要再加 `owner_user_id` 一类字段。
4. **凡是进程内存里按"非全局唯一 ID"存东西的地方，都必须带 `tenant_id`。** 典型的非唯一 ID：`default`、`agent_id`、`provider_id`、`skill_id`、`mcp server id`、`workspace_id`、`project_id`。UUID 生成的 run / firing / group id 全局唯一，可以不带，但访问入口必须先在当前租户库里查到记录再访问内存。
5. **asyncio 任务在 `create_task` 那一刻复制 contextvars。** 后台任务必须在 `with use_tenant(ctx):` 块**内部**创建。线程池（`loop.run_in_executor`）不复制 contextvars，线程里不允许访问租户数据，除非显式用 `contextvars.copy_context().run(...)` 包装。
6. **不做数据迁移。** 托管数据直接清空；桌面库原地保留，多出的列和表不删。
7. **标注了"实施前核对"的地方**，是本设计基于阅读代码做出的假设，实施时需要先确认；如果和假设不符，按本节原则调整，并在 PR 里说明。

## 1. 目标形态

| | Web（托管多租户） | 桌面（本地优先） |
|---|---|---|
| `serve_mode` | `hosted` | `desktop` |
| 数据 | 服务器上每用户一份数据根 | 本机 userData |
| 登录 | 必须 | 可选 |
| 租户 | `tenants/<user_id>`，`kind="hosted"` | 固定 `tenant_id="local"`，`kind="local"` |
| 平台模型 | 服务端用该用户的 `k2a_` Key 直连 key2api | 登录后经云端 `/api/platform/v1` 转发 |
| 自定义模型 | 租户自己的 `models.json`，本人可写 | 本机 `models.json` |
| 命令执行 | 只在容器沙箱 | 本地或 Docker，由用户选择 |
| MCP | 只允许远程（SSE / WebSocket） | 全部 |
| 出站请求 | SSRF 校验 | 不限（允许访问局域网模型如 Ollama） |
| 登录的含义 | 决定"你是谁、看到哪份数据" | 只决定能否使用平台模型 |

## 2. 目录布局

### 2.1 Web（`AGENT_WORKSTATION_DATA_DIR`，下称 `ROOT`，建议 `/srv/yinli/data`）

```
ROOT/
├── control/
│   └── control.db              账号、会话、验证码、provider key、tenants、平台调用审计
├── shared/
│   └── platform-models.json    平台模型能力表（管理员维护），见 §11.5
├── logs/  tmp/                 进程级
└── tenants/
    └── usr_<32hex>/            一个租户；结构与桌面 userData 完全相同
        ├── data/
        │   ├── agent_workstation.db
        │   ├── memory/         习惯 users/habits.md、专业区 agents/<id>/professional.md
        │   ├── vector_store/
        │   ├── team-runtime/runs/
        │   ├── sandboxes/
        │   └── secrets/        加密后的密钥文件（§11.3）
        ├── models.json         自定义模型层
        ├── skills/             Skill 包（含播种的系统 Skill）
        ├── subagents/
        ├── workspaces/         Workspace catalog（.agents/profiles、mcp、tools、jobs）
        └── projects/
            ├── default/        默认 Project 根
            └── <project_id>/   用户新建的 Project 根
```

### 2.2 桌面（userData，即 `AGENT_WORKSTATION_DATA_DIR`；开发模式为 `backend/`）

完全保持现状。`local` 租户的 `data_root` = 现有 `USER_DATA_ROOT`。默认 Project 根仍为现有 `DEFAULT_PROJECT_ROOT`（安装版 `userData/workspace`，开发模式仓库根）。

另外新增 / 调整：
- `hosted-account.json`：只保存 `baseUrl` 和 `email`，**不再保存 token**（token 进系统密钥存储，§11.6）。

## 3. 租户上下文（新增 `backend/src/tenancy/`）

### 3.1 `tenancy/context.py`

```python
from __future__ import annotations
import re
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal

TENANT_ID_PATTERN = re.compile(r"^usr_[0-9a-f]{32}$")
LOCAL_TENANT_ID = "local"

class TenantMissing(RuntimeError):
    """hosted 下没有租户上下文时访问租户数据。"""

@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    data_root: Path
    kind: Literal["local", "hosted"]
    user_id: str = ""          # hosted 时等于 tenant_id；local 时为空
    user_role: str = ""        # hosted 时为 auth_users.role

    @property
    def is_hosted(self) -> bool: return self.kind == "hosted"

    @staticmethod
    def local() -> "TenantContext": ...
        # data_root = runtime_paths.USER_DATA_ROOT（进程级根），tenant_id="local"

    @staticmethod
    def hosted(user_id: str, *, role: str = "user") -> "TenantContext": ...
        # 校验 TENANT_ID_PATTERN；data_root = tenants_root() / user_id

_TENANT: ContextVar[TenantContext | None] = ContextVar("yinli_tenant", default=None)

def serve_mode() -> str:            # "desktop" | "hosted"，读 settings.serve_mode
def is_hosted_mode() -> bool:

def current_tenant() -> TenantContext:
    ctx = _TENANT.get()
    if ctx is not None:
        return ctx
    if is_hosted_mode():
        raise TenantMissing("托管模式下缺少租户上下文")
    return _local_singleton()        # 进程内缓存一个 TenantContext.local()

def current_tenant_or_none() -> TenantContext | None   # 只给中间件和日志用

def set_tenant(ctx: TenantContext) -> Token: ...
def reset_tenant(token: Token) -> None: ...

@contextmanager
def use_tenant(ctx: TenantContext) -> Iterator[TenantContext]:
    token = _TENANT.set(ctx)
    try:
        yield ctx
    finally:
        _TENANT.reset(token)

def spawn_in_tenant(ctx: TenantContext, coro, *, name: str | None = None) -> asyncio.Task:
    """在 ctx 下创建任务。用于不从请求派生的后台任务。"""
    with use_tenant(ctx):
        return asyncio.create_task(coro, name=name)
```

注意：
- `tenants_root()`、`control_root()`、`shared_root()` 定义在 `runtime_paths.py`（§4），`context.py` 不要反向 import 业务模块。
- `TenantContext.hosted()` 必须校验 `TENANT_ID_PATTERN`，防止拼出 `../` 之类的路径。现有用户 ID 格式为 `usr_<uuid4.hex>`（`storage/auth_records.py:51`），符合该正则。

### 3.2 `tenancy/registry.py`（按租户分桶的缓存工具）

```python
class TenantScoped(Generic[T]):
    """按 tenant_id 懒加载、可整体驱逐的进程内状态。"""
    def __init__(self, factory: Callable[[], T]) -> None
    def get(self) -> T                     # 当前租户的实例，首次访问调用 factory()（在当前租户上下文内）
    def peek(self, tenant_id) -> T | None
    def evict(self, tenant_id: str) -> None

def evict_tenant(tenant_id: str) -> None   # 驱逐所有注册过的 TenantScoped 与 dict 缓存
def evict_all() -> None                    # 测试 teardown 用
```

所有 §6 列出的缓存都改为 `TenantScoped`，或者把 key 改成 `(tenant_id, 原 key)`，并在 `evict_tenant` 里注册清理函数。

### 3.3 中间件

- **桌面**：不需要中间件。ContextVar 未设置时 `current_tenant()` 自动返回 `local`。
- **托管**：`server/auth/middleware.py::HostedSessionMiddleware` 改为：
  1. 公开路径（`PUBLIC_PATHS`）直接放行，**不设置租户**。公开接口不得访问租户数据。
  2. 校验会话（`current_user(token)`，读控制面）。失败返回 401。
  3. 读 `tenants` 行。不存在则调用 `lifecycle.ensure_tenant(user)`（兜底，正常情况下验证时已经创建）；`status != 'active'` 返回 403 `{"detail": "账号已停用"}`。
  4. `token = set_tenant(TenantContext.hosted(user_id, role=user.role))`，执行下游，`finally: reset_tenant(token)`。
  5. **删除**：`unassigned_count()` 检查、`HostedOwnershipPending` 409 分支、`set_owner/reset_owner`。
- `scope["state"]["auth_user"]` 保留，供需要角色判断的接口使用。

## 4. 路径改造（`config/runtime_paths.py`）

### 4.1 新的函数式 API

保留进程级常量：`INSTALL_BACKEND_ROOT`、`INSTALL_PROJECT_ROOT`、`USER_HOME_ROOT`、`USING_CUSTOM_DATA_ROOT`、`USER_DATA_ROOT`（进程根，hosted 下即 `ROOT`）、`LOG_ROOT`、`TEMP_ROOT`。

新增进程级函数：

```python
def tenants_root() -> Path:   return USER_DATA_ROOT / "tenants"
def control_root() -> Path:   return USER_DATA_ROOT / "control"
def shared_root() -> Path:    return USER_DATA_ROOT / "shared"
```

把以下**模块常量删除**，改为读取 `current_tenant().data_root` 的函数：

| 旧常量 | 新函数 | 解析结果 |
|---|---|---|
| `DATA_ROOT` | `data_root()` | `tenant_root()/data` |
| `MEMORY_ROOT` | `memory_root()` | `data_root()/memory` |
| `VECTOR_STORE_ROOT` | `vector_store_root()` | `data_root()/vector_store` |
| `TEAM_RUNTIME_ROOT` | `team_runtime_root()` | `data_root()/team-runtime/runs` |
| `SANDBOX_ROOT` | `sandbox_root()` | `data_root()/sandboxes` |
| `SKILL_PACKAGES_ROOT` | `skill_packages_root()` | `tenant_root()/skills` |
| `LEGACY_SKILL_PACKAGES_ROOT` | `legacy_skill_packages_root()` | `data_root()/skill_packages` |
| `SUBAGENT_ROOT` / `USER_SUBAGENT_ROOT` | `subagent_root()` | `tenant_root()/subagents` |
| `LEGACY_SUBAGENT_ROOT` | `legacy_subagent_root()` | `data_root()/subagents` |
| `WORKSPACES_CATALOG_ROOT` | `workspaces_catalog_root()` | `tenant_root()/workspaces` |
| `DEFAULT_PROJECT_ROOT` | `default_project_root()` | local：现有逻辑；hosted：`tenant_root()/projects/default` |
| —— | `tenant_root()` | `current_tenant().data_root` |
| —— | `projects_root()` | hosted：`tenant_root()/projects`；local：不使用（桌面 Project 可在任意路径） |
| —— | `user_home_root()` | local：`USER_HOME_ROOT`；hosted：`None`（§12.7） |

`ensure_runtime_directories()` 拆成两个：
- `ensure_process_directories()`：`USER_DATA_ROOT`、`LOG_ROOT`、`TEMP_ROOT`；hosted 时再加 `control_root()`、`shared_root()`、`tenants_root()`。
- `ensure_tenant_directories()`：当前租户的 `data_root()`、`default_project_root()`、`skill_packages_root()`、`subagent_root()`、`workspaces_catalog_root()`，并执行两个 legacy 迁移函数（它们改为使用函数式路径）。按 `tenant_id` 记忆，每个租户只执行一次。

**为什么不保留常量做兼容**：保留常量会让漏改的地方继续静默使用进程根，在 hosted 下就是串数据。删除常量后，漏改的地方会在 import 时直接报 `ImportError`，可以靠测试一次性找全。

### 4.2 调用点清单（必须全部修改）

以下是 `grep` 出的全部使用点（`backend/src`，不含测试）。改成在**函数体内调用**新函数。**禁止在模块顶层求值**（例如 `DATA_DIR = str(data_root())`），否则 import 时就会固定成某一个租户，或者在 hosted 下直接抛 `TenantMissing`。

| 文件 | 现状 | 改法 |
|---|---|---|
| `storage/database.py:17-21,52,65,84,124,133,136,141-142,415,429,535,2071` | `DATA_ROOT`、`DEFAULT_PROJECT_ROOT`、`USER_DATA_ROOT`、`PROJECT_ROOT`、`DB_PATH`、`DEFAULT_GUARDRAILS` | 见 §5 |
| `config/model_catalog.py:18-20` 及所有 `MODELS_PATH` | 模块常量 | `def models_path() -> Path: return tenant_root() / "models.json"`；锁文件 `models_path().with_suffix(".lock")`；`_secured_windows_files` 以路径为 key，可以保持 |
| `config/secret_store.py:22-26` | `SECRET_DIR = DATA_ROOT / "secrets"` | `secret_dir()`，见 §11.3 |
| `memory/markdown_store.py:12,69` | `self.root = root or MEMORY_ROOT` | `self.root = root or memory_root()`（构造时取，`MarkdownMemoryStore()` 每次调用都新建实例，可以接受） |
| `catalog/paths.py:27,164` | `runtime_paths.USER_DATA_ROOT` | 27 行改为 `workspaces_catalog_root()`；164 行的 extra scope 拒绝列表改为 `tenant_root()` |
| `catalog/index.py:29-38` | `USER_DATA_ROOT`、`USER_HOME_ROOT` | `tenant_root()`、`user_home_root()` |
| `catalog/skill_seed.py:9,19,33,36` | `SKILL_PACKAGES_ROOT` | `skill_packages_root()` |
| `catalog/skills.py:16,169` | 同上 | 同上；`user_home_root` 为 `None` 时跳过 user scope（§12.7） |
| `skills/registry.py:28,69,536-537,699,709` | `DATA_ROOT`、`USER_DATA_ROOT`、`USER_HOME_ROOT`；`DATA_DIR` 模块常量 | 默认参数改为 `None`，函数体内解析；`DATA_DIR` 删除，改函数 |
| `server/routes/skills.py:23-25` | 同上 | 同上 |
| `server/routes/skill_packages.py:16-19` | `SKILL_PACKAGES_DIR = str(...)` 模块常量 | 删除常量，改函数 |
| `server/routes/mcp.py:34` | `DATA_ROOT` | 函数 |
| `mcp/registry.py:28,93` | `DATA_DIR = str(DATA_ROOT)` | 函数 |
| `server/routes/agents.py:42,100` | `DATA_DIR = str(DATA_ROOT)` | 函数 |
| `server/routes/chat/models.py:12-14` | `CHAT_DATA_PATH = DATA_ROOT / "chat_sessions.json"` | `def chat_data_path()`；只在旧 JSON 导入时使用（§7.5） |
| `server/routes/chat/team_runtime/store.py:17,79,140` | `TEAM_RUNTIME_ROOT` 作为默认值 | `root or team_runtime_root()` |
| `server/routes/chat/team_runtime/maintenance.py:8,20,63` | 默认参数 `root: Path = TEAM_RUNTIME_ROOT` | 改为 `root: Path \| None = None`，函数体内解析 |
| `server/routes/chat/runtime.py:117-126` | `_hosted_personal_project` 用 `DEFAULT_PROJECT_ROOT` | **整段删除**（§12.4.6） |
| `server/routes/chat/completion.py:28,418` | `database.PROJECT_ROOT` | `default_project_root()` |
| `agents/factory.py:49,529,537` | `SANDBOX_ROOT` | `sandbox_root()`；hosted 下不再使用（§12.4） |
| `agents/runtime_types.py:7,11` | `WORKSPACE_DIR = str(DEFAULT_PROJECT_ROOT)` 模块常量 | 删除常量；`grep WORKSPACE_DIR` 全部改为调用 `default_project_root()` |
| `subagents/models.py:10,20` | `SUBAGENT_USER_DIR = str(USER_SUBAGENT_ROOT)` | 函数 |
| `subagents/registry.py:10,26,45` | `user_home_dir: Path = USER_HOME_ROOT` | 默认 `None`；`user_home_root()` 为 `None` 时跳过 user scope |
| `vector_store/providers/zvec.py:11,228` | `VECTOR_STORE_ROOT` | `vector_store_root()` |
| `runtime/agent_python.py:33` | `USER_DATA_ROOT / VENV_DIRNAME` | 保持进程级；hosted 下不调用（§12.4.7） |
| `server/auth/desktop_account.py:9-11` | `ACCOUNT_FILE` | 保持进程级（只在桌面使用），见 §11.6 |
| `server/auth/ownership.py` | —— | **整个文件删除**（§8.5） |

实施完后执行 `grep -rnE "\b(DATA_ROOT|MEMORY_ROOT|VECTOR_STORE_ROOT|TEAM_RUNTIME_ROOT|SANDBOX_ROOT|SKILL_PACKAGES_ROOT|SUBAGENT_ROOT|USER_SUBAGENT_ROOT|WORKSPACES_CATALOG_ROOT|DEFAULT_PROJECT_ROOT|MODELS_PATH|DB_PATH|PROJECT_ROOT|SECRET_DIR|WORKSPACE_DIR|DATA_DIR|SKILL_PACKAGES_DIR|SUBAGENT_USER_DIR|CHAT_DATA_PATH)\b" backend/src`，结果只能出现在注释或 `runtime_paths.py` 的进程级定义里。

## 5. 数据库层（`storage/database.py`）

### 5.1 按路径维护状态

```python
_state_lock = RLock()
_initialized_paths: set[Path] = set()
_path_locks: dict[Path, RLock] = {}

def db_path() -> Path:
    ctx = current_tenant()
    if ctx.kind == "local":
        return _local_db_path()            # 现有 _db_path() 逻辑：尊重 settings.database_url
    return ctx.data_root / "data" / "agent_workstation.db"   # hosted 忽略 database_url

def _lock_for(path: Path) -> RLock: ...   # 在 _state_lock 下取 / 建

class _TenantLockProxy:
    """兼容 `with database._lock:` 的旧写法：进入时按当前租户解析到对应路径的 RLock。"""
    def __enter__(self): ...               # 需要保存本次取到的锁对象，以便在 __exit__ 时释放同一把锁
    def __exit__(self, *exc): ...
_lock = _TenantLockProxy()

def _connect() -> sqlite3.Connection:
    ensure_tenant_directories()
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")   # 注意 §5.4 的执行顺序
    return conn

def ensure_db() -> None:
    path = db_path()
    if path in _initialized_paths:
        return
    with _lock_for(path):
        if path in _initialized_paths:
            return
        ...                                # 现有建表、迁移、播种逻辑
        _initialized_paths.add(path)

def forget_db(path: Path) -> None          # 删除租户和测试 teardown 时调用
```

**`_TenantLockProxy` 必须是线程安全的**：同一个 proxy 对象可能被多个线程同时进入，不能把"本次取到的锁"存在 proxy 实例属性上。用 `threading.local()` 维护一个锁栈，或者把 `__enter__` 返回的锁对象记在调用方。推荐实现：`threading.local` 里放一个 list，`__enter__` 时 append 并 acquire，`__exit__` 时 pop 并 release。

现有外部用法（全部依赖 `_lock` 和 `_connect` 这两个名字）都可以继续工作，不需要改：
- `server/routes/chat/projection/projection_writer.py:67-68`
- `server/routes/chat/projection/domain_events.py:52-53`
- `storage/timeline_records.py:30,73`
- 各 `_xxx_record_store()` 工厂（约 16 个）传入的 `lock=_lock`、`connect=_connect`

`storage/auth_records.py`（17 处）和 `server/auth/platform_models.py`（2 处）**改为使用控制面**（§8）。

### 5.2 删除或改为函数的模块级值

- `DB_PATH`：删除。需要路径的地方调用 `db_path()`。`init_db()` 中的打印改为在启动遍历租户时打印。
- `PROJECT_ROOT`：删除，改为 `default_project_root()`。
- `DEFAULT_GUARDRAILS`：改为 `def default_guardrails() -> dict`，其中 `filesystem_scope = str(default_project_root())`。`_normalize_guardrails` 改为在调用时取 `default_guardrails()`。`grep -rn "DEFAULT_GUARDRAILS\|database.PROJECT_ROOT"` 全部改掉。
- `_initialized`：删除，改为 `_initialized_paths`。
- `ensure_db()` 中 `conn.executescript(AUTH_SCHEMA_SQL)`：**删除**。账号表只在控制面创建（§8）。
- `ensure_db()` 中第 383-385 行附近为 5 张表 `_ensure_column(..., "owner_user_id", "TEXT")` 的循环：**删除**（桌面旧库已有的列保留不动）。
- 默认 Project 的 `root_path` 播种（约 405-431 行）改用 `default_project_root()`。hosted 下 `USING_CUSTOM_DATA_ROOT` 分支不适用（hosted 的根目录永远是新的）。

### 5.3 测试夹具

`backend/test/conftest.py` 新增并调整：

```python
@pytest.fixture(autouse=True)
def local_tenant(tmp_path, monkeypatch):
    """每个测试一份全新的 local 租户数据根。"""
    from src.config import runtime_paths
    from src.tenancy.context import TenantContext, use_tenant
    from src.tenancy.registry import evict_all
    from src.storage import database
    root = tmp_path / "tenant-local"
    root.mkdir()
    monkeypatch.setattr(runtime_paths, "USER_DATA_ROOT", root)   # local 租户 data_root 指向这里
    ctx = TenantContext.local()                                   # 实现上不要缓存跨测试的单例，或在这里重置单例
    with use_tenant(ctx):
        yield ctx
    database.forget_all()
    evict_all()
```

- `isolated_model_catalog` 改为 `def isolated_model_catalog(local_tenant): ...`，删除 `monkeypatch.setattr(model_catalog, "MODELS_PATH", ...)`，直接 `_write(...)` 写入当前租户。
- 44 个测试文件中 `monkeypatch.setattr(database, "DB_PATH", ...)` 和 `monkeypatch.setattr(database, "_initialized", False)` 的写法全部删除（共约 87 处），改为依赖 autouse fixture。需要两份库的测试显式 `with use_tenant(TenantContext.hosted(...))`。
- 25 个测试文件直接 patch 路径常量（`USER_DATA_ROOT`、`DATA_ROOT`、`MEMORY_ROOT`、`MODELS_PATH`、`SKILL_PACKAGES_ROOT`、`TEAM_RUNTIME_ROOT` 等），统一改为依赖 `local_tenant` 或给函数传参。
- 新增 hosted 测试工具 `backend/test/hosted_support.py`：
  - `hosted_env(tmp_path, monkeypatch)` fixture：`serve_mode=hosted`，`auth_email_mode=log`，`USER_DATA_ROOT=tmp_path/"root"`，`AGENT_WORKSTATION_SECRET_KEY` 设一个测试用 Fernet key，初始化控制面，沙箱后端替换为 `FakeSandbox`（§12.4.8）。
  - `make_user(client, email) -> (token, user_id)`：注册、验证、登录。
  - `tenant_of(user_id) -> TenantContext`。

### 5.4 外键与执行顺序（重要陷阱）

`ChatSessionRecordStore.save_store` 现在是"`DELETE FROM chat_messages; DELETE FROM chat_sessions;` 再全量插入"。schema 里 `run_events.session_id` 声明了 `REFERENCES chat_sessions(id) ON DELETE CASCADE`。**如果先开 `foreign_keys=ON`，每次保存会话都会把所有 run_events 级联删光。**

因此执行顺序必须是：
1. 先完成 §7（ChatStore 增量写），并删除 `save_store` / `save_chat_store`。
2. `grep -rn "DELETE FROM [a-z_]*\"\|DELETE FROM [a-z_]*$" backend/src/storage`，找出所有不带 `WHERE` 的整表删除，逐一确认不会在开启外键后造成级联误删。
3. 再在 `_connect()` 中开启 `PRAGMA foreign_keys = ON`。
4. 跑全量测试。外键报错（`FOREIGN KEY constraint failed`）说明存在"先插子表后插父表"的顺序问题，必须修正写入顺序，**不要关掉外键绕过**。
5. 桌面旧库可能已有孤儿行。外键只在写子表时检查，不会因为存量孤儿行报错。启动时对 local 租户执行一次 `PRAGMA foreign_key_check`，只记录日志数量，不修复。

## 6. 进程内缓存清单

`grep -rnE "^_?[a-zA-Z_]+\s*(:\s*[^=]+)?=\s*(\{\}|dict\(|OrderedDict\(|set\(\)|defaultdict)"` 和 `@lru_cache` 得到的全部清单，逐项处理：

| 位置 | key | 处理 |
|---|---|---|
| `server/routes/agents.py:145 _agents` + **279 行 import 时执行 `_load_agents()`** | agent_id（含 `default`） | 改为 `TenantScoped(lambda: _load_agents_for_current_tenant())`；**删除 import 时加载**；所有 `_agents[...]` 读写改为 `_agents_registry.get()[...]`；`_ensure_system_agents` 在 factory 内执行 |
| `mcp/registry.py:245 _mcp_servers`、`246 _mcp_header_refs` + **533 行 import 时 `_load_user_mcp_servers()`** | server id | 同上；`_builtin_mcp_servers` 只在 local 租户合并进去（§12.3） |
| `skills/registry.py:411 _skills`、`74 _external_skill_overrides` + **865 行 import 时 `_load_user_skills()`** | skill id | 同上 |
| `agents/factory.py:77-79 _agent_cache`、`_session_agents`、`_agent_last_used` | agent / session 组合 key | key 前加 `tenant_id`（不要用 TenantScoped，因为 LRU 淘汰逻辑是全局的） |
| `memory/prompt_cache.py:17-19` 三个缓存 | session / agent / user | key 前加 `tenant_id`；`invalidate_memory_prompts(...)` 只清当前租户 |
| `server/routes/chat/memory_prompt_cache.py`��实施前核对是否有模块级缓存） | —— | 同上 |
| `config/secret_store.py:27 _cache`、`28 _memory_secrets` | account | key 改为 `(tenant_id, account)`（§11.3） |
| `config/model_catalog.py:23 _secured_windows_files` | Path | 路径本身已区分租户，保持 |
| `vector_store/registry.py:13 @lru_cache(maxsize=1) get_vector_store_backend` | 无 | 改为 `TenantScoped(...)` |
| `server/routes/chat/background_runs.py:98 _runs` | run_id（uuid） | `ChatBackgroundRun` 新增字段 `tenant_id`；`get/list/cancel/steer/subscribe` 过滤 `run.tenant_id == current_tenant().tenant_id`，不匹配按"不存在"处理（§10.6） |
| `server/routes/chat/models.py:29-30 PENDING_INTERACTIONS`、`PENDING_AGENTS` | interaction id | 存入时记录 `tenant_id`；解析时校验，不匹配按"不存在"处理 |
| `server/routes/chat/team_runtime/registry.py:7 _active_services` | run id | 只能通过已经校验过租户的 run 访问；存 `tenant_id` 做防御性断言 |
| `server/routes/chat/projection/projection_runner.py:15 _STREAM_LOCKS` | stream_key（含 run id） | 保持 |
| `server/routes/workflows.py:31-32 _SUBSCRIBERS`、`_RUN_TASKS` | workflow run id | 保持；所有入口先 `get_workflow_run(id)`（读租户库），查不到即 404 |
| `group_studio/sessions.py:23-26` 四个 dict | group id / run id | 保持；入口先在租户库查 studio；`WorkerSession` 记录 `tenant_id` 做断言 |
| `group_studio/stream.py:10 _SUBSCRIBERS` | group id | 同上 |
| `group_studio/tools_worker.py:42 _WORKER_CONTEXTS` | (group_id, member_id) | 保持 |
| `notifications/hub.py` `_subscribers` 列表 | (project_id, queue) | 改为 `(tenant_id, project_id, queue)`；`publish(event)` 时取 `current_tenant().tenant_id`，只投递给同租户订阅者 |
| `subagents/tool_policy.py:24 REQUIRED_INTERNAL_TOOLS` | 常量集合 | 保持 |
| `scheduler/ticker.py` `_firing_tasks`、`_inflight` | firing id | 保持；另记 `firing_id -> tenant_id`，供删除租户时取消（§9.4） |

**import 时加载是最大的陷阱**：hosted 下 import 阶段没有租户，`_load_agents()` 等会抛 `TenantMissing` 导致进程启动失败；桌面下它们会在 `local_tenant` fixture 生效前读取错误的目录。三处 import 时加载必须全部删除，改为首次访问时懒加载。

`server/routes/agents.py` 里所有 `global _agents` 赋值（如 `_sync_agents_from_catalog` 第 260 行 `_agents = {...}`）改为 `_agents_registry.replace(new_dict)`（在 `TenantScoped` 上提供 `replace()`）。

## 7. ChatStore 增量写

### 7.1 Schema 补齐

`ensure_db()` 中追加：

```python
_ensure_column(conn, "chat_messages", "files", "TEXT NOT NULL DEFAULT '[]'")
_ensure_column(conn, "chat_messages", "clarification_requests", "TEXT NOT NULL DEFAULT '[]'")
_ensure_column(conn, "chat_messages", "clarification_resolved", "INTEGER NOT NULL DEFAULT 0")
conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_session_order ON chat_messages(session_id, sort_order)")
conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_project_kind ON chat_sessions(project_id, kind, updated_at DESC)")
```

这是一个**已有的数据丢失 bug**：`StoredChatMessage` 有 `files`、`clarification_requests`、`clarification_resolved` 字段，但 `save_store` / `load_store` 从未读写它们，重启后附件名和澄清状态全部丢失（`run_context.snapshot_session_history` 依赖 `message.files`）。

### 7.2 `ChatSessionRecordStore` 新方法（`storage/chat_session_records.py`）

```python
def get_session(self, session_id: str) -> dict | None
def list_sessions(self, *, project_id: str | None = None, kind: str | None = None) -> list[dict]
    # ORDER BY updated_at DESC；kind=None 表示全部
def insert_session_if_absent(self, session: dict) -> dict
    # INSERT OR IGNORE，然后 SELECT 返回库里的那一行（可能是并发先插入的那一行）
def update_session(self, session_id: str, **fields) -> None
    # 白名单字段：title, agent_id, agent_name, mode, workspace_id, project_id,
    #           model_config_id, updated_at, message_count
def append_message(self, session_id: str, message: dict) -> dict
    # 在同一个连接上执行 BEGIN IMMEDIATE：
    #   sort = COALESCE(MAX(sort_order), -1) + 1 WHERE session_id=?
    #   INSERT chat_messages(..., files, clarification_requests, clarification_resolved, sort_order)
    #   UPDATE chat_sessions SET message_count=(SELECT COUNT(*) ...), updated_at=? WHERE id=?
    #   COMMIT
def list_messages(self, session_id: str) -> list[dict]       # ORDER BY sort_order
def update_message_snapshot(self, message_id: str, snapshot: dict) -> None
def delete_messages(self, session_id: str, message_ids: list[str]) -> None   # 删除后重算 message_count
def delete_session(self, session_id: str) -> None            # 先删 messages 再删 session，同一事务
```

- 单行转换函数 `_session_from_row` / `_message_from_row` 统一处理 JSON 列；JSON 损坏时用默认值并 `logger.warning`，**不跳过、不删除整行**。
- `load_store` / `save_store` **删除**；`database.py` 中对应的 `load_chat_store` / `save_chat_store` 包装函数也删除。
- `database.py` 增加对应的模块级包装函数（与现有风格一致，如 `get_chat_session(...)`、`append_chat_message(...)`）。

### 7.3 `server/routes/chat/store.py` 改写

| 函数 | 新实现 |
|---|---|
| `_load_store`、`_load_store_lenient`、`_save_store` | 删除 |
| `_append_user_message` | `get_session`；不存在则 `insert_session_if_absent`（标题由内容生成），存在则 `update_session(agent_id, agent_name, mode, workspace_id, project_id, model_config_id, updated_at)`；最后 `append_message`。删除 `ownerUserId` 相关代码 |
| `ensure_chat_session` | `insert_session_if_absent`；返回行的 `project_id` 与请求不一致时抛 `ChatSessionProjectConflict` |
| `ensure_scheduled_job_session` | `insert_session_if_absent(kind="scheduled_job")` |
| `_append_assistant_message` | `append_message` |
| `_update_latest_assistant_display_snapshot` | `list_messages(session_id)`（只读这一个会话），沿用现有算法找目标消息和重复占位消息，然后 `update_message_snapshot` + `delete_messages` |

### 7.4 其他调用方

| 位置 | 改法 |
|---|---|
| `server/routes/chat/run_context.py:124 snapshot_session_history` | `list_messages(session_id)[-limit:]` |
| `server/routes/chat/handlers/send.py:116` | `get_session(session_id)` |
| `server/routes/chat/handlers/sessions.py` 全部 handler | `list_sessions(project_id=..., kind=...)` / `get_session` / `list_messages` / `delete_session` / `update_session`；**删除 `_session_visible`** 及所有调用 |
| `runtime/chat_session.py:29 find_chat_session` | `get_session` |
| `group_studio/service.py:194` | 不变（调用 `find_chat_session`） |

handler 的返回结构（`ChatSession` / 消息字典）保持不变，前端无需改动。

### 7.5 旧 JSON 导入

`_load_store` 里现有的 `migrate_json_object_file(CHAT_NAMESPACE, CHAT_STORE_KEY, CHAT_DATA_PATH)` 和"`json_store` 遗留记录回写"逻辑，改成 `import_legacy_chat_store_once()`：
- 在 `ensure_db()` 末尾调用。
- 用 `json_store('schema_migrations', 'chat_store_incremental_v1')` 做一次性标记。
- 逐条用 `insert_session_if_absent` / `append_message` 导入。
- 导入后保留原 JSON 文件不删除。

## 8. 控制面（新增 `storage/control_db.py`）

### 8.1 模块

```python
_control_lock = RLock()
_control_initialized = False

def control_db_path() -> Path: return control_root() / "control.db"
def control_connect() -> sqlite3.Connection    # WAL、busy_timeout、foreign_keys
def ensure_control_db() -> None                # 只在 hosted 下调用；desktop 调用直接抛 RuntimeError
```

Schema = 现有 `AUTH_SCHEMA_SQL`（`auth_users`、`auth_sessions`、`auth_email_codes`、`auth_provider_keys`）+：

```sql
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,               -- = auth_users.id
    status TEXT NOT NULL,              -- active | disabled | deleting | deleted
    quota_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS platform_call_audit (     -- 从 server/auth/platform_models.py::_ensure_audit_table 迁来
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    model_config_id TEXT NOT NULL,
    client_kind TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    status INTEGER NOT NULL DEFAULT 200,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_call_audit_user ON platform_call_audit(user_id, created_at DESC);
```

`auth_schema.py` 保留，只由 `control_db.py` 引用。

### 8.2 `storage/control_records.py`（新增）

`list_active_tenants()`、`get_tenant(id)`、`insert_tenant(id)`、`set_tenant_status(id, status)`、`get_tenant_quota(id) -> TenantQuota`、`record_platform_call(...)`。

### 8.3 迁移现有代码

- `storage/auth_records.py`：17 处 `with database._lock, database._connect() as conn:` 改为 `with control_db._control_lock, control_db.control_connect() as conn:`。每个函数开头的 `database.ensure_db()` 改为 `control_db.ensure_control_db()`。
- `server/auth/platform_models.py:30-70`：审计表改用控制面，`_ensure_audit_table` 删除。
- `server/main.py` lifespan：hosted 下先 `ensure_control_db()`，再 `ensure_default_admins()`；desktop 下两者都不调用。
- `server/auth/service.py::ensure_default_admins`：内部调用的 `database.ensure_db()` 一并改掉。

### 8.4 desktop 下的账号接口

desktop 下控制面不存在。`/api/auth/register|verify|login|logout|me|usage|forgot-password|reset-password|heartbeat` 和 `/api/admin/*` 在 desktop 下返回 404 `{"detail": "仅托管服务可用"}`（在路由里统一加依赖 `require_hosted_mode`）。`/api/auth/config` 和 `/api/auth/desktop/session*` 继续可用。

### 8.5 删除清单（行级归属方案）

- `backend/src/server/auth/ownership.py`：整个文件删除。
- `backend/src/server/auth/assign_owners.py`：删除。
- `server/auth/routes.py:269 POST /api/admin/ownership/assign`：删除。
- `server/auth/service.py:247` 一带的 `assign_unowned` / `unassigned_count` 引用：删除。
- 所有 `from src.server.auth.ownership import ...`（见 §16 清单）：删除，并按各处说明替换。
- `server/routes/chat/models.py:189-197` 的 `owner_user_id` 字段与 validator：删除。
- `storage/chat_session_records.py` 中 `ownerUserId` / `owner_user_id` 的读写：删除。
- `storage/scheduled_job_records.py:100-112`、`storage/group_studio_records.py:315-327` 的归属补写：删除。
- `storage/workspace_project_records.py:12-28` 的 `_keep` / `_visible` / `_stamp` 及调用：删除（列表、读取恢复为不过滤）。
- `runtime_context.py:56-66` 的托管兜底分支（`ensure_hosted_home` 那段）：删除。
- `server/routes/workspaces.py:123-150,178-180` 的 `_hosted_owner` / `_allocate_hosted_project` / `_reject_hosted_escape` / `ensure_hosted_home`：按 §9.3 重写。
- `runtime/governance.py:106-111 _hosted_memory_user`、`server/routes/memory_api/live.py:36-44 _habit_user_id`：删除（§10.5）。
- `backend/test/test_hosted_ownership.py`：删除，改由 `test_tenant_isolation.py`（§17）覆盖。
- `openspec/changes/hosted-account-access/tasks.md` 的 4.1、4.5：不修改该文件，在本 change 落地 PR 里说明已被取代。

## 9. 租户生命周期（新增 `tenancy/lifecycle.py`）

### 9.1 创建 `ensure_tenant(user: dict) -> TenantContext`

触发点：
1. `server/auth/service.py` 中邮箱验证成功处（现在 75-77 行调用 `ensure_hosted_home`）。
2. 登录成功处（现在 97-99 行）。
3. 中间件兜底（§3.3）。

步骤（必须幂等，可以重复调用）：
1. 控制面 `INSERT OR IGNORE tenants(id, status='active', quota_json=默认配额)`。
2. `ctx = TenantContext.hosted(user_id, role=...)`；`with use_tenant(ctx):`
3. `ensure_tenant_directories()`；`ensure_db()` 会建表并创建 `default` Workspace 和 `default` Project，根目录为 `tenant_root()/projects/default`。
4. `seed_system_skills()`：把系统 Skill 包复制到租户的 `skills/`。
5. 访问一次 Agent 注册表，触发懒加载里的 `_ensure_system_agents()`，写入默认 Agent 和定时任务 Agent。
6. 写入 `json_store` 默认值：定时任务开关（与桌面默认一致，调用现有 `ensure_desktop_scheduled_jobs_defaults` 的逻辑，改名为 `ensure_scheduled_jobs_defaults`）。
7. **重新绑定资源白名单**：对 `default` Project 再执行一次 `_bind_workspace_resources(conn, project_id="default", workspace_id="default", now=...)`。

   **陷阱**：`ensure_db()` 创建 Project 时 Agent / Skill 还不存在，绑定结果是空的。不补这一步，新用户的默认 Project 里一个 Agent 都选不到。
8. 若尚未写入 `models.json`，写一个空目录：`providers=[]`、`models=[]`、`defaultModelId=""`。**实施前核对**：`model_catalog` 的校验是否允许空默认值；如果不允许，在 hosted 下放宽为允许空（§11.2 的默认模型解析会兜底）。
9. 调用 `key2api.ensure_provider_key(user)`。失败只记日志，不阻塞（`/api/models/provided` 会再次懒重试）。

### 9.2 Project 根目录规则（hosted）

- 新建 Project：服务端分配 `projects_root()/<project_id>`，忽略客户端传入的 `rootPath`。
- 修改 Project：`rootPath` 字段在 hosted 下**一律拒绝**（422 `"Web 端不能修改 Project 目录"`）。
- `instructionsFile` 仍允许修改，必须位于 Project 根目录内（沿用现有校验）。
- 所有使用 Project 根目录的地方额外断言：`Path(root).resolve().is_relative_to(tenant_root().resolve())`，失败即抛错（防御性检查）。

### 9.3 `server/routes/workspaces.py` 重写要点

- 删除 `_hosted_owner`、`_allocate_hosted_project`、`_reject_hosted_escape`、`list_workspace_records` 中的 `ensure_hosted_home()`。
- 新增 `_hosted_project_root(project_id) -> str`：`is_hosted_mode()` 时返回 `projects_root()/project_id`（mkdir），并预先生成 project_id。
- `bootstrap_workspace`、`create_project_record`：hosted 下用 `_hosted_project_root`；desktop 维持原逻辑。
- `update_project_record`：hosted 下如果请求包含 `rootPath`，返回 422。
- `upload_project_file`：加配额检查（§12.6）。

### 9.4 停用 / 启用 / 删除

`disable_tenant(user_id)`：
1. `tenants.status='disabled'`。
2. 吊销该用户全部会话（沿用现有逻辑）。
3. key2api 同步 `disabled`（沿用现有逻辑）。
4. `cancel_tenant_activity(user_id)`：取消 `_runs` 中 `tenant_id` 匹配的 Run，取消 ticker 中属于该租户的 firing task，取消群聊 worker 任务。
5. ticker 下一轮自然跳过（只遍历 `active`）。

`enable_tenant(user_id)`：`status='active'`，key2api 同步 `active`。

`delete_tenant(user_id)`：
1. `status='deleting'`，然后执行 `disable_tenant` 的第 2-4 步。
2. 删除沙箱容器：按 label `yinli.tenant=<user_id>` 删除（§12.4）。
3. `evict_tenant(user_id)`；`database.forget_db(path)`；从 `ensure_tenant_directories` 的记忆集合中移除。
4. 删除目录：`root = (tenants_root()/user_id).resolve()`，断言 `root.parent == tenants_root().resolve()` 且 `root.name == user_id` 且符合 `TENANT_ID_PATTERN`，然后 `shutil.rmtree(root)`。
5. `status='deleted'`，写 `deleted_at`；调用现有 `auth_records.delete_account`。
6. 不删除 key2api 账号（沿用现有约定）。

`server/auth/service.py` 中 `disable_user` / `enable_user` / `delete_user` 改为调用上述函数，删除 `purge_hosted_user`。

## 10. 后台任务

### 10.1 启动（`server/main.py` lifespan）

```python
async def lifespan(app):
    assert_hosted_config()          # 扩展：SMTP（现有）+ SECRET_KEY + 沙箱配置（§12.4.1），hosted 缺失即启动失败
    ensure_process_directories()
    if is_hosted_mode():
        ensure_control_db()
        ensure_default_admins()
    for ctx in startup_tenants():   # desktop: [local]；hosted: list_active_tenants()
        with use_tenant(ctx):
            try:
                ensure_tenant_directories(); ensure_db()
                reconcile_interrupted_subagent_invocations()
                reconcile_interrupted_file_tasks([...list_projects(include_archived=True)...])
                maintain_team_runtime_runs(...)
                bootstrap_tenant_scheduler()        # 原 bootstrap_scheduler 中与租户相关的部分，§10.2
                reconcile_interrupted_studios()     # 内部 create_task，必须在 with 块内执行
            except Exception:
                logger.exception("租户启动对账失败 tenant=%s", ctx.tenant_id)   # 单个租户失败不阻塞其他租户
    get_job_scheduler().start_if_needed()           # §10.2
    if not is_hosted_mode():
        ensure_agent_venv()                         # hosted 不需要宿主 venv（命令都在沙箱里执行）
    yield
    ...
```

### 10.2 调度器（`scheduler/ticker.py`、`scheduler/settings.py`）

- `bootstrap_scheduler()` 拆分：
  - `bootstrap_tenant_scheduler()`：在当前租户内执行 `ensure_scheduled_jobs_defaults`、job 定义同步、`reconcile_interrupted_chat_runs`、`reconcile_interrupted_job_runs`。
  - 进程级启动 ticker：desktop 下沿用"`is_scheduled_jobs_enabled()` 为真才启动"；hosted 下 ticker **总是启动**，每个租户的开关在 tick 内判断。
- `JobScheduler.tick()` 改为：

```python
for ctx in iter_tick_tenants():                 # desktop: [local]；hosted: list_active_tenants()
    with use_tenant(ctx):
        if not is_scheduled_jobs_enabled():
            continue
        try:
            stats += self._tick_current_tenant(now)   # 原 tick 主体；_dispatch_due_job 内的 create_task 在 with 块内执行
        except Exception:
            logger.exception("tick 失败 tenant=%s", ctx.tenant_id)
```

- `_track(task, firing_id)` 同时记录 `self._firing_tenants[firing_id] = current_tenant().tenant_id`，供 `cancel_tenant_activity` 使用。
- `set_running(enabled)`（设置页开关）：hosted 下**不停止进程级 ticker**，只写当前租户的设置；desktop 维持原行为。
- hosted 下租户很多时，每次 tick 都要打开每个租户的库。可以接受（SQLite 打开开销在毫秒级）；如有性能问题，后续可在控制面维护 `next_due_at` 索引，本 change 不做。

### 10.3 Firing 与定时任务会话

- `run_firing`、`start_scheduled_chat_run`、`launch_scheduled_chat_run` 不需要显式传租户：它们由 tick 在 `with use_tenant` 块内 `create_task` 产生，会继承上下文。
- **验证点**：hosted 下使用 `platform:` 模型的定时任务能正常解析绑定（以前因 `current_owner_id()` 为空而失败）。§11.2 的平台绑定改为从 `current_tenant().user_id` 取用户。
- `script` 模式 firing 在 hosted 下走沙箱（§12.4.5）。

### 10.4 群聊

- `group_studio/scheduler.py:45,62`、`group_studio/service.py:634`、`group_studio/events.py:66` 中的 `loop.create_task(...)`：调用点都在请求处理或已进入租户的后台任务中，会继承上下文。**实施前核对**：确认它们没有在 `use_tenant` 块之外、或者从 `call_soon` 回调里被调用；如果有，改为 `spawn_in_tenant(current_tenant(), ...)`，并在调用前取得 ctx。
- `reconcile_interrupted_studios()` 必须在 lifespan 的 `with use_tenant` 内执行（§10.1）。
- `group_studio/worker_runtime.py:297` 的 `build_actor_memory_prompt(user_id="default", ...)`：参数保留，但 §10.5 之后租户内永远只有 `default` 用户，所以语义正确，**不需要改**。

### 10.5 记忆

租户内只有一个用户，习惯记忆一律使用 `user_id="default"`，即租户内 `memory/users/habits.md`：
- 删除 `runtime/governance.py::_hosted_memory_user`，调用处直接使用传入的 `user_id`（各调用方传的都是 `default`）。
- 删除 `server/routes/memory_api/live.py::_habit_user_id`。`GET/PUT /api/memory/habits` 的 `userId` 查询参数保留以兼容前端，但忽略其值，固定读写 `default`。
- 专业区、时间线、记忆候选、`memory_nodes` 本来就在租户的数据根里，天然隔离，不需要改动。
- `memory/professional_update.py:55 loop.create_task(...)`：同 §10.4，核对它是在租户上下文内创建的。

### 10.6 后台 Run（`server/routes/chat/background_runs.py`）

- `ChatBackgroundRun` 新增 `tenant_id: str = ""`，`start_chat_background_run` 写入 `current_tenant().tenant_id`。
- `get_chat_background_run(run_id)`：内存中找到但 `tenant_id` 不匹配，返回 `None`；内存中没有则查租户库（天然隔离）。
- `list_chat_background_runs`：内存部分按 `tenant_id` 过滤。
- `subscribe_chat_background_run`、`cancel_chat_background_run`、`steer_chat_background_run`、`attach_chat_background_run_task`：内部都通过 `_runs.get` 访问，统一走一个 `_get_owned_run(run_id)` 辅助函数。
- `server/routes/chat/handlers/runs.py::_get_project_run` 不需要再做归属判断，租户过滤已经在下层完成。

### 10.7 线程

- `tools/web_fetch.py:303 run_in_executor(getaddrinfo)`：只做 DNS 解析，不访问租户数据，保持不变。
- `mcp/registry.py:437 anyio.to_thread.run_sync(...)`：**实施前核对**回调里是否读写租户数据（注册表、文件、密钥）。如果有，在提交前用 `ctx = contextvars.copy_context()`，线程内执行 `ctx.run(fn)`。

## 11. 模型来源

### 11.1 两层结构

| 层 | ID 形式 | 来源 | 谁能写 |
|---|---|---|---|
| 平台层 | `platform:<上游模型名>` | hosted：该用户 key2api `/v1/models` 并合并 `shared/platform-models.json` 中的能力；local：云端 `/api/platform/v1/models` | 只有管理员能改能力表 |
| 自定义层 | 现有 ID | 当前租户的 `models.json` | 租户本人（hosted）/ 本机用户（desktop） |

### 11.2 绑定解析（`config/runtime_settings.py::resolve_model_binding`）

把现有的 `platform:` 分支（70-88 行）替换为：

```python
if catalog is None and model_config_id and model_config_id.startswith("platform:"):
    from src.models.platform_layer import platform_binding
    return platform_binding(model_config_id)        # 失败时抛 CatalogError，文案见下
```

**默认模型解析顺序**（`model_config_id` 为空时）：租户 `models.json` 的 `defaultModelId` → hosted 下取平台层第一个模型 → 抛 `CatalogError("没有可用模型，请添加自定义模型")`。

新增 `backend/src/models/platform_layer.py`：

```python
PLATFORM_PREFIX = "platform:"

def list_platform_models() -> list[dict]:
    # hosted：key2api.list_platform_models(current_tenant().user_id)，与能力表合并
    # local：已登录则调用 cloud_client.list_models()（缓存 300 秒，key=local），未登录返回 []
def platform_binding(model_config_id: str) -> RuntimeModelBinding:
    # hosted：key2api.binding_for_platform_model(id, current_tenant().user_id)，并补上能力
    # local：cloud_client.binding(id)，§11.6
def platform_capabilities(model_name: str) -> dict
    # 读 shared/platform-models.json；没有记录时用默认值 {"tools": true, "images": false}
```

错误文案：
- local 未登录：`"登录引力云端后才能使用平台模型"`
- local 令牌失效（云端返回 401）：`"云端登录已失效，请重新登录"`，同时把本机账号状态标为 `expired`
- hosted 没有平台 Key：`"平台模型暂不可用，请稍后重试或添加自己的模型"`

平台模型不支持 fallback 链：`binding.chain` 只有一项。

### 11.3 密钥存储（`config/secret_store.py`）

- `SECRET_DIR` 改为 `secret_dir() -> data_root()/"secrets"`。
- `_cache` 和 `_memory_secrets` 的 key 改为 `(current_tenant().tenant_id, account)`。
- **hosted 新后端 `tenant_encrypted`**：hosted 下 `_backend_name()` 固定返回它，忽略环境变量。
  - 主密钥：环境变量 `AGENT_WORKSTATION_SECRET_KEY`（Fernet key，base64 编码的 32 字节）。hosted 启动时缺失即失败（§10.1）。
  - 文件：`secret_dir()/<sha256(account)>.enc`，内容为 Fernet 密文，权限 0600。
  - `pyproject.toml` 显式加入 `cryptography`（目前只是传递依赖）。
  - 同时影响"命名密钥"（MCP / HTTP 工具头部）和"目录文件回退"（`_catalog_file_path`）：hosted 下一律走加密文件，**不允许落明文**。
- desktop 行为不变（keychain / dpapi / memory），只是缓存 key 加上了 `tenant_id`（恒为 `local`）。
- 测试：`AGENT_WORKSTATION_SECRET_STORE=memory` 在 desktop 测试里仍然生效；hosted 测试用 `hosted_env` 设置测试主密钥。

### 11.4 自定义模型（hosted）

`server/routes/settings.py`：
- 删除 `_require_hosted_model_admin`。`GET/PUT /api/settings/models` 读写当前租户的 `models.json`，任何已登录用户都能修改自己的目录。
- `PUT` 时，hosted 下对每个 provider 的 `baseUrl` 调用 `egress.assert_public_url(...)`（§12.5），失败返回 422 `"模型地址不能指向内网或本机"`。
- 响应仍然通过 `public_catalog()` 脱敏，只返回 `apiKeyConfigured`。现有的"响应里出现 `apiKey` 就报 500"断言保留。
- 模型调用时（`create_model_chain` 之前）再校验一次（DNS 可能变化）。**实施前核对**：AgentScope 的 OpenAI / Anthropic 等模型类能否注入自定义 `httpx.AsyncClient`；能的话用 §12.5 的 `GuardedAsyncTransport`，不能的话就只在绑定时校验，并在 design 的风险一节注明剩余风险。

### 11.5 平台模型能力表

- 文件：`shared/platform-models.json`，结构：

```json
{ "version": 1,
  "models": [ { "model": "deepseek-v4-flash", "label": "DeepSeek V4 Flash",
                "capabilities": { "tools": true, "images": false, "thinking": true } } ] }
```

  `capabilities` 的字段名与 `ModelEntry.capabilities` 现有字段保持一致（实施前核对现有字段集）。
- 新增管理员接口：`GET /api/admin/platform-models`、`PUT /api/admin/platform-models`（原子写，写临时文件再 `os.replace`）。
- 部署级 `models.json` 作为"平台目录"的旧用法（`platform_models.py::list_provided_models` 和 `invoke_platform_model` 里 `key2api` 未配置时的分支）**删除**：hosted 要求必须配置 `AGENT_WORKSTATION_KEY2API_SYNC_TOKEN`，缺失即启动失败。

### 11.6 桌面云端转发

**云端（hosted 进程）新增 `server/platform_gateway.py`**，路由前缀 `/api/platform/v1`：

```
GET  /api/platform/v1/models
     -> {"object":"list","data":[{"id":"platform:<name>","object":"model","owned_by":"yinli",
                                  "capabilities":{...}}]}
POST /api/platform/v1/chat/completions
     请求体为 OpenAI Chat Completions 格式，原样转发，只改两处：
       1) model 去掉 "platform:" 前缀；必须在该用户可用模型列表中，否则返回 404
       2) stream=true 时设置 stream_options.include_usage=true
     用该用户的 k2a_ Key 调 key2api {base_url}/v1/chat/completions
     stream：httpx.AsyncClient.stream 逐块转发（media_type="text/event-stream"），不缓冲
     非 stream：原样返回 JSON
```

- 鉴权：走 `HostedSessionMiddleware`（Bearer 令牌），因此请求进入时已有租户上下文。转发本身只用到控制面，不读写租户数据。
- 错误映射：上游 401/403 → 502 `"平台 Key 不可用"`（**不能**返回 401，否则桌面会误以为登录失效）；上游 429 → 429；其他上游 4xx → 原状态码，`detail` 取上游 `error.message` 并截断到 500 字；上游 5xx 或超时 → 502。
- 超时：连接 10 秒，读取 600 秒。
- 审计：结束时写入 `platform_call_audit`（user_id、model、client_kind、prompt / completion tokens（从最后的 usage 块或 JSON 的 `usage` 字段解析）、status）。
- **不记录请求和响应正文**。
- 实施前核对：key2api 是否支持 `tools` / `tool_choice` 与 `stream_options`；如果不支持 `stream_options`，就去掉这一项，usage 记为 0。

**删除**：`server/auth/platform_chat.py`；`server/routes/chat/handlers/send.py:62-70` 中 desktop + `platform:` 的特殊分支；`server/auth/platform_models.py` 的 `POST /api/models/complete`、`invoke_platform_model`、`_response_text`；`server/auth/key2api.py::complete_with_platform_key`。

**桌面端（desktop 进程）**：

- 新增 `server/auth/cloud_client.py`：

```python
def load_account() -> CloudAccount | None
    # 读 hosted-account.json（baseUrl、email、status），token 从 secret_store 读 named secret "cloud-session-token"
def save_account(base_url, email, token) -> None
def clear_account() -> None
def mark_expired() -> None              # status="expired"，保留 email 以便界面提示重新登录
async def list_models() -> list[dict]   # GET {base}/api/platform/v1/models，缓存 300 秒；401 时 mark_expired 并返回 []
def binding(model_config_id) -> RuntimeModelBinding:
    # ProviderConfig(id="yinli-cloud", name="引力云端", adapter="openai",
    #                baseUrl=f"{base}/api/platform/v1", apiKey=token)
    # ModelEntry(id=model_config_id, providerId="yinli-cloud", model=model_config_id, capabilities=来自 list_models 缓存)
    # 用 resolve_model_binding(id, catalog=临时目录) 生成，与 key2api.binding_for_platform_model 写法一致
```

  注意：`ModelEntry.model` 传 `platform:<name>`（带前缀），由云端负责去前缀。这样云端可以统一校验模型 ID。

- **兼容旧文件**：`hosted-account.json` 里如果还有 `token` 字段，首次 `load_account()` 时把它迁入 secret_store，再把文件重写为不含 token 的版本。
- secret_store 在开发模式下可能是 `memory` 后端，重启后 token 会丢，属于预期行为（需要重新登录）。
- `server/auth/routes.py` 的 `/api/auth/desktop/session`（GET / POST / DELETE）改为使用 `cloud_client`。POST 成功后清空 `list_models` 缓存；DELETE 时尽力调用远端 `/api/auth/logout`（失败忽略），然后 `clear_account()`。
- 云端地址默认值：新增设置 `cloud_base_url`（环境变量 `AGENT_WORKSTATION_CLOUD_BASE_URL`，打包时写入）。登录表单允许在"高级"里修改。
- `server/auth/platform_models.py::_desktop_provided_models` 改为调用 `cloud_client.list_models()`。`GET /api/models/provided` 的响应结构保持不变。
- 桌面令牌有效期：**实施前核对** `auth_sessions.expires_at` 对 `client_kind=desktop` 的 TTL。要求至少 30 天，且每次通过鉴权时滑动续期；现有实现不满足就改。

## 12. Web 执行安全

### 12.1 能力表（新增 `tenancy/capabilities.py`）

```python
@dataclass(frozen=True)
class Capabilities:
    local_project_paths: bool     # 可以选择任意本地目录作为 Project 根
    stdio_mcp: bool
    local_execution: bool         # 可以使用 execution_backend=local
    sandbox_execution: bool       # 已配置可用的容器沙箱
    sandbox_images: tuple[str, ...]
    cloud_login: bool             # 桌面可登录云端
    custom_models: bool           # 恒为 True

def capabilities() -> Capabilities
    # desktop: (True, True, True, docker_available?, (), True, True)
    # hosted:  (False, False, False, True, settings.sandbox_images, False, True)
```

`GET /api/auth/config` 返回：

```json
{"serveMode": "hosted", "capabilities": {"localProjectPaths": false, "stdioMcp": false, "localExecution": false,
 "sandboxExecution": true, "sandboxImages": ["python:3.11-slim"], "cloudLogin": false, "customModels": true}}
```

**后端必须在每个入口单独校验**，不能只依赖前端隐藏。

### 12.2 后端强制点

| 入口 | hosted 下的行为 |
|---|---|
| `POST /api/workspaces/bootstrap`、`POST /api/workspaces/{id}/projects` | 忽略 `rootPath`（§9.2） |
| `PATCH /api/projects/{id}` 带 `rootPath` | 422 |
| Agent 创建 / 更新 / 导入（`server/routes/agents.py`、`catalog/profiles.py` 写入） | `guardrails.execution_backend` 强制改写为 `docker`，`persistent_sandbox=True`，`docker_image` 不在白名单中则改写为白名单第一项；`filesystem_scope` 改写为 `default_project_root()` |
| 运行时 `_build_runtime_guardrails`（`server/routes/chat/runtime.py:40`）及 `group_studio/worker_runtime.py:110`、`subagents/permission_policy.py:234` | 同上，再强制一次（防止库里有旧值） |
| `agents/factory.py` 创建 Workspace | hosted 下如果得到的不是 `HostedDockerWorkspace`，抛错拒绝运行 |
| MCP 注册 / 安装 / 启用（`server/routes/mcp.py:142,185,253` 等） | transport 为 `stdio` 时返回 403 `{"detail":"Web 端不支持本地 MCP"}`；远程 URL 走出站校验 |
| `catalog/mcp_files.py` 文件发现 | hosted 下过滤掉 stdio 条目 |
| Chat 运行时 `_build_runtime_mcp_servers` | hosted 下跳过 stdio（防御） |
| Skill 包安装（`server/routes/skill_packages.py`） | 允许安装；脚本只会在沙箱里执行（因为 Bash 只存在于沙箱） |
| `POST /api/scheduled-jobs`，`mode=script` | 允许；firing 在沙箱执行（§12.4.5） |
| HTTP 工具保存 / 调试（`server/routes/tools.py:461,474,509,552,615`） | URL 走出站校验；执行时用受保护的 transport |
| `PUT /api/settings/models` | provider `baseUrl` 走出站校验 |
| 沙盒页 `/api/sandbox/cases` | 实施前核对其用途；如果会执行代码，hosted 下同样走沙箱或者直接禁用 |

### 12.3 MCP 注册表

- `mcp/registry.py::_builtin_mcp_servers`（filesystem / git / sqlite / fetch，全部是 stdio）：只在 `local` 租户合并进注册表；hosted 下 `list` / `get` 都看不到它们。
- `scheduler/mcp_runtime.py:36` 现在已经对定时任务排除了 stdio，保持不变。

### 12.4 容器沙箱

#### 12.4.1 配置（`config/settings.py` 新增，`AGENT_WORKSTATION_` 前缀）

| 设置 | 默认 | 说明 |
|---|---|---|
| `sandbox_docker_host` | `""` | Docker daemon 地址：`unix:///var/run/docker.sock`（方案 A）或 `tcp://sandbox-host:2376`（方案 B）。hosted 下为空则启动失败 |
| `sandbox_tls_ca` / `sandbox_tls_cert` / `sandbox_tls_key` | `""` | 方案 B 的 TLS 文件路径 |
| `sandbox_host_data_root` | `""` | **daemon 看到的** `ROOT` 路径（用于 bind mount）。为空时等于 `USER_DATA_ROOT`（方案 A 推荐把宿主路径以相同路径挂进后端容器） |
| `sandbox_runtime` | `"runsc"` | 容器 runtime。值为 `runc` 时必须同时设置 `sandbox_allow_runc=true`，否则启动失败 |
| `sandbox_network` | `"yinli-sandbox"` | 沙箱接入的 Docker 网络；Agent 的 `network_enabled=false` 时使用 `none` |
| `sandbox_images` | `"python:3.11-slim"` | 逗号分隔的镜像白名单 |
| `sandbox_memory_mb` / `sandbox_cpus` / `sandbox_pids` | `1024` / `1.0` / `256` | 资源限制 |
| `sandbox_max_timeout_seconds` | `600` | 单次命令执行上限（与 Agent 的 `timeout_seconds` 取较小值） |

两种部署方案都通过这些配置切换，代码路径完全一样：
- **方案 A：同机，挂载 docker.sock。** 部署简单。代价：后端进程等同于拥有宿主 root 权限，后端本身一旦被攻破即全盘失守。必须使用 `runsc`，并按 §15 配置网络隔离。
- **方案 B：独立沙箱主机（或 rootless Docker）。** 后端通过 TCP + TLS 访问。更安全。要求沙箱主机能以 `sandbox_host_data_root` 路径看到同一份租户文件（NFS 或其他共享存储）。

启动检查（hosted）：连接 daemon，`docker info` 中必须存在配置的 runtime，必须存在 `sandbox_network`（`none` 除外），镜像白名单里的镜像如果本地没有，就记录警告（首次使用时拉取）。

#### 12.4.2 `HostedDockerWorkspace`（新增 `backend/src/sandbox/hosted_workspace.py`）

继承 `agentscope.workspace.DockerWorkspace`，覆盖 `_create_and_start_container`（AgentScope 已锁定 `==2.0.5`，覆盖私有方法可以接受；**升级 AgentScope 时必须复查**）：

```python
host_config = {
    "Binds": [f"{to_daemon_path(project_root)}:/workspace:rw"] + skill_binds,
    "Memory": mb * 1024 * 1024,
    "NanoCpus": int(cpus * 1e9),
    "PidsLimit": pids,
    "NetworkMode": network,           # 或 "none"
    "Runtime": runtime,
    "CapDrop": ["ALL"],
    "SecurityOpt": ["no-new-privileges"],
    "Privileged": False,
}
labels = {"agentscope.workspace": "true", "agentscope.workspace.id": wid,
          "yinli.tenant": tenant_id, "yinli.project": project_id}
```

- **容器命名**：`workspace_id = "y" + sha1(f"{tenant_id}:{project_id}:{agent_id}").hexdigest()[:24]`。

  **陷阱**：现有代码用 `f"agent-{agent_id}"` 作为 workspace_id，容器名为 `as_ws_agent-default`，所有租户的默认 Agent 会**共用同一个容器**。必须把租户和 Project 编进名字里。
- `host_workdir` 是 **Project 根目录**（不再是 `sandbox_root()/agent_id`），这样沙箱里产出的文件会直接出现在 Project 中，Read / Write 等宿主侧工具也能看到。
- `to_daemon_path(p)`：断言 `p.resolve().is_relative_to(tenant_root().resolve())`，然后把 `USER_DATA_ROOT` 前缀替换为 `sandbox_host_data_root`。
- Skill 挂载：AgentScope 对 `skill_paths` 的处理方式**实施前核对**。如果它通过 bind mount 挂载宿主路径，也需要用 `to_daemon_path` 转换，并加 `:ro`。
- Docker 客户端：**实施前核对** AgentScope 使用的 Docker 客户端（aiodocker 或其他）是否读取 `DOCKER_HOST` 和 TLS 环境变量。读取的话，进程启动时把 `sandbox_*` 写入这些环境变量；不读取的话，在子类里按配置显式构造客户端。
- 镜像构建：`DockerWorkspace` 会在 daemon 上基于 `base_image` 构建带 gateway 的镜像，`base_image` 必须在白名单内。

#### 12.4.3 工厂接入（`agents/factory.py:519-541`）

```python
if current_tenant().is_hosted:
    workspace = HostedDockerWorkspace(tenant_id=..., project_id=..., agent_id=...,
                                      project_root=workspace_dir, base_image=..., skill_paths=skill_paths,
                                      network_enabled=guardrails.network_enabled)
elif guardrails and guardrails.execution_backend == "docker":
    ...现有逻辑，sandbox_root() 替换 SANDBOX_ROOT...
else:
    workspace = LocalWorkspace(...)
```

`project_id` 从 `RuntimeContext` 获取。如果调用链里拿不到，在 `create_agent` 增加 `project_id` 参数，从 `run_context` / `team_runtime` / `group_studio` 依次传入（**实施前核对**所有 `create_agent` 调用点）。

#### 12.4.4 Bash 与文件工具

- `GuardedBash` 在 DockerWorkspace 下通过 workspace backend 执行，不会落到宿主。hosted 下额外断言：`workspace` 不是 `HostedDockerWorkspace` 时拒绝执行（返回错误块，不抛到外层）。
- `ScopedRead/Grep/Glob/Write/Edit` 在宿主侧操作文件，`scope_dir` 等于 Project 根目录（hosted 下 `filesystem_scope` 已被强制为 Project 根），可以接受。
- **已知体验问题**（不在本 change 解决）：Bash 里看到的路径是 `/workspace/...`，文件工具里看到的是宿主绝对路径。在 hosted 的系统提示词中加一句说明："Bash 中的 /workspace 就是项目根目录"。

#### 12.4.5 Script 定时任务（`scheduler/script_job.py::execute_script_in_dir`）

hosted 下改为调用 `sandbox.run_script(cwd, filename, interpreter, args, timeout)`：
- 优先复用 `HostedDockerWorkspace`（`host_workdir` = firing 目录，`workspace_id` 在 §12.4.2 的基础上加 firing id 哈希，用完即删）执行 `python3 <file>` 或 `bash <file>`。
- 如果 workspace backend 不提供"执行一次命令并取回输出"的接口（**实施前核对**），就直接用 Docker API 创建一次性容器（`AutoRemove`，`HostConfig` 与 §12.4.2 相同，镜像取白名单第一项），等待结束后取日志。
- 返回结构与现有 `ScriptRunResult` 保持一致（stdout / stderr 截断规则不变）。
- desktop 行为不变。

#### 12.4.6 删除的旧逻辑

`server/routes/chat/runtime.py::_hosted_personal_project` 及其在 `scope_guardrails_to_project` 中的分支删除。hosted 下 `filesystem_scope` 已经被强制为 Project 根，交集计算自然成立。

#### 12.4.7 宿主 venv

`runtime/agent_python.py::ensure_agent_venv` 只在 desktop 下调用。hosted 下 Bash 只在沙箱里运行，不需要宿主 venv。

#### 12.4.8 测试替身

`sandbox/fake.py::FakeSandbox`：记录创建请求（名称、Binds、labels、HostConfig），`exec` 返回预设输出。`hosted_env` fixture 通过依赖注入替换（`sandbox/provider.py` 提供 `get_sandbox_provider()`，测试里 monkeypatch 它）。真实 Docker 的测试标记为 `@pytest.mark.docker`，默认跳过。

### 12.5 出站校验（新增 `backend/src/security/egress.py`）

把 `tools/web_fetch.py:288-372` 中的 `_validate_fetch_host`、`_normalized_ip`、`_is_lan_address`、`_is_cgnat_address`、`_require_allowed_ip`、`_validate_url_shape` 迁入本模块，`web_fetch` 改为从这里导入（web_fetch 现有的桌面行为保持不变）。

```python
class EgressBlocked(ValueError): ...

async def assert_public_url(url: str, *, purpose: str) -> None
def assert_public_url_sync(url: str, *, purpose: str) -> None
def egress_enforced() -> bool            # == current_tenant().is_hosted

class GuardedAsyncTransport(httpx.AsyncHTTPTransport):
    """每次请求（包括手动跟随重定向后的每一跳）都解析并校验目标 IP。"""

def guarded_async_client(**kwargs) -> httpx.AsyncClient
    # hosted：transport=GuardedAsyncTransport()，follow_redirects=False，由调用方逐跳跟随并校验（最多 5 跳）
    # desktop：普通 httpx.AsyncClient(**kwargs)
```

拒绝的目标：回环、RFC1918 私网、链路本地（`169.254.0.0/16`、`fe80::/10`）、CGNAT（`100.64.0.0/10`）、组播、保留地址、未指定地址（`0.0.0.0`、`::`）、IPv6 ULA（`fc00::/7`）、IPv4 映射 IPv6（先解映射再判断）、云元数据（`169.254.169.254`、`fd00:ec2::254`）。只允许 `http` / `https`。

例外白名单：`AGENT_WORKSTATION_EGRESS_ALLOW_HOSTS`（逗号分隔，主机名或 IP），用于例如内网部署的 key2api。平台网关和 key2api 的调用由服务端发起、URL 不受用户控制，**不经过**出站校验。

接入点：
- `tools/configured_http_tool.py`、`tools/http_request.py`、`tools/knowledge_base_search.py`：创建客户端时改用 `guarded_async_client()`。
- `tools/web_fetch.py`：hosted 下同样走本模块（它已有类似逻辑，合并即可）。
- 远程 MCP：注册 / 安装 / 启用时调用 `assert_public_url`；每次构建运行时 server spec 前（`_build_runtime_mcp_servers`）再调用一次。**剩余风险**：AgentScope 的 MCP 客户端建立连接时不经过本模块，存在 DNS rebinding 的窗口；靠 §15 的网络层出站规则兜底。
- 自定义模型 `baseUrl`：§11.4。

### 12.6 配额

`TenantQuota`（存在控制面 `tenants.quota_json`；默认值来自配置）：

| 字段 | 配置项 | 默认 | 检查位置 | 超限响应 |
|---|---|---|---|---|
| `disk_mb` | `tenant_disk_mb` | 2048 | 上传前、Skill 包安装前 | 413 |
| `upload_mb` | `tenant_upload_mb` | 50 | `upload_project_file`（边读边计数，超限立即中止） | 413 |
| `max_active_runs` | `tenant_max_active_runs` | 3 | `start_chat_background_run`（统计 `_runs` 中同租户、非终态的 Run） | 429 |
| `max_jobs` | `tenant_max_jobs` | 20 | `create_job` | 429 |
| `max_firings_per_day` | `tenant_max_firings_per_day` | 50 | ticker 派发前（统计租户库中当天 `scheduled_job_runs` 数量） | 本次 firing 标记为 `skipped_quota`（实施前核对现有 firing 状态枚举，必要时新增） |

错误体统一为 `{"detail": {"code": "quota_exceeded", "quota": "<字段>", "limit": <n>}}`。

磁盘用量：`os.scandir` 递归统计租户目录，按租户缓存 60 秒。只在 hosted 下启用；desktop 不检查配额。

### 12.7 禁止发现服务器 HOME

`user_home_root()` 在 hosted 下返回 `None`。以下位置在该值为 `None` 时跳过 user scope：
- `catalog/skills.py:118-127`（`(user_home_root, "user", False, "external_agents")`）
- `skills/registry.py:536-546, 699-709`
- `subagents/registry.py:26, 45`
- `catalog/index.py:29-38`

**陷阱**：如果漏掉这一步，服务器运维账号 `~/.claude/skills`、`~/.agents/...` 下的内容会出现在所有租户的 Skill / Subagent 列表里，甚至被注入提示词。

## 13. API 变更汇总

| 接口 | 变更 |
|---|---|
| `GET /api/auth/config` | 增加 `capabilities` |
| `/api/auth/*`（除 `config`、`desktop/session*`）、`/api/admin/*` | desktop 下返回 404 |
| `POST /api/admin/ownership/assign` | 删除 |
| `GET/PUT /api/admin/platform-models` | 新增（仅管理员） |
| `POST /api/models/complete` | 删除 |
| `GET /api/models/provided` | 结构不变；hosted 来源为 key2api + 能力表，desktop 来源为云端网关 |
| `GET /api/platform/v1/models`、`POST /api/platform/v1/chat/completions` | 新增（只在 hosted 进程提供） |
| `PUT /api/settings/models` | hosted 下普通用户可写自己的目录；`baseUrl` 走出站校验 |
| `PATCH /api/projects/{id}` | hosted 下拒绝修改 `rootPath` |
| `GET/PUT /api/memory/habits` | 忽略 `userId` 参数 |
| MCP 写接口 | hosted 下拒绝 stdio |
| 各类创建接口 | 超出配额时返回 413 / 429 |
| 所有按 ID 访问的接口 | 跨租户访问返回 404（由物理隔离保证） |

## 14. 前端

| 文件 | 改动 |
|---|---|
| `features/auth/authStore.ts` | 增加 `capabilities`、`cloudAccount: {connected, email, status}`；`setSession` 时一并写入 |
| `features/auth/authClient.ts` / `HostedSessionGate.tsx` | 读取 `/api/auth/config` 的 `capabilities`；桌面读 `/api/auth/desktop/session` 的 `status` |
| 新增 `stores/resetTenantState.ts` | `resetTenantState()`：重置 `appStore`（当前 workspace / project）、`agentsStore`、`notificationStore` 以及所有保存租户数据的 zustand store；关闭通知 SSE；清空模型列表缓存。**`settingsStore`（主题）和 `productFeaturesStore` 不重置** |
| `features/auth/UserMenu.tsx::signOut`、登录成功处 | 用户 ID 变化（含变为 `null`）时调用 `resetTenantState()` |
| 依赖用户的 `useEffect` 数据加载 | 依赖数组加入 `user?.id`，确保切换账号后重新拉取 |
| `features/settings/ApiKeyOnboardingModal.tsx:18,26` | localStorage key 加后缀：hosted 为 `:${user.id}`，desktop 为 `:local`；hosted 下永远不弹出 |
| `features/auth/authRules.ts::shouldPromptLocalApiKey` | hosted 返回 `false`；desktop 维持"未连接云端且本机默认模型没有 Key 时弹出" |
| `features/workspaces/ContextCreateModal.tsx` | `!capabilities.localProjectPaths` 时隐藏 rootPath 输入框和目录选择按钮，请求里不发送 `rootPath` |
| Project 设置里修改根目录的入口（实施前 grep `rootPath` 定位） | 同上 |
| `features/agents/sections/AgentGuardrailsSection.tsx` | `!capabilities.localExecution` 时隐藏执行后端选择，显示"Web 端命令在隔离沙箱中执行"；镜像下拉框只列出 `sandboxImages` |
| `pages/MCPPage.tsx` / `types/mcp.ts` | `!capabilities.stdioMcp` 时隐藏 stdio 传输选项 |
| `pages/ScheduledJobsPage.tsx` / `ScheduledJobScheduleForm.tsx` | script 模式在 hosted 下显示"在隔离沙箱中运行" |
| `pages/ModelsPage.tsx` + `features/models/*` | 分成两块："平台模型"（只读列表，来源 `/api/models/provided`）和"我的模型"（自定义层，所有人可编辑）。hosted 下移除管理员限制 |
| `features/auth/AdminUsersPanel.tsx` | 增加"平台模型"页签，编辑 `/api/admin/platform-models` |
| 桌面登录入口（`UserMenu` 或标题栏，desktop 时显示） | 未登录显示"登录引力云端（可选）"，表单包含邮箱、密码，"高级"里可改服务地址；说明文案："登录只用于使用云端模型，本地数据不会上传"。已登录显示邮箱和"退出"；`status=expired` 时显示"云端登录已失效，请重新登录" |
| `features/models/useProvidedModels.ts` | desktop 用 `cloudAccount.connected` 判断是否显示平台模型 |
| `features/auth/authRules.test.ts` 等 | 同步更新；新增 `resetTenantState` 测试 |

## 15. 部署（`deploy/`）

- `docker-compose.web.yml`：
  - 数据从命名卷改为宿主路径，并以**相同路径**挂入容器：`- /srv/yinli/data:/srv/yinli/data`；`AGENT_WORKSTATION_DATA_DIR=/srv/yinli/data`。（方案 A 下 bind mount 使用的是宿主路径，路径相同才不需要做转换。）
  - 新增环境变量：`AGENT_WORKSTATION_SECRET_KEY`、`AGENT_WORKSTATION_SANDBOX_*`、`AGENT_WORKSTATION_TENANT_*`、`AGENT_WORKSTATION_EGRESS_ALLOW_HOSTS`、`AGENT_WORKSTATION_KEY2API_SYNC_TOKEN`（hosted 必填）。
  - 方案 A：增加 `- /var/run/docker.sock:/var/run/docker.sock`。
- `Dockerfile`：uvicorn 显式 `--workers 1`。
- `backend.env.example`：补齐上面所有变量，并加注释说明方案 A / B。
- 新增 `deploy/SANDBOX.md`：
  - 安装 gVisor 并在 `/etc/docker/daemon.json` 注册 `runsc` runtime。
  - 创建网络：`docker network create --driver bridge -o com.docker.network.bridge.enable_icc=false yinli-sandbox`。
  - 在宿主 `DOCKER-USER` 链中拒绝该网段访问 `10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`、`169.254.0.0/16`、`100.64.0.0/10` 以及宿主自身地址（给出 iptables 示例）。
  - 方案 B 的 TLS 证书生成和共享存储挂载说明。
- 新增 `deploy/BACKUP.md`：控制面用 `sqlite3 control.db ".backup ..."`；租户目录按目录快照。
- 清空旧数据（任务 0.1）：`docker compose -f deploy/docker-compose.web.yml down`，`docker volume rm yinli_yinli-data`。

## 16. 删除与替换总清单

完成后 `grep -rn "ownership\|owner_user_id\|ownerUserId\|current_owner_id\|ensure_hosted_home\|path_is_inside_user\|assign_unowned\|platform_chat\|complete_with_platform_key\|invoke_platform_model\|_hosted_memory_user\|_habit_user_id\|_hosted_personal_project" backend/src frontend/src`，结果必须为空（注释除外）。

已知需要改动的引用（来自本次代码检查）：
`runtime_context.py:58-62`、`config/runtime_settings.py:79-82`、`runtime/governance.py:109-111`、`server/auth/platform_models.py:96-101`、`server/auth/service.py:75-77,97-99,188-190,247`、`server/auth/assign_owners.py`、`server/routes/group_studio.py:97-100,125-127`、`server/auth/routes.py:273`、`server/auth/middleware.py:51-65`、`server/routes/scheduled_jobs.py:253-257`、`server/routes/workspaces.py:123-180`、`server/routes/chat/store.py:126-140`、`server/routes/chat/models.py:189-197`、`server/routes/chat/runtime.py:117-126`、`server/routes/chat/handlers/sessions.py:47-67,94,111,128,144`、`server/routes/memory_api/live.py:36-44`、`storage/chat_session_records.py:89-125`、`storage/database.py:377-385`、`storage/workspace_project_records.py:12-28,95,102,126,149,156,197`、`storage/scheduled_job_records.py:100-112`、`storage/group_studio_records.py:315-327`。

## 17. 测试策略

### 17.1 新增测试文件

| 文件 | 覆盖 |
|---|---|
| `test/test_tenant_context.py` | desktop 默认返回 local；hosted 未设置时抛 `TenantMissing`；`use_tenant` 嵌套与恢复；`spawn_in_tenant` 任务内读到正确租户；租户 ID 正则拒绝 `../x` |
| `test/test_tenant_paths.py` | 两个租户解析出的每个路径函数结果不同且都在各自根目录下；`grep` 检查脚本（§4.2）以测试形式运行，断言源码里不再出现旧常量 |
| `test/test_chat_store_incremental.py` | 并发追加（两个线程各 50 条）总数 100 且 `sort_order` 唯一；坏 JSON 行读取时不丢、后续写入后仍存在；`files` / 澄清字段在重新读取后保留；删除会话级联删除消息；开启外键后删除会话会清理 `run_events` |
| `test/test_tenant_isolation.py` | 两个 hosted 用户 A、B。A 创建以下资源：会话、Run、定时任务、通知、时间线节点、记忆候选、Agent、Skill、远程 MCP、HTTP 工具、自定义模型（含 Key）、上传文件、群聊、Workflow。B 对每类资源执行列表、按 ID 读、改、删，结果全部为"看不到"或 404。另外：B 的提示词里不包含 A 的专业区记忆；B 读不到 A 的模型 Key；两个租户的 `default` Agent 互不影响 |
| `test/test_tenant_lifecycle.py` | 验证后租户目录存在，默认 Project 可用且资源白名单非空（含 agent 和 skill）；停用后返回 403 且 ticker 跳过；删除后目录不存在、在途 Run 被取消、`FakeSandbox` 收到按 label 删除的请求、再次登录失败 |
| `test/test_tenant_background.py` | 两个租户各有一个到期 Job，tick 后各自的会话写入各自的库；hosted 下使用 `platform:` 模型的 Job 能解析出绑定（用 mock 替换 key2api） |
| `test/test_control_db.py` | 账号表只在控制面；租户库中不存在 `auth_*` 表；desktop 下控制面不创建 |
| `test/test_platform_gateway.py` | 无令牌 401；停用账号 403；模型不在列表中 404；stream 逐块透传；上游 401 映射为 502；审计写入 usage |
| `test/test_cloud_client.py` | 桌面未登录时平台模型不出现；登录后 `binding()` 指向云端 `/api/platform/v1`；云端返回 401 时标记 expired 并返回明确错误；旧 `hosted-account.json` 里的 token 被迁入 secret_store |
| `test/test_hosted_execution.py` | stdio MCP 注册被拒；Agent 保存 `execution_backend=local` 后被改写为 docker；`FakeSandbox` 记录的容器名包含租户哈希、Binds 指向该租户 Project、`CapDrop=ALL`；两个租户默认 Agent 的容器名不同；script Job 走沙箱 |
| `test/test_egress.py` | 回环、私网、元数据、IPv4 映射 IPv6、重定向到私网均被拒绝；白名单生效；desktop 下不拦截 |
| `test/test_tenant_quota.py` | 并发 Run 达到上限返回 429；超大上传返回 413，且已写入的部分被清理 |
| `test/test_secret_store_tenant.py` | hosted 加密文件不含明文；两个租户同名 `provider_id` 读到各自的 Key；缺少主密钥时 hosted 启动失败 |

### 17.2 需要修改的现有测试

- 44 个 patch `DB_PATH` / `_initialized` 的文件、25 个 patch 路径常量的文件（§5.3）。
- `test_hosted_ownership.py`：删除。
- `test_account_access.py`、`test_platform_models.py`、`test_key2api_sync.py`、`test_local_access.py`：改为使用控制面和 `hosted_env`；删除涉及 `/api/models/complete` 和 ownership 的用例。
- `test_workspace_project.py`、`test_scheduled_jobs.py`、`test_runtime_governance.py`、`test_memory_markdown_candidates.py` 中与 owner 相关的断言：删除或改写。

### 17.3 阶段门槛

| 门槛 | 时机 | 条件 |
|---|---|---|
| A | 任务组 1、2 完成后 | `uv run pytest` 全绿；桌面手动验证：用改造前的旧 userData 启动，历史会话、Project、Agent、Skill、模型都能正常读取，能完成一轮对话 |
| B | 任务组 3、4 完成后 | `test_tenant_isolation.py`、`test_tenant_lifecycle.py`、`test_tenant_background.py` 全绿；hosted 手动验证：两个账号在两个浏览器中各自完成对话、定时任务、群聊，互相看不到 |
| C | 任务组 5 完成后 | 桌面登录云端（使用预发布环境），用平台模型完成一轮带工具调用的对话；退出后本地数据完整 |
| D | 任务组 6 完成后 | 安全相关测试全绿；按 §12 逐条做手动检查（stdio MCP、本地执行、出站请求、沙箱内能否看到其他租户的目录） |
| E | 任务组 7、8 完成后 | 前端测试全绿；按 §15 在全新机器上部署成功；文档更新完毕 |

## 18. 风险与取舍

| 风险 | 缓解措施 |
|---|---|
| 路径常量改函数影响面很大 | 删除常量让 import 报错，一次性找全；门槛 A 要求桌面完全回归 |
| 漏改缓存造成串数据 | §6 清单；hosted fail closed；`test_tenant_isolation.py` 对每类资源做交叉测试 |
| 覆盖 AgentScope 私有方法 | 版本已锁定 2.0.5；升级时必须复查 `HostedDockerWorkspace` |
| 方案 A 下 docker.sock 等同宿主 root | 使用 runsc、网络隔离、后端不对公网暴露（Caddy 反代）；条件允许时迁到方案 B |
| 远程 MCP / 模型 SDK 的 DNS rebinding | 保存时和运行前各校验一次；网络层出站规则兜底 |
| 每个租户一个 SQLite 文件 | 单机数千租户没有问题；ticker 遍历的开销后续可以用控制面索引优化 |
| 单进程限制 | 后台 Run 在内存中；Web 固定 workers=1，水平扩展另立 change |
| 租户内设置变为按租户（如定时任务开关、群聊设置、记忆策略） | 这是预期行为；创建租户时用部署默认值播种 |
