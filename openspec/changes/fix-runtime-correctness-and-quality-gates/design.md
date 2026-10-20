# Design

## Context

本设计基于 2026-10-20 09:03 的工作区快照（分支 `codex/simplify-global-session-context`，HEAD `c534c3f` 加未提交改动）。文中行号是该快照里的位置，实施时以符号名为准；所有"当前 N 处"的数字只是参考，实施时以本文给出的脚本重新计算的结果为准。

现状：

- 运行时：anyio 4.13、starlette 1.2.1、FastAPI。租户上下文是 `ContextVar`（`src/tenancy/context.py`），已实测可以传到 `run_in_threadpool` 和 `asyncio.to_thread`。同步路由由 starlette 放入 anyio 线程池（默认 40 个线程）。
- 按本变更 `tools/loop_affinity_audit.py` 的口径，196 个 `async` 路由处理函数中有 170 个函数体里没有 `await`（另有 8 个已是 `def`）。其中 151 个可以直接改成 `def`；19 个必须留在事件循环线程：16 个在调用链上会碰到事件循环 API（`create_task`、`get_running_loop`、`Task.cancel`、`Future.set_result` 等），3 个是 SSE 流式路由。另有 5 个含 `await` 的路由直接调用同步存储或同步网络。
- `src/server/auth/middleware.py` 的 `HostedSessionMiddleware.__call__` 在事件循环里同步执行 `current_user`、`get_tenant`、`ensure_tenant`。`ensure_tenant` 会建目录、初始化库、写入系统技能、同步平台 Key，首次登录时耗时明显。
- `src/agents/guarded_bash.py` 的 `_exec_windows_shell` 先带 `env=` 调用 `self._backend.exec_shell`。agentscope 2.0.5 的 `BackendBase.exec_shell(command, *, cwd, timeout)` 没有 `env` 参数，于是抛出 `TypeError`，再不带 env 重调。结果是智能体环境丢失；如果 `TypeError` 发生在子进程启动之后，同一命令还会执行两次。另外 `call()` 在 Windows 上对任何后端都走 `cmd.exe` 包装。
- `backend/pyproject.toml` 没有任何 `[tool.*]` 配置，`pytest` 写在运行时依赖里。仓库只有 `.github/workflows/model-config.yml`，它只跑 3 个后端测试文件，不跑 vitest。远端 `origin` 是 Gitee，默认分支 `master`，GitHub Actions 是否真的会执行尚未确认。
- Ruff 0.17.1 实测（`src` 目录）：`F401` 278、`F405` 21、`B023` 14、`F403` 5、`B008` 4、`F811` 4、`B010` 4、`F841` 4、`F821` 3、`B009` 2、`RUF006` 1、`F541` 1、`B904` 1、`B007` 1、`B905` 1；`BLE001`+`S110`+`S112` 共 150；`C901`（上限 15）超限 29。`test` 目录：`F841` 12、`B023` 6、`B009` 1、`B905` 1。
- basedpyright 1.40.2（basic，`include = ["src"]`）实测约 561 个错误，生成基线后再次运行为 0。
- 前端 `tsconfig.app.json` 未开 `strict`，开启后实测 0 错误；`eslint .` 有 2 条告警，都在 `src/features/group-studio/useGroupStudioRoom.ts`（170 行、755 行）。
- 3 个已知失败测试属于 `simplify-global-session-context` 13.1：`test/test_chat_runtime_regressions.py::test_chat_run_context_includes_memory_prompt`、`test/test_chat_runtime_regressions.py::test_chat_run_context_keeps_history_out_of_system_prompt`、`test/test_global_session_foundation.py::test_historical_reference_allowlist_matches_repository`。

## Goals / Non-Goals

**Goals:**

- 慢查询和同步网络调用不再卡住事件循环；一旦卡住，日志里能看到。
- Windows 命令执行与 macOS 使用同样的智能体环境，并且只执行一次。
- 建立可以本地运行、也可以在任意 CI 上运行的统一门禁，只拦新增问题，并把存量问题分批清零。
- 异常和日志可以被看见。

**Non-Goals:**

- 不改模块分层、不搬文件、不引入 import-linter（属于 `restructure-code-layering`）。
- 不删除未引用的函数，不拆分高复杂度函数（属于 `restructure-code-layering`）。
- 不重写群聊工作室的事件循环模型，那 19 个保留路由继续在事件循环里执行。
- 不引入 `ruff format`，不做大规模格式化。
- 不提高 basedpyright 的严格级别，不要求清零基线。
- 不处理 bandit 的 SQL 拼接误报（已人工确认都是参数化查询）。

## Decisions

### 1. 分两个阶段，阶段 B 等待 `simplify-global-session-context` 归档

阶段 A 只改动少量文件且改动局部，可以立即在当前分支上实施。阶段 B 会触及该变更正在重写或删除的大量文件（`routes/workspaces.py` 等会被删，`routes/sessions.py` 等是新文件），必须等它归档后开始。开始方式是先执行本文给出的审计脚本重新生成清单。判断条件：`openspec list` 中不再出现 `simplify-global-session-context`，且 `openspec/changes/archive/` 下有它的目录。

### 2. Windows 本地 shell：自己创建子进程

`src/agents/guarded_bash.py`：

1. 顺序修正。`call()` 中第二个分支 `if os.name != "nt":` 改为 `if os.name != "nt" or self._backend.__class__.__name__ != "LocalBackend":`，让 Windows 上的非本地后端也走 `super().call(...)`。
2. 顶部导入增加 `import contextlib`、`import subprocess`，`from agentscope.tool import Bash, ToolChunk` 改为 `from agentscope.tool import Bash, ExecResult, ToolChunk`（`ExecResult` 是 agentscope 2.0.5 公开导出的类型）。
3. `_exec_windows_shell` 整体替换为：

```python
    async def _exec_windows_shell(self, shell_path: str, command: str, *, timeout_ms: int) -> ExecResult:
        """Windows 本机：只启动一次 cmd.exe，带智能体环境；超时时结束整个进程树。"""

        payload = [shell_path, "/d", "/c", _windows_utf8_command(command)]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            process = await asyncio.create_subprocess_exec(
                *payload,
                cwd=self._cwd or None,
                env=agent_shell_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=creationflags,
            )
        except OSError as exc:
            return ExecResult(exit_code=127, stdout=b"", stderr=str(exc).encode("utf-8"))
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_ms / 1000.0)
        except asyncio.TimeoutError:
            await _kill_windows_process_tree(process.pid)
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.communicate()
            return ExecResult(exit_code=-1, stdout=b"", stderr=b"timed out")
        return ExecResult(exit_code=process.returncode if process.returncode is not None else -1, stdout=stdout, stderr=stderr)
```

4. 模块级新增：

```python
async def _kill_windows_process_tree(pid: int) -> None:
    """taskkill /T 结束 cmd.exe 及其子进程；失败只记日志，调用方随后仍会 kill 主进程。"""

    try:
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(pid), "/T", "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        await asyncio.wait_for(killer.wait(), timeout=5)
    except (OSError, asyncio.TimeoutError):
        logger.warning("taskkill 结束进程树失败 pid=%s", pid, exc_info=True)
```

调用方 `call()` 里的超时判断 `result.exit_code == -1 and result.stderr == b"timed out"` 与文案保持不变。文件若无 `logger`，在导入后增加 `logger = logging.getLogger(__name__)`。

选择理由：不再依赖 agentscope 私有签名；与 POSIX 分支 `_call_local_streaming` 对称（那里已经自己创建子进程并传 env）。备选方案是给 agentscope 提 PR 加 `env` 参数，周期不可控，故不采用。

### 3. 托管会话中间件：线程池解析身份，按用户串行初始化租户

`src/server/auth/middleware.py` 新增模块级类型与函数，`__call__` 只保留异步部分：

```python
import threading
from dataclasses import dataclass
from typing import Any

from starlette.concurrency import run_in_threadpool

_TENANT_INIT_GUARD = threading.Lock()
_TENANT_INIT_LOCKS: dict[str, threading.Lock] = {}


def _tenant_init_lock(user_id: str) -> threading.Lock:
    with _TENANT_INIT_GUARD:
        return _TENANT_INIT_LOCKS.setdefault(user_id, threading.Lock())


@dataclass(frozen=True)
class _HostedIdentity:
    ctx: Any = None
    user: dict[str, Any] | None = None
    session: dict[str, Any] | None = None
    reject_status: int = 0
    reject_detail: str = ""


def _resolve_hosted_identity(scope: Scope) -> _HostedIdentity:
    """在线程池中执行：查登录会话与租户状态，首次登录时初始化租户。"""

    from src.storage.control_records import get_tenant
    from src.tenancy.context import TenantContext
    from src.tenancy.lifecycle import ensure_tenant

    found = current_user(token_from_request(_RequestView(scope)))
    if found is None:
        return _HostedIdentity(reject_status=401, reject_detail="需要登录")
    user = found["user"]
    user_id = str(user["id"])
    tenant = get_tenant(user_id)
    if tenant is None:
        # 同一新用户的并发首请求必须串行，否则会重复建库、重复写入系统技能。
        with _tenant_init_lock(user_id):
            tenant = get_tenant(user_id)
            ctx = ensure_tenant(user) if tenant is None else None
    else:
        ctx = None
    if ctx is None:
        if tenant is not None and tenant["status"] != "active":
            return _HostedIdentity(reject_status=403, reject_detail="账号已停用")
        ctx = TenantContext.hosted(user_id, role=str(user.get("role") or "user"))
    return _HostedIdentity(ctx=ctx, user=user, session=found["session"])
```

`__call__` 中从 `found = current_user(...)` 到 `ctx = TenantContext.hosted(...)` 的整段替换为：

```python
        identity = await run_in_threadpool(_resolve_hosted_identity, scope)
        if identity.reject_status:
            response = JSONResponse(status_code=identity.reject_status, content={"detail": identity.reject_detail})
            await response(scope, receive, send)
            return
        from src.tenancy.context import reset_tenant, set_tenant

        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["auth_user"] = identity.user
            state["auth_session"] = identity.session
        token = set_tenant(identity.ctx)
```

其余（`try/finally reset_tenant`）不变。租户上下文在异步上下文中设置，之后的同步路由会继承它。变更前后的响应码与文案完全一致。加锁之前事件循环天然把这段代码串行化，改到线程池后必须显式加锁，这是本决策里唯一新增的并发风险点。

### 4. 事件循环卡顿监测

新文件 `src/server/loop_monitor.py`：

```python
"""事件循环卡顿监测：周期性 sleep，实际耗时减去期望耗时即为卡顿。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)


async def watch_event_loop_lag(
    *,
    threshold_ms: int,
    interval: float = 0.5,
    cooldown: float = 10.0,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    threshold = threshold_ms / 1000.0
    last_warning = float("-inf")
    while True:
        started = clock()
        await asyncio.sleep(interval)
        now = clock()
        lag = now - started - interval
        if lag >= threshold and now - last_warning >= cooldown:
            last_warning = now
            logger.warning("事件循环卡顿 %d ms（阈值 %d ms）", int(lag * 1000), threshold_ms)


def start_loop_monitor(threshold_ms: int) -> asyncio.Task[None] | None:
    """threshold_ms <= 0 时不启动。必须在事件循环内调用。"""

    if threshold_ms <= 0:
        return None
    return asyncio.get_running_loop().create_task(
        watch_event_loop_lag(threshold_ms=threshold_ms),
        name="event-loop-lag-monitor",
    )


async def stop_loop_monitor(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
```

`src/config/settings.py` 的 `Settings` 新增 `loop_lag_warn_ms: int = 250`。`src/server/main.py` 的 `lifespan` 在 `yield` 前一行执行 `lag_monitor = start_loop_monitor(settings.loop_lag_warn_ms)`，`yield` 后第一行执行 `await stop_loop_monitor(lag_monitor)`。`stop_loop_monitor` 里的 `except asyncio.CancelledError: pass` 是取消自己创建的任务后的标准写法，在该行加 `# noqa: S110` 注释并写明理由（阶段 B 开启 S110 后生效）。

### 5. 后台任务保留引用

`src/server/routes/chat/completion.py` 模块顶层新增 `_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()`。`_schedule_skill_evolution` 改为：

```python
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(
        _run_skill_evolution_background(item, model_name=model_name, persist=persist),
        name=f"skill-evolution-{item.session_id}-{item.agent_id}",
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_finish_background_task)
```

并新增：

```python
def _finish_background_task(task: asyncio.Task[None]) -> None:
    _BACKGROUND_TASKS.discard(task)
    if not task.cancelled() and task.exception() is not None:
        logger.error("技能自进化后台任务失败 %s", task.get_name(), exc_info=task.exception())
```

原来的 `try ... except RuntimeError: pass` 只保护"没有运行中的事件循环"这一种情况，改写后语义不变。

### 6. Ruff：阶段 A 的配置与修复清单

`backend/pyproject.toml` 追加：

```toml
[tool.ruff]
target-version = "py311"
src = ["src", "test"]
extend-exclude = [".venv", "dist", "build"]

[tool.ruff.lint]
select = ["E9", "F", "B", "RUF006"]
ignore = [
    "F401",  # 阶段 B 开启：先清理未使用导入，并把隐式再导出改为显式
    "B009",  # getattr 常量属性：纯风格，改写会引入新的类型检查错误
    "B010",  # setattr 常量属性：同上，现有用法是有意绕开第三方对象的类型声明
]

[tool.ruff.lint.flake8-bugbear]
extend-immutable-calls = [
    "fastapi.Body", "fastapi.Depends", "fastapi.File", "fastapi.Form",
    "fastapi.Header", "fastapi.Path", "fastapi.Query",
]

[tool.ruff.lint.per-file-ignores]
# 待 simplify-global-session-context 20.2 删除 scope_guardrails_to_project 后移除本条。
"src/server/routes/chat/runtime.py" = ["F821"]
```

逐处修复（都不改变行为）：

| 规则 | 位置 | 修改 |
|---|---|---|
| F811 | `src/scheduler/service.py` 导入块第二次出现的 `insert_scheduled_job`、`insert_scheduled_job_run`（约 59–60 行） | 删除第二次出现的两行，两次导入来自同一模块 |
| F811 | `src/server/routes/mcp.py` 从 `src.mcp.registry` 导入的 `Optional`、`HTTPException` | 从该导入列表中删除这两个名字，保留文件顶部从 `typing`/`fastapi` 的导入 |
| F821 | `src/security/authorization/service.py` 的 `reset_denials(self, owner: RunAuthorization)` | 第 12 行改为 `from .models import ActionSnapshot, AuthorizationDecision, ExecutionRecord, RunAuthorization` |
| F841 | `src/server/routes/agents.py` `update_agent` 中的 `removed_skills`（约 551–553 行） | 删除这 3 行，该变量从未被使用 |
| F841 | `src/server/routes/tools.py` `delete_http_tool_config` 的 `existing = next(...)` | 删除该行，生成器表达式没有副作用 |
| F841 | `src/skills/catalog.py` 两处 `except ... as exc`（约 156、210 行） | 去掉 ` as exc` |
| F403/F405 | `src/server/routes/memory_api/{live,markdown,nodes,settings,skills}.py` 的 `from src.server.routes.memory_api.models import *` | 改为显式导入。live：`MarkdownMemoryUpdate`；markdown：`MarkdownCandidateAction, MarkdownCandidateListResponse, MarkdownMemoryUpdate`；nodes：`MemoryNodeCreate, MemoryNodeStatusUpdate`；settings：`MemoryAdmissionPolicyUpdate, MemoryAuditEventListResponse, MemoryRetrievalFeedback, MemorySettingsResponse`；skills：`SkillEvolutionRecordAction, SkillEvolutionRecordListResponse` |
| B023 | `src/agents/permission_adapter.py:142`、`src/scheduler/capability_match.py:275`、`src/security/authorization/evaluation.py:124-156`（6 处）、`src/skills/migration.py:104-203`（6 处），及 `test/` 6 处 | 闭包通过默认参数绑定循环变量，例如 `lambda item: f(item, fault)` 改为 `lambda item, fault=fault: f(item, fault)`，嵌套 `def g(x):` 改为 `def g(x, fault=fault):`。若审查发现闭包在循环结束后才被调用（即原代码确有缺陷），在 tasks 里记录并补一个回归测试。`skills/migration.py` 若已被 `simplify-global-session-context` 删除则跳过 |
| B904 | `src/server/routes/agent_builder.py` 约 413 行 `raise ValueError("模型没有返回 JSON 对象")` | 末尾加 ` from None` |
| B905 | `src/server/routes/chat/scheduled_skill_preload.py:390`、`test/test_tenant_paths.py:39` | `zip(...)` 加 `strict=False`，保持原语义 |
| B007 | `src/skills/catalog.py:174` 的 `for entry, package in ...` | `entry` 改名 `_entry` |
| F541 | `src/storage/group_studio_records.py:130` | 去掉无占位符 f-string 的 `f` 前缀 |
| RUF006 | `src/server/routes/chat/completion.py:365` | 见 Decision 5 |
| F841 | `test/` 12 处 | 未使用的赋值改为不赋值；若赋值右侧有副作用则保留调用去掉变量名 |
| B008 | 4 处 `File(...)` | 由 `extend-immutable-calls` 放行，代码不改 |

完成后 `uv run ruff check src test` 必须输出 `All checks passed!`。

### 7. basedpyright 与基线

`backend/pyproject.toml` 追加：

```toml
[tool.basedpyright]
include = ["src"]
exclude = ["**/__pycache__", ".venv"]
pythonVersion = "3.11"
typeCheckingMode = "basic"
reportMissingTypeStubs = false
failOnWarnings = false
```

在 `backend/` 执行 `uv run basedpyright --writebaseline`，生成 `backend/.basedpyright/baseline.json` 并提交。之后 `uv run basedpyright` 自动读取基线，只报告新增错误。修掉基线内错误的提交，必须同时提交重新生成、变小了的基线。基线不增长由门禁脚本检查（Decision 9）。

### 8. 依赖分组

`backend/pyproject.toml`：从 `[project].dependencies` 删除 `"pytest>=9.0.2",`，追加：

```toml
[dependency-groups]
dev = [
    "pytest>=9.0.2",
    "ruff==0.17.1",
    "basedpyright==1.40.2",
]
```

然后在 `backend/` 执行 `uv lock`，提交 `uv.lock`。本地 `uv sync` 默认安装 dev 组，开发流程不变。`deploy/Dockerfile` 已是 `uv sync --frozen --no-dev`，不改。`scripts/build-backend-desktop.sh` 的两行改为 `uv sync --no-dev` 和 `uv run --no-dev --with pyinstaller pyinstaller --clean --noconfirm backend-server.spec`（`uv run` 默认会把 dev 组重新同步进环境，必须也加 `--no-dev`）。

### 9. 统一门禁脚本与 CI

新文件 `scripts/quality-gates.sh`（可执行权限 `chmod +x`）：

```bash
#!/bin/bash
# 统一质量门禁：本地、GitHub 工作流、Gitee 流水线都调用本脚本。
# 用法：scripts/quality-gates.sh [backend|frontend|all]
# 环境变量：QUALITY_BASE_REF 目标分支引用，默认 origin/master，用于类型基线不增长检查。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-all}"
BASE_REF="${QUALITY_BASE_REF:-origin/master}"

# 待 simplify-global-session-context 13.1 修复后删除这三条。
DESELECT=(
  --deselect test/test_chat_runtime_regressions.py::test_chat_run_context_includes_memory_prompt
  --deselect test/test_chat_runtime_regressions.py::test_chat_run_context_keeps_history_out_of_system_prompt
  --deselect test/test_global_session_foundation.py::test_historical_reference_allowlist_matches_repository
)

backend() {
  cd "$ROOT_DIR/backend"
  uv sync --frozen
  uv run --no-sync ruff check src test
  uv run --no-sync basedpyright
  python3 - "$BASE_REF" <<'PY'
import json, subprocess, sys
path = ".basedpyright/baseline.json"
count = lambda doc: sum(len(v) for v in doc.get("files", {}).values())
current = count(json.load(open(path, encoding="utf-8")))
shown = subprocess.run(["git", "show", f"{sys.argv[1]}:backend/{path}"], capture_output=True, text=True)
if shown.returncode != 0:
    print(f"未取到 {sys.argv[1]} 的类型基线，跳过不增长检查（当前 {current} 条）")
    sys.exit(0)
previous = count(json.loads(shown.stdout))
if current > previous:
    sys.exit(f"类型检查基线从 {previous} 条增加到 {current} 条，只允许减少")
print(f"类型检查基线 {previous} -> {current}")
PY
  uv run --no-sync pytest -q "${DESELECT[@]}"
}

frontend() {
  cd "$ROOT_DIR/frontend"
  npm ci
  npm run lint -- --max-warnings=0
  npm test
  npm run build
  npm run electron:compile
}

case "$TARGET" in
  backend) backend ;;
  frontend) frontend ;;
  all) backend; frontend ;;
  *) echo "用法: $0 [backend|frontend|all]" >&2; exit 2 ;;
esac
```

新文件 `.github/workflows/quality-gates.yml`：

```yaml
name: 质量门禁

on:
  workflow_dispatch:
  pull_request:
    paths:
      - 'backend/**'
      - 'frontend/**'
      - 'electron/**'
      - 'scripts/quality-gates.sh'
      - '.github/workflows/quality-gates.yml'

jobs:
  backend:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: '3.11'
      - name: 取目标分支（用于类型基线不增长检查）
        if: github.event_name == 'pull_request'
        run: git fetch --depth=1 origin "${{ github.base_ref }}"
      - name: 后端门禁
        env:
          QUALITY_BASE_REF: ${{ github.event_name == 'pull_request' && 'FETCH_HEAD' || 'origin/master' }}
        run: scripts/quality-gates.sh backend
  frontend:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '24'
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - name: 前端门禁
        run: scripts/quality-gates.sh frontend
```

`model-config.yml` 保留（覆盖 Windows），把其 pytest 列表追加 `test/test_guarded_bash_windows.py`。选择 macOS runner 是因为现有工作流和开发机都是 macOS，且 `electron:compile` 会构建 macOS 预览扩展。

### 10. 前端严格模式与两条 hooks 告警

- `frontend/tsconfig.app.json` 的 `compilerOptions` 在 `/* Linting */` 下增加 `"strict": true,`。
- `useGroupStudioRoom.ts` 约 170 行：依赖数组改为 `[groupId, localStudioSeed?.modelConfigId, localStudioSeed?.nonce, refreshStudio, studioSurface]`。与同文件约 214 行把 `localStudioSeed?.agentId` 与 `nonce` 一起列入依赖的写法一致；seed 对象在 store 中整体替换，`modelConfigId` 不会在 `nonce` 不变时单独变化。
- 约 755 行：`postToStudio` 是每次渲染都重建的普通函数，直接加入依赖会导致定时器在每次渲染时重置。改用"最新引用"写法：从 `react` 导入 `useRef`；在 `postToStudio` 定义（约 672 行）之后增加

```ts
  const postToStudioRef = useRef(postToStudio);
  useEffect(() => {
    postToStudioRef.current = postToStudio;
  });
```

然后把 755 行所在 effect 里的 `void postToStudio(` 改为 `void postToStudioRef.current(`，依赖数组不变。
- 验证：`npx eslint . --max-warnings=0` 与 `npx tsc -p tsconfig.app.json --noEmit` 都无输出。

### 11. 统一日志配置

新文件 `src/server/logging_setup.py`：

```python
"""后端日志配置：只配置 `src` 命名空间，不动 uvicorn 自己的 logger 和根 logger。"""

from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_MARKER = "_yinli_handler"


def configure_logging(level: str | None = None) -> None:
    """可重复调用；只会挂一个 stderr handler。propagate 保持 True，pytest caplog 仍能捕获。"""

    from src.config.settings import settings

    logger = logging.getLogger("src")
    logger.setLevel((level or settings.log_level or "INFO").upper())
    if any(getattr(handler, _MARKER, False) for handler in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    setattr(handler, _MARKER, True)
    logger.addHandler(handler)
```

`Settings` 新增 `log_level: str = "INFO"`（环境变量 `AGENT_WORKSTATION_LOG_LEVEL`）。`src/server/main.py` 在所有导入之后、`logger = logging.getLogger(__name__)` 之前调用 `configure_logging()`。`lifespan` 中 11 处 `print(...)`（约 83–133 行）全部改为 `logger.info(...)`，f-string 改为 `%s` 参数形式，文案不变；原来在 `except` 里的那一处（"Agent Python 环境初始化失败"）改为 `logger.warning(..., exc_info=True)`。

### 12. 提取记忆 Markdown 分节工具函数

`src/memory/markdown_reflection.py` 与 `src/tools/memory_reflection.py` 中下列 6 个函数 AST 完全一致，只依赖 `re` 和彼此：`_rebuild_summary_section`、`_parse_markdown_sections`、`_replace_section`、`_strip_source_reference`、`_is_near_duplicate_note`、`_dedupe_tokens`。新建 `src/memory/markdown_sections.py`，把它们以公开名（去掉前导下划线）搬入，函数体逐字不变。两个源文件删除各自的定义，改为：

```python
from src.memory.markdown_sections import (
    dedupe_tokens as _dedupe_tokens,
    is_near_duplicate_note as _is_near_duplicate_note,
    parse_markdown_sections as _parse_markdown_sections,
    rebuild_summary_section as _rebuild_summary_section,
    replace_section as _replace_section,
    strip_source_reference as _strip_source_reference,
)
```

两个文件内部调用点不变。另外 5 个同名但内容不同的函数（`_insert_note_into_section`、`_merge_conflicting_note`、`_conflict_key`、`_prepare_markdown_memory_content`、`_collapse_blank_lines`）保持各自实现；`src/memory/retrieval.py` 自己的 `_parse_markdown_sections` 也与这两份不同，不动。新增 `test/test_markdown_sections.py`，对 6 个函数各写至少一个特征测试（输入输出取自现有 `test_memory_reflection*.py` 中的样例）。若加入 `tools → memory` 的导入导致循环导入，则在使用处改为函数内延迟导入。

### 13. 阶段 B：路由改为同步、保留清单与防回退测试

新增 `backend/test/test_route_handler_concurrency_contract.py`。它既是防回退测试，也提供审计函数给改写脚本用：

```python
"""路由并发契约：没有 await 的 async 路由必须改成 def，除非在 KEEP_ASYNC 清单里。"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "api_route", "websocket"}

# 这些路由的调用链会碰到 create_task / get_running_loop / Task.cancel / Future.set_result 等事件循环 API，
# 改到线程池会变成跨线程调用；SSE 路由只构造异步生成器，留在循环里没有成本。
# 新增条目必须在本行注释写明理由。清单由 tools/loop_affinity_audit.py 生成后粘贴。
KEEP_ASYNC = {
    "server/auth/routes.py::disable",  # group_studio.sessions.cancel_run_task -> cancel
    "server/auth/routes.py::remove_user",  # group_studio.sessions.cancel_run_task -> cancel
    "server/routes/chat/handlers/sessions.py::delete_session",  # group_studio.sessions.cancel_session -> set_result
    "server/routes/group_studio.py::create_from_chat",  # group_studio.events._publish_nowait -> get_running_loop
    "server/routes/group_studio.py::stream_events",  # SSE 流式响应：只构造异步生成器，推送在事件循环里进行
    "server/routes/group_studio.py::pause_studio_route",  # cancel_session -> Future.set_result
    "server/routes/group_studio.py::resume_studio_route",  # _publish_nowait -> get_running_loop
    "server/routes/group_studio.py::post_message",  # cancel_session -> Future.set_result
    "server/routes/group_studio.py::rerun_studio",  # _publish_nowait -> get_running_loop
    "server/routes/group_studio.py::interrupt_studio",  # cancel_session -> Future.set_result
    "server/routes/group_studio.py::update_pin_route",  # _publish_nowait -> get_running_loop
    "server/routes/group_studio.py::add_member_route",  # _publish_nowait -> get_running_loop
    "server/routes/group_studio.py::mute_member_route",  # _publish_nowait -> get_running_loop
    "server/routes/group_studio.py::remove_member_route",  # _publish_nowait -> get_running_loop
    "server/routes/group_studio.py::close_studio_route",  # cancel_session -> Future.set_result
    "server/routes/group_studio.py::decide_approval",  # group_studio.approvals.decide_approval -> Future.set_result
    "server/routes/notifications.py::stream_notifications",  # SSE 流式响应：只构造异步生成器，推送在事件循环里进行
    "server/routes/workflows.py::start_workflow",  # server.routes.workflows.start_workflow -> create_task
    "server/routes/workflows.py::stream_workflow_events",  # SSE 流式响应：只构造异步生成器，推送在事件循环里进行
}


def _is_route_decorator(node: ast.expr) -> bool:
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    owner = node.func.value
    return node.func.attr in ROUTE_METHODS and isinstance(owner, ast.Name) and (owner.id.endswith("router") or owner.id == "app")


def _awaits(fn: ast.AsyncFunctionDef) -> bool:
    stack: list[ast.AST] = list(fn.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith)):
            return True
        if isinstance(node, ast.comprehension) and node.is_async:
            return True
        stack.extend(ast.iter_child_nodes(node))
    return False


def route_handlers() -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(map(_is_route_decorator, node.decorator_list)):
                found.append((f"{path.relative_to(SRC).as_posix()}::{node.name}", node))
    return found


def async_without_await() -> list[str]:
    return [key for key, node in route_handlers() if isinstance(node, ast.AsyncFunctionDef) and not _awaits(node)]


def test_async_routes_without_await_are_sync_or_kept() -> None:
    offenders = sorted(set(async_without_await()) - KEEP_ASYNC)
    assert not offenders, "以下 async 路由没有 await，应改为 def 或加入 KEEP_ASYNC 并写明理由：\n" + "\n".join(offenders)


def test_keep_list_has_no_stale_entries() -> None:
    current = set(async_without_await())
    stale = sorted(KEEP_ASYNC - current)
    assert not stale, "以下 KEEP_ASYNC 条目已不存在、已是 def 或已含 await，请移除：\n" + "\n".join(stale)
```

改写步骤（在 `backend/` 执行）：

```bash
uv run python - <<'PY'
import importlib.util, pathlib, re
spec = importlib.util.spec_from_file_location("contract", "test/test_route_handler_concurrency_contract.py")
contract = importlib.util.module_from_spec(spec); spec.loader.exec_module(contract)
targets = sorted(set(contract.async_without_await()) - contract.KEEP_ASYNC)
nodes = dict(contract.route_handlers())
for key in targets:
    rel, _ = key.split("::")
    path = pathlib.Path("src") / rel
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    line_no = nodes[key].lineno - 1
    lines[line_no], n = re.subn(r"\basync def ", "def ", lines[line_no], count=1)
    assert n == 1, key
    path.write_text("".join(lines), encoding="utf-8")
    print("converted", key)
PY
```

同一文件多个函数时行号不会偏移（只删字符不删行）。改写前，先在 `backend/` 执行 `uv run python ../openspec/changes/fix-runtime-correctness-and-quality-gates/tools/loop_affinity_audit.py` 重算保留清单，把标准输出整段替换 `KEEP_ASYNC` 的内容。该脚本对每个候选路由，沿模块内调用和 `from src... import` 调用最多追 7 层，查找属性或名字 `create_task`、`ensure_future`、`get_running_loop`、`get_event_loop`、`call_soon`、`call_soon_threadsafe`、`run_coroutine_threadsafe`、`start_soon`、`put_nowait`、`set_result`、`set_exception`、`call_later`、`cancel`，以及对象名含 `event` 的 `.set()`；函数体含 `text/event-stream` 的路由也保留。静态分析抓不到对象方法调用，由下面的 asyncio 调试模式兜底。

asyncio 调试模式兜底：CPython 的 `BaseEventLoop.call_soon` 在调试模式下会检查调用线程，发现是其他线程就抛出 `RuntimeError: Non-thread-safe operation invoked on an event loop other than the current one`。`Task.cancel`、`Future.set_result`、`Queue.put_nowait` 唤醒等待者时都会走 `call_soon`。因此改写完成后必须执行 `PYTHONASYNCIODEBUG=1 uv run pytest -q 2>&1 | tee /tmp/asyncio-debug.log`，并确认 `grep -c "Non-thread-safe operation" /tmp/asyncio-debug.log` 为 0。若不为 0，把对应路由改回 `async def` 并加入 `KEEP_ASYNC`。之后在门禁脚本的 pytest 前加 `PYTHONASYNCIODEBUG=1`，长期保留。

仍为 `async` 且含 `await` 的路由中，下列 5 处同步调用改用 `from starlette.concurrency import run_in_threadpool`：

| 位置 | 修改 |
|---|---|
| `src/server/auth/platform_models.py` `provided` 托管分支 | 把 `ensure_provider_key(...)` 与 `list_platform_models()` 收进本文件新增的同步函数 `_hosted_provided_models(user: dict) -> dict`（保留内部 `except Key2ApiError` 分支），路由里 `return await run_in_threadpool(_hosted_provided_models, require_user(request)["user"])` |
| `src/server/auth/routes.py` `write_platform_models` | `return save_platform_capability_document(payload)` 改为 `return await run_in_threadpool(save_platform_capability_document, payload)`，`except ValueError` 不变 |
| `src/server/platform_gateway.py` `chat_completions` | `record = _active_key(user_id)` 与 `allowed = {...}` 收进新增同步函数 `_resolve_key_and_models(user_id) -> tuple[dict, set[str]]`，用 `await run_in_threadpool(...)` 调用；两处 `_audit(...)` 改为 `await run_in_threadpool(_audit, user_id=..., ...)`（关键字参数原样传） |
| `src/server/routes/agent_builder.py` `plan_agents` | `binding = await run_in_threadpool(resolve_model_binding, validate=False)`，再 `with model_binding_scope(binding): return await _plan_agents(data)` |
| `src/server/routes/sessions.py` `upload_session_file` | `if get_session(session_id) is None:` 改为 `if await run_in_threadpool(get_session, session_id) is None:` |

线程安全审计：改成 `def` 后，原来被事件循环天然串行化的代码会在多个线程里并发执行。下列模块级可变容器当前没有锁，且会被被改写路由的调用链修改。每个模块新增一个 `threading.RLock()`；所有修改该容器的函数，以及"先读后写"的复合操作，都在持锁状态下执行。用 RLock 是为了避免同一线程内重入时死锁。

| 模块 | 容器 | 新锁名 |
|---|---|---|
| `src/server/routes/agents.py` | `_agents` | `_agents_lock` |
| `src/agents/factory.py` | `_agent_cache`、`_agent_last_used`、`_session_agents` | `_agent_cache_lock` |
| `src/mcp/registry.py` | `_mcp_servers`、`_mcp_header_refs` | `_mcp_lock` |
| `src/server/auth/cloud_client.py` | `_cached_models` | `_cached_models_lock` |
| `src/tenancy/quota.py` | `_disk_cache` | `_disk_cache_lock` |

`group_studio/*` 与 `routes/workflows.py` 的容器只由保留在事件循环里的路由和任务修改，不加锁。实施前在 `backend/` 执行 `uv run python ../openspec/changes/fix-runtime-correctness-and-quality-gates/tools/mutable_state_audit.py` 重新扫一次并更新上表。脚本的判断标准是：模块顶层赋值为 `{}`、`[]`、`set()`、`dict()`、`deque()` 等，在函数中被 `append/update/pop/clear/setdefault/add/discard/remove/insert/popitem`、下标赋值或 `global` 重新赋值，而文件内没有 `Lock(`。

### 14. 阶段 B：清理未使用导入（F401）

1. 从 Ruff `ignore` 列表中删除 `"F401"`。
2. 在 `backend/` 执行 `uv run python ../openspec/changes/fix-runtime-correctness-and-quality-gates/tools/reexport_audit.py`，找出被别的模块或测试使用的"未使用导入"（即隐式再导出）。判断标准：其他文件包含 `from <该模块> import ...<名字>`、`<该模块>.<名字>`（含 monkeypatch 字符串）或 `<模块末段>_routes.<名字>`。快照里共 42 个：`agents/factory.py` 4 个（`_bash_error_chunk`、`_decode_windows_shell_output`、`_resolve_windows_command_shell`、`_windows_utf8_command`，测试使用）；`mcp/registry.py` 2 个（`Any`、`delete_named_secret`，`server/routes/mcp.py` 使用）；`server/routes/mcp.py` 3 个（`_fetch_tools_with_mcp_client`、`_fetch_remote_mcp_tools_json_fallback` 被 `mcp/registry.py` 使用，`resolve_mcp_runtime_servers` 被 `chat/runtime.py` 使用）；`server/routes/memory.py` 20 个（测试使用）；`server/routes/skill_packages.py` 1 个；`server/routes/skills.py` 6 个（对话引擎使用）；`skills/registry.py` 6 个。
3. 对再导出：生产代码里的使用方改为从源模块导入（例如 `from src.mcp.registry import Any` 改为 `from typing import Any`）；只有测试使用的，测试改为从源模块导入；若测试是 monkeypatch 该模块上的名字（修改的是该模块的绑定），则保留导入并改为冗余别名 `from x import name as name`，在行尾注释 `# 显式再导出：<使用方文件>`。对话引擎对 `server/routes/skills.py`、`server/routes/mcp.py` 的再导出依赖由 `restructure-code-layering` 处理，本变更只改为冗余别名。
4. 其余执行 `uv run ruff check src test --select F401 --fix`，再跑全量测试。

### 15. 阶段 B：宽泛异常（BLE001、S110、S112）与 print（T201）

把 `"BLE001", "S110", "S112", "T201"` 加入 `select`，`per-file-ignores` 增加 `"src/security/authorization/evaluate_cli.py" = ["T201"]`（命令行工具本就输出到终端）。快照里异常类共 150 处，前几名为：`server/routes/chat/trace_recorder.py` 10、`storage/database.py` 8、`memory/nodes.py` 6、`memory/reflection.py` 5、`server/routes/chat/background_runs.py` 5、`tools/delegate_task.py` 5，其余 55 个文件各 1–4 处。每处按下表顺序选第一个适用的处理方式：

| 情况 | 处理 |
|---|---|
| try 块只调用一个异常类型明确的操作（`json.loads` → `json.JSONDecodeError`/`ValueError`；文件与目录操作 → `OSError`；`int()`/`float()` → `ValueError`/`TypeError`；字典取值 → `KeyError`；sqlite → `sqlite3.Error`；httpx → `httpx.HTTPError`；pydantic → `pydantic.ValidationError`） | 改为捕获具体类型，处理体不变；处理体是 `pass` 且含义是"忽略"的，改为 `with contextlib.suppress(<类型>):` |
| 边界代码：后台任务、工具执行、模型调用、追踪/记忆写入等"失败不能中断主流程"的位置 | 保留 `except Exception`，处理体第一行加 `logger.warning("<模块内的动作描述>失败", exc_info=True)`（Ruff 的 BLE001 不报带 `exc_info=True` 或 `logger.exception` 的处理块）；文件若没有 logger 则增加 `logger = logging.getLogger(__name__)` |
| 已经把异常转成 HTTP 错误、工具失败结果或追踪事件对外返回 | 保留，补 `logger.debug("...", exc_info=True)`，并在 `except` 行加 `# noqa: BLE001` 与理由，例如 `# noqa: BLE001 - 任意工具异常都要转成工具失败结果` |
| 清理/释放资源时的尽力而为（关闭连接、删除临时文件、取消自己创建的任务） | 能确定类型就用 `contextlib.suppress(<类型>)`；不能就保留并加 `# noqa: S110 - <理由>` |

禁止用没有理由的裸 `# noqa`。剩余 `print`（快照里除 main.py 与 evaluate_cli.py 外还有 16 处：`observation_runtime.py` 4、`observation_writer.py` 4、`storage/database.py` 2，`key2api.py`、`mail.py`、`auth/service.py`、`background_runs.py`、`runtime_events.py`、`trace_recorder.py` 各 1）按 Decision 11 的规则改为 logger。处于 `except` 块内的改为 `logger.warning(..., exc_info=True)`，其余改为 `logger.info`。`mail.py` 若是开发模式下把验证码打印到控制台，保持输出内容不变，只换成 `logger.info`。

### 16. 阶段 B：复杂度上限（C901）

把 `"C901"` 加入 `select`，并增加：

```toml
[tool.ruff.lint.mccabe]
max-complexity = 15
```

执行 `uv run ruff check src --select C901 --add-noqa`，为现有超限函数（快照 29 个）的 `def` 行自动加 `# noqa: C901`。随后在每个注释后手工补上 ` - 待 restructure-code-layering 拆分`。新增函数不得再加 C901 豁免。

### 17. 阶段 B：简体中文

dev 组增加 `"opencc-python-reimplemented==0.1.7"`（纯 Python，无本机库依赖）。转换脚本（在仓库根目录执行一次）：

```bash
cd backend && uv run python - <<'PY'
import pathlib
from opencc import OpenCC
convert = OpenCC("t2s").convert
roots = ["src", "test", "../frontend/src", "../electron"]
for root in roots:
    for path in pathlib.Path(root).rglob("*"):
        if path.suffix not in {".py", ".ts", ".tsx"} or "node_modules" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        new = convert(text)
        if new != text:
            path.write_text(new, encoding="utf-8")
            print(path)
PY
```

快照里会改 120 个文件 432 行，涉及的字都是真正的繁体字（最多的是 錄、與、寫、權、檔、徑、讀、時），唯一需要核对上下文的是 `memory/model_review.py` 的"回覆"，它会被转成"回复"，结果正确。转换后必须审查 `git diff --stat` 与全量测试；若有测试断言依赖这些文案，测试与实现已被同一脚本一起转换。

防回退测试 `backend/test/test_simplified_chinese_contract.py`：

```python
"""简体中文契约：源码、测试与前端中不得出现繁体字。"""

from __future__ import annotations

import re
from pathlib import Path

from opencc import OpenCC

REPO = Path(__file__).resolve().parents[2]
ROOTS = [REPO / "backend/src", REPO / "backend/test", REPO / "frontend/src", REPO / "electron"]
SUFFIXES = {".py", ".ts", ".tsx"}
CJK = re.compile(r"[\u4e00-\u9fff]")
# 确需保留繁体的 (相对仓库路径, 字符) 对，每条必须写理由。
ALLOWED: set[tuple[str, str]] = set()


def test_no_traditional_characters() -> None:
    convert = OpenCC("t2s").convert
    problems: list[str] = []
    for root in ROOTS:
        for path in sorted(root.rglob("*")):
            if path.suffix not in SUFFIXES or "node_modules" in path.parts:
                continue
            rel = path.relative_to(REPO).as_posix()
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not CJK.search(line):
                    continue
                simplified = convert(line)
                if simplified == line:
                    continue
                changed = {a for a, b in zip(line, simplified, strict=False) if a != b}
                if all((rel, ch) in ALLOWED for ch in changed):
                    continue
                problems.append(f"{rel}:{number}: {line.strip()} -> {simplified.strip()}")
    assert not problems, "发现繁体字，请改为简体：\n" + "\n".join(problems[:50])
```

### 18. 阶段 B：移除临时豁免

`simplify-global-session-context` 20.2 删除 `scope_guardrails_to_project` 后，从 `per-file-ignores` 删除 `src/server/routes/chat/runtime.py` 一行；13.1 修复三个测试后，从 `scripts/quality-gates.sh` 删除 `DESELECT` 数组及其引用。

## Risks / Trade-offs

- [路由改成 `def` 后，原来被事件循环串行化的代码并发执行，可能暴露竞态] → Decision 13 的锁表、asyncio 调试模式全量测试，以及分文件提交（每个路由文件一个提交）便于二分回滚。数据库访问已有 `database._lock`，JSON 文档存储已带锁。
- [线程池默认 40 个线程，长时间阻塞的同步路由可能耗尽线程池] → 当前的长耗时操作（模型调用、流式）都在 async 路由里，不受影响。卡顿监测可以暴露问题，必要时再调大 anyio 的 `total_tokens`。
- [类型基线与 `simplify-global-session-context` 的并行修改冲突] → 基线按文件和规则匹配。引入新错误的一方自己修，不允许重新生成基线来放行。
- [GitHub Actions 可能不会在 Gitee 仓库上执行] → 门禁以脚本为准；见 Open Questions。
- [繁简转换触及 120 个文件，影响 git blame] → 单独一个提交，提交信息以 `chore(text):` 开头，并写入 `.git-blame-ignore-revs`。

## Migration Plan

1. 阶段 A 按 tasks 1–9 顺序实施，每组一个提交。先提交依赖分组与工具配置，再提交代码修复，最后提交门禁脚本与工作流。
2. 确认 `simplify-global-session-context` 已归档后，阶段 B 按 tasks 10–15 实施。路由改写每个文件一个提交；繁简转换单独一个提交。
3. 没有数据迁移，没有接口变化。

### Rollback

- 门禁：删除 `scripts/quality-gates.sh` 与 `.github/workflows/quality-gates.yml`，不影响运行。
- Windows shell：回退 `guarded_bash.py` 一个文件。
- 托管中间件：回退 `middleware.py` 一个文件。
- 卡顿监测：设置 `AGENT_WORKSTATION_LOOP_LAG_WARN_MS=0` 即关闭，无需发版。
- 路由改写：按文件回退对应提交，并把路由加入 `KEEP_ASYNC`。

## Open Questions

- 仓库 `origin` 是 Gitee。如果没有镜像到 GitHub，`.github/workflows/*` 不会执行。实施者需要向仓库维护者确认：是否在 Gitee Go 中新建流水线调用 `scripts/quality-gates.sh all`，或者至少把它配置为本地 `pre-push` 钩子。无论结论如何，脚本本身都要完成。
