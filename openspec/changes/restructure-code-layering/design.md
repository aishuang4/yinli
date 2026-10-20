# Design

## Context

后端 `src/` 下 26 个顶层包中 24 个处在同一个强连通分量里，38 对包互相导入，647 处跨包导入写在函数内部以绕开循环导入。向上导入（低层导入高层）共 66 条，集中在四个枢纽：

| 枢纽 | 向上/反向边 | 性质 |
| --- | --- | --- |
| `server/routes/agents.py` 里的 Agent 注册表（`_agents` 缓存、`get_registered_agent` 等） | 11 个非 HTTP 模块导入它 | 领域状态住在路由文件里 |
| `server/routes/chat/` 下 93 个非 HTTP 文件、1.9 万行 | `runtime`/`subagents`/`memory`/`tools` 反向依赖路由层 20 条 | 对话引擎住在路由层 |
| `tenancy/lifecycle.py` | 导入 13 个上层包 | 跨模块编排放在最底层 |
| `config/model_catalog.py` ↔ `tenancy`、`server/auth` | 9/9 与 1/17 互引 | 模型目录是业务，混在配置包 |

其他快照数字（tasks 1.4 会重算）：双键兼容读取 134 处、未引用顶层函数 69–71 个、`# noqa: C901` 29 处、前端含裸 `fetch(` 的文件 40 个、存储层私有 `_lock`/`_connect` 被 17 个外部文件直接使用。

这些数字说明的是同一件事：没有任何一层是真正的底层，所以任何改动都能沿导入链传回"底层"再扩散到所有包。本变更只解决**位置与方向**问题——把文件放到正确的层、把依赖方向固定下来、给结构指标装上棘轮。它刻意不解决另外两个病因：

- 同一个运行时事实在 SourceEvent / RuntimeEvent / DomainEvent / RuntimeFrame 四层各有一份手写表示，且以字符串和 `dict[str, Any]` 连接——由后继变更 `unify-chat-runtime-events` 解决。
- `storage/database.py`（2373 行）同时拥有十几张表，19 个存储层以外的文件自己写 SQL，"每张表只有一个主人"不成立——由后继变更 `split-storage-by-aggregate` 解决。

与本变更并行、不依赖 `simplify-global-session-context` 的 `define-runtime-contracts` 提供 `contracts/` JSON Schema、前后端对账测试、AgentScope 事件 fixture → RuntimeFrame 序列金标，以及按目录拆分的 AGENTS.md。本变更的 W4 搬迁对话引擎时，以这些对账测试作为"没搬坏"的额外证据。

## Goals / Non-Goals

**Goals**

- `src/server/routes/` 只含 HTTP 适配；Agent 注册表、工具目录、对话引擎、租户编排各归其位。
- 向上导入从 66 条降到 15 条（全部是存储/配置层的延迟导入），并由 import-linter 与棘轮测试保证只减不增；W5 之后 `src.server` 成为任何非 server 模块都不得导入的叶子（`forbidden` 契约，零例外）。
- 存储层只通过公开的 `transaction()` / `control_transaction()` 被使用，连接在事务结束时关闭。
- 每个搬迁目标包在搬入的同一个提交里具备：显式公开面（`__all__` 或 `api.py`）与一份只写不变量和测试锚点的 `AGENTS.md`。
- 前端所有请求经由统一客户端，不再改写 `window.fetch`。
- 全程不改变 HTTP 接口、SSE 事件、SQLite 结构与任何用户可见行为（W9 的错误文案除外）。

**Non-Goals**

- 不统一事件类型、不改投影管线、不减少持久化份数（→ `unify-chat-runtime-events`）。
- 不拆 `database.py`、不迁移表所有权（→ `split-storage-by-aggregate`）。
- 不把事件分发热点改写为规则表：那段代码在事件统一后整体消失，只保留它们的金标测试作为后继变更的回归基线（见 Decision 13）。
- 不追求业务模块之间完全无环；`agents ↔ runtime` 这类同层互引不在本变更处理范围。
- 不重新设计前端交互；W9 只做请求层与 `ChatPage` 的机械拆分。

## Decisions

### 1. 分层契约与基线棘轮

用 import-linter 的 `layers` 契约固定依赖方向，现有向上导入全部写进 `ignore_imports` 作为基线，基线只能减少。初始层次（与 `tools/layer_baseline.py` 的 `DEFAULT_LAYERS` 一致，脚本会优先读取 pyproject 中已配置的契约）：

```toml
[tool.importlinter]
root_package = "src"
include_external_packages = false

[[tool.importlinter.contracts]]
name = "backend layers"
type = "layers"
layers = [
  "src.server",
  "src.group_studio | src.scheduler | src.runtime | src.subagents | src.mcp | src.agents | src.tools | src.memory | src.security | src.catalog | src.skills | src.sessions | src.notifications | src.sandbox | src.vector_store | src.project_files | src.project_resources | src.runtime_context | src.session_migration | src.models | src.generated_file_preview",
  "src.storage",
  "src.tenancy",
  "src.config | (src.common) | src.exception_detail",
]
ignore_imports = [
  # 由 tools/layer_baseline.py 生成，整段替换；只删不加
]
```

同层之间（`|` 分隔）互相导入不受约束；`(src.common)` 的括号表示该包在 W2 之前尚不存在。`src/` 下每个顶层包和顶层模块都必须出现在某一层，`layer_baseline.py` 的标准错误会列出未归层模块（开工时只有空包 `src.model`，W8 删除）。

W5 结束后层次收成四层（见 Decision 9），并追加第二条零例外契约，使 `src.server` 成为叶子：

```toml
[[tool.importlinter.contracts]]
name = "server is a leaf"
type = "forbidden"
source_modules = ["src.group_studio", "src.scheduler", "src.runtime", "src.subagents", "src.mcp", "src.agents", "src.tools", "src.memory", "src.security", "src.catalog", "src.skills", "src.sessions", "src.notifications", "src.sandbox", "src.vector_store", "src.models", "src.generated_file_preview", "src.storage", "src.config", "src.tenancy", "src.common", "src.exception_detail"]
forbidden_modules = ["src.server"]
```

该契约不允许 `ignore_imports`。它是 DeerFlow `test_harness_boundary.py` 的等价物：整个后端只有这一条边界是绝对的，其余层间关系允许带基线过渡。

基线棘轮 `backend/test/test_layer_baseline_ratchet.py`：

```python
"""分层基线只减不增：每条 ignore_imports 必须仍对应真实导入，总数不得超过上限。"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
MAX_ENTRIES = 66  # 每删一条基线就下调


def _baseline() -> list[str]:
    config = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    for contract in config["tool"]["importlinter"]["contracts"]:
        if contract["name"] == "backend layers":
            return list(contract.get("ignore_imports", []))
    raise AssertionError("pyproject.toml 缺少 backend layers 契约")


def _import_exists(source: str, target: str) -> bool:
    path = BACKEND / Path(*source.split(".")).with_suffix(".py")
    if not path.exists():
        path = BACKEND / Path(*source.split(".")) / "__init__.py"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    parent, _, leaf = target.rpartition(".")
    patterns = [rf"^\s*import {re.escape(target)}\b", rf"^\s*from {re.escape(target)} import\b"]
    if parent:
        patterns.append(rf"^\s*from {re.escape(parent)} import [^\n]*\b{re.escape(leaf)}\b")
    return any(re.search(p, text, re.MULTILINE) for p in patterns)


def test_baseline_entries_are_live() -> None:
    stale = [entry for entry in _baseline() if not _import_exists(*[s.strip() for s in entry.split("->")])]
    assert not stale, "以下基线条目对应的导入已不存在，请从 ignore_imports 删除：\n" + "\n".join(stale)


def test_baseline_does_not_grow() -> None:
    count = len(_baseline())
    assert count <= MAX_ENTRIES, f"分层基线有 {count} 条，超过上限 {MAX_ENTRIES}；新增向上导入要改代码，不要加基线"
    assert count == MAX_ENTRIES, f"分层基线已减少到 {count} 条，请把 MAX_ENTRIES 下调为 {count}"


def test_contract_is_kept() -> None:
    result = subprocess.run([sys.executable, "-m", "importlinter.cli", "lint-imports"], cwd=BACKEND, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
```

备选：只用 `forbidden` 契约逐对禁止。不采用：26 个包两两组合无法维护，`layers` 一条就表达了全部方向。

### 2. 波次顺序、提交前检查与搬迁包的验收

**波次顺序**：W1（注册表与工具目录）→ W2+W3（密钥与平台客户端）→ W4（对话引擎）→ W5（租户编排）→ W6（存储访问）→ W7（复杂度热点）→ W8（结构检查与清理）；W9（前端）可与后端任一波次并行，合并顺序放最后。W4 依赖 W1（引擎不再引用路由模块后才能整体搬走），W6/W7 依赖 W4（它们改写的文件已在新路径）。

**搬家与改逻辑分开提交**：每个波次里由脚本完成的机械搬迁单独一个提交，手工收尾与任何逻辑改动另一个提交。出了问题要能区分"搬坏的"和"改坏的"。

**提交前检查**（五条，任一失败不得提交）：

```bash
uv run --no-sync ruff check src test
uv run --no-sync basedpyright            # 类型基线条数不得增加（scripts/quality-gates.sh 已含该比较）
uv run --no-sync lint-imports
uv run --no-sync pytest -q -p no:cacheprovider 2>&1 | grep -E '^(FAILED|ERROR) ' | sort > /tmp/after.txt
diff /tmp/before.txt /tmp/after.txt      # 必须无输出：失败集合与开工前完全一致
```

`/tmp/before.txt` 在 tasks 1.3 于未改动代码上生成一次；全程不允许"顺手修"既有失败，也不允许引入新失败。

**搬迁目标包的验收**（W1、W2、W3、W4、W5 每个新建或搬入的包都适用，在搬入的同一提交里完成）：

1. 显式公开面。单文件模块在文件顶部写 `__all__`；多文件包在 `__init__.py` 写 `__all__` 或提供 `api.py`。公开面的内容由脚本算出：所有在该包外部出现的 `from <包> import <名字>` 的名字集合。不在公开面里的名字，外部模块不得再导入（W4 的 `src/runtime/chat/` 例外：该包即将被 `unify-chat-runtime-events` 重塑，只在 `AGENTS.md` 的"入口"段列出被外部导入的模块清单，不强制 `api.py`）。
2. `AGENTS.md`。固定五段：职责与边界（一句话职责、拥有的表、不得导入什么）；入口（公开面清单，每项一句话）；不变量（每条附"`test/xxx.py` 钉住"）；禁止（本变更中删掉的旧做法，防止长回来）；改动政策（"改本目录代码必须同步更新本文件"）。控制在 800 词以内。每个有 `AGENTS.md` 的目录同时放一行 `CLAUDE.md`：`@AGENTS.md`。
3. 结构测试 `test/test_agents_md.py`（W1 新建，后续波次只增加目录）：`REQUIRED_DIRS` 中每个目录必须有 `AGENTS.md` 与 `CLAUDE.md`；`AGENTS.md` 中以 `test/` 开头的路径必须存在。

这样做的原因：本仓库大部分代码由编码智能体产出，分层规则如果不写在它们按目录加载的文件里，下一次就会把注册表放回路由文件。文档与结构在同一个提交里产生，不会脱节。

### 3. W1a：Agent 注册表搬到 `src/agents/registry.py`

`src/server/routes/agents.py` 里 7 个路由函数之外有 24 个顶层定义，其中 22 个函数/变量与 2 个请求模型（`AgentCreate`、`AgentUpdate`）。全部搬到 `src/agents/registry.py`，路由模块改为 `from src.agents import registry as agent_registry` 并通过 `agent_registry.<名字>` 访问。22 个函数/变量（按源文件顺序；以 `tools/extract_symbols.py` 的 AST 扫描结果为准）：

`_job_service`、`_pause_jobs_for_agent_capability_change`、`AGENTS_NAMESPACE`、`_agents`、`_build_default_agent`、`_build_default_websearch_job_agent`、`upsert_agent`、`_ensure_default_agent`、`_ensure_default_websearch_job_agent`、`_ensure_system_agents`、`_upgrade_default_agent_prompt`、`_apply_job_agent_fields`、`_sync_agents_from_catalog`、`_load_agents`、`_save_agents`、`_install_agent_registry`、`get_registered_agent`、`list_registered_agents`、`list_workspace_agents`、`remove_mcp_tools_from_agents`、`remove_skills_from_agents`、`remove_knowledge_tools_from_agents`。

两个请求模型随之搬走是因为 `upsert_agent` 等函数的签名引用它们；留在路由模块会让 registry 反向导入 server。路由模块从 registry 重新导入它们用于请求体解析。

```bash
uv run python $T/extract_symbols.py \
  --from src/server/routes/agents.py --to src/agents/registry.py --alias agent_registry \
  --names _job_service,_pause_jobs_for_agent_capability_change,AGENTS_NAMESPACE,_agents,_build_default_agent,_build_default_websearch_job_agent,upsert_agent,_ensure_default_agent,_ensure_default_websearch_job_agent,_ensure_system_agents,_upgrade_default_agent_prompt,_apply_job_agent_fields,_sync_agents_from_catalog,_load_agents,_save_agents,_install_agent_registry,get_registered_agent,list_registered_agents,list_workspace_agents,remove_mcp_tools_from_agents,remove_skills_from_agents,remove_knowledge_tools_from_agents \
  --by-name AgentCreate,AgentUpdate \
  --docstring "Agent 注册表：进程内缓存、默认 Agent 保障与按能力移除。路由层只做 HTTP 适配。"
uv run python $T/move_symbols.py --old src.server.routes.agents --new src.agents.registry --names get_registered_agent,list_registered_agents,list_workspace_agents,remove_mcp_tools_from_agents,remove_skills_from_agents,remove_knowledge_tools_from_agents,AgentCreate,AgentUpdate,upsert_agent
uv run --no-sync ruff check --select I --fix src test
uv run --no-sync ruff check --select F401 --fix src/server/routes/agents.py
```

`_job_service` 原来是模块级实例，会在导入时拉起 `scheduler`，形成 `agents → scheduler → agents` 的导入环。改为延迟创建：

```python
from functools import cache

@cache
def _job_service() -> "JobService":
    from src.scheduler.service import JobService  # 延迟导入：打破 agents → scheduler → agents 环
    return JobService()
```

调用处 `_job_service.pause(...)` 改为 `_job_service().pause(...)`。测试若 patch `_job_service`，改为 patch `agent_registry._job_service` 为返回替身的函数。

`_agents` 缓存只有一份：路由模块不再持有自己的 `_agents` 绑定，统一读写 `agent_registry._agents`，这样测试 `monkeypatch.setattr(agent_registry, "_agents", {...})` 对路由同样生效。`_install_agent_registry()` 的顶层调用随函数一起搬到 registry 末尾，保证导入 registry 即完成默认 Agent 保障。

验收（Decision 2）：`src/agents/registry.py` 顶部 `__all__` 列出 9 个被外部导入的名字；新建 `src/agents/AGENTS.md`，不变量至少包含"默认 Agent 与系统 Agent 由 `_ensure_system_agents` 在导入时保障，`test/test_default_agent.py`、`test/test_system_job_agent.py` 钉住"（测试文件名以实际存在者为准）。

### 4. W1b：HTTP/知识库工具目录搬到 `src/tools/http_tool_catalog.py`

`src/server/routes/tools.py` 中被 `src.server` 以外模块引用的名字及其在该文件内的传递依赖，用下面的闭包脚本重算后搬走：

```bash
uv run python - <<'EOF'
import ast, pathlib, re
src = pathlib.Path("src/server/routes/tools.py").read_text(encoding="utf-8")
tree = ast.parse(src)
defs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
defs |= {t.id: n for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
external = set()
for p in pathlib.Path("src").rglob("*.py"):
    if p.parts[1] == "server": continue
    for m in re.finditer(r"from src\.server\.routes\.tools import ([^\n]+)", p.read_text(encoding="utf-8")):
        external |= {x.strip() for x in m.group(1).split(",")}
seen, todo = set(), list(external)
while todo:
    name = todo.pop()
    if name in seen or name not in defs: continue
    seen.add(name)
    todo += [n.id for n in ast.walk(defs[name]) if isinstance(n, ast.Name) and n.id in defs]
print(",".join(sorted(seen)))
EOF
```

输出即 `extract_symbols.py --names` 的参数，目标 `src/tools/http_tool_catalog.py`，alias `http_tool_catalog`。`_require_http_url` 抛 `fastapi.HTTPException`，随之进入 `src/tools/`；改异常类型会改变错误响应，超出本变更范围（Risks 已记录）。

### 5. W1c：引擎直接依赖 `src.skills.*` 与 `src.mcp.registry`

对话引擎中所有 `from src.server.routes.skills import ...`、`from src.server.routes.mcp import ...` 改为从被路由模块再导出的原始模块导入（以路由文件顶部的 import 为准）。`src/mcp/registry.py` 中通过 `src.server.routes.mcp._fetch_*` 绕道调用自身的写法，改为直接调用本模块函数；测试里对 `routes.mcp._fetch_*` 的 patch 改为 patch `src.mcp.registry.<函数>`。

完成判定：

```bash
grep -rn "server\.routes\.\(skills\|mcp\|agents\|tools\)\|server\.routes import \(skills\|mcp\|agents\|tools\)" src --include=*.py | grep -v "^src/server/"   # 无输出
grep -rn "routes.mcp._fetch\|mcp_routes" src test   # 无输出
```

### 6. W2：`server/auth/secrets.py` 搬到 `src/common/secrets.py`

`secrets.py` 只依赖标准库与 `keyring`，却被 `config`、`models`、`tenancy` 导入，是"底层导入 server"中最无理由的一条。新建 `src/common/`（`__init__.py` 只有 docstring 与 `__all__`），`git mv` 后批量改写引用。`src/common` 不得导入任何 `src.*`，由 `grep -n "^from src\|^import src" src/common/*.py` 无输出保证。契约中的 `(src.common)` 改为 `src.common`。

### 7. W3：平台客户端搬到 `src/models/platform/`，模型目录不再调用 auth

`server/auth/key2api.py`、`server/auth/cloud_client.py` 是对上游 key2api 与引力云端的 HTTP 客户端，不是鉴权中间件；`config/model_catalog.py` 通过它们拉取上游模型列表，造成 `config → server.auth` 17 条反向导入。

1. 新建 `src/models/platform/`，`git mv` 两个文件进去；`account_path` 从 `server/auth/__init__.py` 搬到 `cloud_client.py`。
2. `config/model_catalog.py` 的 `ensure_hosted_upstream_catalog` 拆成两半：纯数据部分保留为 `apply_upstream_models(names: list[str], upstream_base_url: str) -> None`；调用 key2api 的部分新建为 `src/models/platform/upstream_catalog.py::ensure_hosted_upstream_catalog()`，内部先取名单再调用 `apply_upstream_models`。原调用方改调新位置。
3. 完成判定：`grep -n "key2api" src/config/*.py` 无输出；`grep -n "server" src/models/platform/*.py` 无输出。

验收（Decision 2）：`src/models/platform/__init__.py` 写 `__all__`；新建 `src/models/AGENTS.md`，不变量包含"`config` 只做纯数据合并，任何网络调用都在 `models/platform`"并指向对应测试。

### 8. W4：对话引擎整体搬到 `src/runtime/chat/`

`src/server/routes/chat/` 下除 `__init__.py`、`router.py`、`handlers/` 外的 93 个文件整体 `git mv` 到 `src/runtime/chat/`，与现有 `src/runtime/`（`run_context` 等）合并，`runtime ↔ server.routes.chat` 14/13 的互引随之消失。只搬不改。

```bash
uv run python $T/move_package.py --old src.server.routes.chat --new src.runtime.chat \
  --keep __init__.py --keep router.py --keep handlers --dry-run   # 先看"需人工处理"清单
uv run python $T/move_package.py --old src.server.routes.chat --new src.runtime.chat \
  --keep __init__.py --keep router.py --keep handlers
```

脚本改写 `src/`、`test/`、`backend-server.spec` 中的 import、`monkeypatch.setattr` / `mock.patch` 字符串和 `importlib` 字符串。手工收尾五步：

1. `CHAT_PREFIX` 等只被 `router.py` 使用的常量留在路由包；被引擎使用的常量随引擎走，路由包从新位置导入。
2. `src/server/routes/chat/__init__.py` 的 docstring 改为"Chat HTTP 适配层"，再导出只剩 `router` 与 `handlers`。
3. `src/runtime/chat/__init__.py` 新建，docstring 写"Chat 运行时：ReAct 循环、投影、Trace、Team Runtime"，不做任何再导出（避免 93 个模块的导入环）。
4. `backend-server.spec` 的 `hiddenimports` 若含旧路径，由脚本改写；人工复核 `grep -n "routes.chat" *.spec` 只剩 `router`/`handlers`。
5. `docs/` 与 `AGENTS.md` 中的旧路径按 Decision 19 第 4 步的 grep 清理。

`test/test_import_boundaries.py` 中对 `src.server.routes.chat.*` 的断言改为 `src.runtime.chat.*`。

验收（Decision 2）：新建 `src/runtime/chat/AGENTS.md`，"入口"段由脚本生成——列出被 `handlers/` 与 `src.runtime.chat` 以外模块导入的模块与名字；"不变量"段至少包含 Event Stream V3 契约的五条（`RUNTIME_EVENT` 是唯一 SSE 事件名、Frame 只有 open/delta/close/snapshot/done、`seq` 单 Run 内单调、HITL 不结束订阅、`done` 晚于所有 close），各自指向现有测试；"禁止"段写明"不要在这里新增事件类型的翻译层，事件统一见 `unify-chat-runtime-events`"。本包不强制 `api.py`（理由见 Decision 2）。

备选：搬到 `src/chat/` 新建顶层包。不采用：`src/runtime/` 已存在且内容本来就是 Chat 运行时的一部分，分开只会多一个同层互引。


### 9. W5：租户生命周期编排上移到 `src/server/tenant_lifecycle.py`，合并底层

> 位置说明：租户生命周期是跨模块编排，不是 HTTP 适配。放进 `src/server/` 是因为当前只有它一个编排模块，单独开一层不值得；`split-storage-by-aggregate` 落地后若编排模块增多（启动 reconcile、会话迁移等），再统一迁到 `src/app/`。本变更不为此单独改动。

> W5 完成后立即生效的硬边界：按 Decision 1 追加 `server is a leaf` 的 `forbidden` 契约（零 `ignore_imports`）。此后任何非 `src.server` 模块导入 `src.server.*` 直接使 `lint-imports` 失败，不再进入基线。W5 之前该契约无法通过，因为 `tenancy.lifecycle` 等尚未搬走。

`src/tenancy/lifecycle.py` 导入了 storage、catalog、group_studio、sandbox、agents 等 13 个上层模块，本质是服务层编排。步骤：

```bash
git mv src/tenancy/lifecycle.py src/server/tenant_lifecycle.py
grep -rl "src\.tenancy\.lifecycle" src test | xargs sed -i '' 's/src\.tenancy\.lifecycle/src.server.tenant_lifecycle/g'
```

快照中的使用方：`src/server/auth/service.py`（5 处延迟导入）、`src/server/auth/middleware.py`（`fix-runtime-correctness-and-quality-gates` 加入的 `ensure_tenant`）、`src/server/main.py`、`src/scheduler/ticker.py`，以及测试 `test_control_db.py`、`test_tenant_background.py`、`test_tenant_isolation.py`、`test_tenant_lifecycle.py`、`test_upstream_models.py`。

`scheduler/ticker.py` 不能导入 `src.server.*`。它只用 `iter_tick_tenants`，而 `iter_tick_tenants`、`startup_tenants` 都只是 `iter_process_tenants` 的别名，后者只读控制面。把它搬进存储层并删掉两个别名：

```python
# src/storage/control_records.py 末尾追加（该文件已导入 TenantContext 并定义 list_active_tenants）
def iter_process_tenants() -> list[TenantContext]:
    """desktop 用当前租户；hosted 只列出控制面里仍然 active 的租户。"""

    from src.tenancy.context import current_tenant, is_hosted_mode

    if is_hosted_mode():
        return list_active_tenants()
    return [current_tenant()]
```

- `src/server/tenant_lifecycle.py`：删除 `iter_process_tenants`、`startup_tenants`、`iter_tick_tenants` 三个函数。
- `src/server/main.py`：`from src.server.tenant_lifecycle import startup_tenants` 改为 `from src.storage.control_records import iter_process_tenants`，`for ctx in startup_tenants():` 改为 `for ctx in iter_process_tenants():`。
- `src/scheduler/ticker.py`：延迟导入改为 `from src.storage.control_records import iter_process_tenants`，循环改为 `for ctx in iter_process_tenants():`。
- `grep -rn "startup_tenants\|iter_tick_tenants" src test` 应无输出（快照中测试没有 patch 这两个名字；若归档后有，改为 patch `src.storage.control_records.iter_process_tenants`）。

然后修改契约，把 `src.tenancy` 并入底层（`config` 与 `tenancy` 本来就互相依赖）：

```toml
layers = [
    "src.server",
    "src.group_studio : ... 中间层不变 ...",
    "src.storage",
    "src.config : src.tenancy : src.common : src.exception_detail",
]
```

重新生成基线，快照期望为 15 条：13 条 `src.tenancy.lifecycle -> *` 因模块进入 server 层而消失，4 条 `src.config.* -> src.tenancy.context` 因同层而消失。`src/tenancy/__init__.py` 只再导出 `context` 中的名字，无须改动。

### 10. W6a：连接自动关闭与公开事务入口

现状：`_connect()` 返回的 `sqlite3.Connection` 用作上下文管理器时只提交或回滚，不关闭。`with database._lock, database._connect() as conn:` 及记录类内部的 `with self.lock, self.connect() as conn:`（快照约 194 处）每次都泄漏一个连接，靠垃圾回收关闭。已核对：仓库中没有 `conn = _connect()` 赋值后跨块使用的写法，也没有 `with conn:` 复用同一连接；块内取得的游标只在块外读取 `rowcount`/`lastrowid`，这两个属性在连接关闭后仍可读。

做法：让连接类在退出上下文时关闭自己。这样一处改动就修好全部约 250 处用法，再给外部模块提供公开入口。

```python
# src/storage/database.py：放在 _connect() 之前
class ClosingConnection(sqlite3.Connection):
    """`with conn:` 结束时先提交或回滚，再关闭连接。"""

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


# _connect() 中
    conn = sqlite3.connect(path, timeout=30, factory=ClosingConnection)


# 紧接 _connect() 之后
@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """持有当前租户库锁执行一个事务：正常结束提交，异常回滚并原样抛出，最后关闭连接。"""

    with _lock, _connect() as conn:
        yield conn
```

`src/storage/control_db.py` 同样处理：定义同名 `ClosingConnection`（不跨文件导入，避免 control_db 依赖 database），`control_connect()` 中加 `factory=ClosingConnection`，并新增：

```python
@contextmanager
def control_transaction() -> Iterator[sqlite3.Connection]:
    """持有控制面锁执行一个事务，语义同 database.transaction()。"""

    with _control_lock, control_connect() as conn:
        yield conn
```

两个文件都要补 `from collections.abc import Iterator` 与 `from contextlib import contextmanager`。

改写外部用法：

```bash
uv run python $T/rewrite_storage_access.py --dry-run   # 先看清单
uv run python $T/rewrite_storage_access.py
```

快照中脚本自动改写 102 处（src 56 处、test 46 处），剩下的"需人工处理"按下表处理：

| 位置 | 处理 |
|---|---|
| `runtime/chat/projection/domain_events.py` `DomainEventRepository.__init__` | 参数 `connect=None, lock=None` 改为 `transaction=None`；默认值 `database.transaction`；属性 `self.connect`、`self.lock` 改为 `self.transaction`；类内 4 处 `with self.lock, self.connect() as conn:` 改为 `with self.transaction() as conn:` |
| `runtime/chat/projection/projection_writer.py` `ProjectionWriter.__init__` | 同上（类内 8 处）；构造 `DomainEventRepository(ensure_db=self.ensure_db, connect=self.connect, lock=self.lock)` 改为 `DomainEventRepository(ensure_db=self.ensure_db, transaction=self.transaction)` |
| `skills/migration.py` 备份数据库 | `with database._lock, database._connect() as source, sqlite3.connect(temp) as target:` 改为 `with database.transaction() as source, closing(sqlite3.connect(temp)) as target:`，并在该处延迟导入 `from contextlib import closing` |
| `test/test_permission_records.py` 约 202 行 | 用 monkeypatch 替换 `database._connect` 统计连接，是允许的探针写法，保持不变 |
| `test/test_storage_transaction.py`、`test/test_storage_private_access.py` | 新增文件自身，允许 |

改写后对改动过的文件执行 `uv run --no-sync ruff check --select F401 <文件>`，只删除报告中的 `closing`（`contextlib`）导入，不加 `--fix`。`src/storage/` 内记录类收到的 `connect=_connect, lock=_lock` 注入保持不变，`ClosingConnection` 已覆盖它们。

以上改动已在沙箱快照上完整执行：全量测试的失败集合与改动前相同，新增的 7 个测试全部通过。

新文件 `backend/test/test_storage_transaction.py`：

```python
"""公开事务入口：正常结束提交，异常回滚，两种情况都关闭连接。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.storage import control_db, database


def _connector(path: Path):
    def connect() -> sqlite3.Connection:
        conn = sqlite3.connect(path, factory=database.ClosingConnection)
        conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
        return conn

    return connect


def test_transaction_commits_and_closes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database, "_connect", _connector(tmp_path / "t.db"))
    with database.transaction() as conn:
        conn.execute("INSERT INTO t VALUES ('a')")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
    with database.transaction() as check:
        assert check.execute("SELECT v FROM t").fetchall() == [("a",)]


def test_transaction_rolls_back_and_closes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(database, "_connect", _connector(tmp_path / "t.db"))
    with pytest.raises(RuntimeError, match="boom"):
        with database.transaction() as conn:
            conn.execute("INSERT INTO t VALUES ('a')")
            raise RuntimeError("boom")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
    with database.transaction() as check:
        assert check.execute("SELECT count(*) FROM t").fetchone()[0] == 0


def test_control_transaction_closes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(control_db, "control_connect", _connector(tmp_path / "c.db"))
    with control_db.control_transaction() as conn:
        conn.execute("INSERT INTO t VALUES ('a')")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_closing_connection_keeps_cursor_counts(tmp_path: Path) -> None:
    with sqlite3.connect(tmp_path / "r.db", factory=database.ClosingConnection) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('a')")
        cursor = conn.execute("UPDATE t SET v = 'b'")
    assert cursor.rowcount == 1
```

新文件 `backend/test/test_storage_private_access.py`（同时承担 Decision 11 的检查）：

```python
"""存储层私有入口不外泄；本地 UTC 时间字符串函数不重复。"""

from __future__ import annotations

import ast
import re
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent
SRC_ROOT = TEST_ROOT.parent / "src"
SRC_OWNERS = {"storage/database.py", "storage/control_db.py"}
# 只允许这些测试用 monkeypatch 替换私有连接函数做探针，不允许直接用它们读写。
TEST_INSTRUMENTATION = {
    "test_permission_records.py",
    "test_storage_transaction.py",
    "test_storage_private_access.py",
}
PRIVATE = re.compile(
    r"\bdatabase\._(?:lock|connect)\b"
    r"|\b_control_lock\b"
    r"|\bcontrol_connect\b"
    r"|from src\.storage\.database import [^\n]*\b_(?:lock|connect)\b"
)
UTC_BODIES = {
    "return datetime.now(timezone.utc).isoformat()",
    "return datetime.now(UTC).isoformat()",
}


def _violations(root: Path, skip: set[str]) -> list[str]:
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel in skip or "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PRIVATE.search(line):
                found.append(f"{root.name}/{rel}:{number}: {line.strip()}")
    return found


def test_src_uses_public_transactions() -> None:
    found = _violations(SRC_ROOT, SRC_OWNERS)
    assert not found, "请改用 database.transaction() 或 control_db.control_transaction()：\n" + "\n".join(found)


def test_tests_use_public_transactions() -> None:
    found = _violations(TEST_ROOT, TEST_INSTRUMENTATION)
    assert not found, "测试请改用 database.transaction()：\n" + "\n".join(found)


def test_no_local_utc_now_iso_helpers() -> None:
    found: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel == "common/time.py" or "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.FunctionDef):
                continue
            body = [
                stmt for stmt in node.body
                if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
            ]
            if len(body) == 1 and ast.unparse(body[0]) in UTC_BODIES:
                found.append(f"src/{rel}:{node.lineno} {node.name}")
    assert not found, "请改为 from src.common.time import utc_now_iso as <原名>：\n" + "\n".join(found)
```

备选：把约 250 处 `with` 全部改写成 `transaction()`。不采用：改动面大且全是机械改动，`ClosingConnection` 用一处改动达到同样效果，而且已用全量测试验证。

### 11. W6b：统一 UTC 时间字符串

```bash
uv run python $T/replace_utc_helpers.py --dry-run
uv run python $T/replace_utc_helpers.py
```

脚本先创建 `src/common/time.py`：

```python
"""时间工具：统一生成 UTC ISO 8601 字符串。"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串，带 +00:00 时区后缀。"""

    return datetime.now(timezone.utc).isoformat()
```

然后删除 15 个本地定义，在文件开头的 import 区末尾插入 `from src.common.time import utc_now_iso as <原名>`。保留原名，所以调用点和 monkeypatch 都不用改。快照中的 15 处（W4 之后的路径）：`catalog/system_defaults.py` `utc_now`、`group_studio/events.py` `_utc_now`、`mcp/registry.py` `_utc_now`、`memory/nodes.py` `_utc_now`、`scheduler/skill_manifest.py` `_utc_now`、`runtime/chat/projection/domain_events.py` `_utc_now`、`runtime/chat/team_runtime/store.py` `utc_now`、`server/routes/sandbox.py` `_utc_now`、`server/routes/tools.py` `_utc_now`（W1b 后在 `tools/http_tool_catalog.py`）、`server/routes/workflows.py` `_utc_now`、`skills/registry.py` `_utc_now`、`storage/control_records.py` `_now`、`storage/database.py` `_utc_now`、`storage/permission_records.py` `_now`、`subagents/task_store.py` `utc_now`。不命中、也不应改的：`storage/auth_records.py::_utc_now` 与 `runtime/chat/projection/projection_writer.py::_utc_now`，它们返回 `datetime` 对象。

脚本最后列出的文件中，手工删除不再使用的 `datetime`、`timezone`、`UTC` 导入（`uv run --no-sync ruff check --select F401 <文件>` 只看这三个名字）。不要对这些文件运行 `--fix`，其中 `storage/database.py`、`skills/registry.py` 带有再导出。

### 12. W6c：`_record_run_event` 改为公开名 `record_run_event`

快照中 `src.runtime.chat.trace_recorder._record_run_event` 被 10 个模块（含引擎外的 `subagents/manager.py`、`runtime/governance.py`、`memory/trace.py`）与 3 个测试文件使用。仓库中没有已存在的 `record_run_event`，可以直接整体替换：

```bash
grep -rlw "_record_run_event" src test | xargs perl -pi -e 's/\b_record_run_event\b/record_run_event/g'
grep -rn "_record_run_event" src test   # 必须无输出
```

`test/test_import_boundaries.py` 的 ALLOWLIST 中引用该名字的字符串会被同一命令改写，无须手工处理。不保留旧名别名。

### 13. W7a：为两个事件分发热点提交金标测试（规则表方案不再实施）

`observe_source_event`（复杂度 134）与 `TeamEventNormalizerMixin._team_event`（102）是 SourceEvent → RuntimeEvent / TEAM_DOMAIN_EVENT → DomainEvent 的翻译层。原方案把它们改写成有序规则表以降低圈复杂度；**现在放弃这一步**：规则表仍是按字符串匹配事件名的同一层翻译，而后继变更 `unify-chat-runtime-events` 会把这两段代码连同它们所属的翻译层整体替换为单一的 `RunEvent` 类型，规则表的工作量会被全部丢弃。两个热点保留 `# noqa: C901 - 待 unify-chat-runtime-events 删除`，不计入 W7 要消掉的豁免。

保留并提前提交的是**金标测试**：它们记录"同一事件序列 → 同一观察记录 / 同一 Domain Event 序列"，是后继变更证明"事件统一后行为未变"的回归基线，也是 W4 搬迁后验证"没搬坏"的证据之一。`define-runtime-contracts` 提供的 AgentScope fixture → RuntimeFrame 金标覆盖管线末端，这两个金标覆盖管线中段，三者合起来把整条翻译链钉住。

两个金标测试已在沙箱快照上验证：连续运行 3 次结果一致，覆盖了全部分支（观察测试产出 10 条观察记录，Team 测试每类事件都有输出）。

`backend/test/test_observation_rules_golden.py`：

```python
"""observe_source_event 金标测试：同一事件序列写出的观察记录必须与金标完全一致（供 W4 搬迁与后继事件统一回归使用）。

重构前生成金标（只运行一次并提交 test/golden/observation_rules.json）：
    UPDATE_GOLDEN=1 uv run pytest test/test_observation_rules_golden.py
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from src.runtime.chat.observation_runtime import close_run_root, observe_source_event, open_run_root
from src.storage.database import list_trace_observations

GOLDEN = Path(__file__).parent / "golden" / "observation_rules.json"
RUN_ID = "run-golden"
SESSION_ID = "sess-golden"
# 覆盖 observe_source_event 的每个分支，包括两处贯穿分支：
# REPLY_END/HITL_REQUIRED/CLARIFICATION_REQUIRED/TOOL_RESULT_START 与 TEAM_DOMAIN_EVENT。
EVENTS: list[tuple[str, dict[str, Any]]] = [
    ("MODEL_CALL_START", {"model_name": "deepseek"}),
    ("THINKING_BLOCK_DELTA", {"delta": "想一想"}),
    ("TEXT_BLOCK_DELTA", {"delta": "先读"}),
    ("REASONING_DELTA", {"delta": "推理"}),
    ("MODEL_CALL_END", {"model_name": "deepseek", "observationText": "先读 README", "finished_reason": "tool_calls"}),
    ("TOOL_CALL_START", {"tool_call_id": "c1", "tool_call_name": "Read"}),
    ("TOOL_CALL_END", {"tool_call_id": "c1", "tool_call_name": "Read", "input": {"path": "README.md"}}),
    ("TOOL_RESULT_START", {"tool_call_id": "c1"}),
    ("TOOL_RESULT_TEXT_DELTA", {"tool_call_id": "c1", "delta": "# 文"}),
    ("TOOL_RESULT_END", {"tool_call_id": "c1", "output": "# 文档", "state": "completed"}),
    ("SKILL_CONTEXT", {"skills": [{"id": "skill-a", "name": "周报"}]}),
    ("SKILL_END", {"skills": [{"id": "skill-a", "name": "周报"}], "status": "completed"}),
    ("MODEL_CALL_START", {"model_name": "deepseek"}),
    ("HITL_REQUIRED", {"interactionId": "hitl-1", "tool_call_name": "Bash", "input": {"command": "ls"}}),
    ("HITL_RESOLVED", {"interactionId": "hitl-1", "action": "approve"}),
    ("CLARIFICATION_REQUIRED", {"interactionId": "clar-1", "text": "要哪一周？"}),
    ("HITL_RESOLVED", {"interactionId": "clar-1", "action": "answer"}),
    ("SUBAGENT_QUEUED", {"id": "sub-1", "name": "检索员", "taskId": "t1"}),
    ("SUBAGENT_STARTED", {"id": "sub-1", "name": "检索员", "taskId": "t1"}),
    ("SUBAGENT_COMPLETED", {"id": "sub-1", "name": "检索员", "result": "完成"}),
    ("SUBAGENT_FAILED", {"id": "sub-2", "name": "写手", "error": "超时"}),
    ("TEAM_DOMAIN_EVENT", {"type": "task.created", "taskId": "t2", "payload": {"task": {"id": "t2", "title": "整理"}}}),
    ("AGENT_FINAL_ANSWER_STARTED", {}),
    ("FINAL_ANSWER_DELTA", {"delta": "完成了"}),
    ("REPLY_END", {}),
    ("UNKNOWN_EVENT", {"x": 1}),
]
_VOLATILE_KEY = re.compile(r"(At|Ms|_at|_ms)$")
_ID_KEYS = {"id", "parentId", "parent_id", "observationId"}


def _normalize(value: Any, ids: dict[str, str]) -> Any:
    """去掉时间与耗时字段，把观察 ID 按出现顺序替换为 obs-1、obs-2……"""

    if isinstance(value, dict):
        result = {}
        for key in sorted(value):
            if _VOLATILE_KEY.search(key):
                continue
            item = value[key]
            if key in _ID_KEYS and isinstance(item, str) and item:
                item = ids.setdefault(item, f"obs-{len(ids) + 1}")
            result[key] = _normalize(item, ids)
        return result
    if isinstance(value, list):
        return [_normalize(item, ids) for item in value]
    return value


def _snapshot() -> list[dict[str, Any]]:
    open_run_root(persist=True, run_id=RUN_ID, session_id=SESSION_ID, task="金标", agent_name="助手")
    frames = []
    for event_type, data in EVENTS:
        observe_source_event(persist=True, run_id=RUN_ID, session_id=SESSION_ID, event_type=event_type, data=data)
        frames.append({"after": event_type, "observations": list_trace_observations(RUN_ID)})
    close_run_root(persist=True, run_id=RUN_ID, outcome="completed", output={"answer": "完成了"})
    frames.append({"after": "close_run_root", "observations": list_trace_observations(RUN_ID)})
    return _normalize(frames, {})


def test_observation_rules_match_golden() -> None:
    actual = _snapshot()
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected
```

`backend/test/test_team_event_rules_golden.py`：

```python
"""TeamEventNormalizerMixin._team_event 金标测试：改为规则表前后，同一 Team outbox 序列产出的领域事件必须一致。

重构前生成金标（只运行一次并提交 test/golden/team_event_rules.json）：
    UPDATE_GOLDEN=1 uv run pytest test/test_team_event_rules_golden.py
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from src.runtime.chat.projection.domain_events import DomainEventRepository
from src.runtime.chat.projection.source_normalizer import ChatProjectionNormalizer

GOLDEN = Path(__file__).parent / "golden" / "team_event_rules.json"
TEAM = "team-golden"
# 覆盖 _team_event 的每个分支；第一条 member.spawned 在块开始前到达，用来覆盖"块未开始时忽略"。
OUTBOX: list[dict[str, Any]] = [
    {"type": "member.spawned", "teamRunId": TEAM, "actorId": "m0"},
    {"type": "run.created", "teamRunId": TEAM, "actorId": "leader", "status": "running",
     "payload": {"run": {"title": "金标团队"}, "leader": {"id": "leader", "name": "组长"}}},
    {"type": "member.spawned", "teamRunId": TEAM, "actorId": "m1", "payload": {"member": {"id": "m1", "name": "检索员"}}},
    {"type": "member.status.changed", "teamRunId": TEAM, "actorId": "m1", "status": "working"},
    {"type": "attempt.started", "teamRunId": TEAM, "actorId": "m1", "taskId": "t1", "payload": {"attempt": {"id": "a1"}}},
    {"type": "task.created", "teamRunId": TEAM, "taskId": "t1", "payload": {"task": {"id": "t1", "title": "检索资料"}}},
    {"type": "task.claimed", "teamRunId": TEAM, "actorId": "m1", "taskId": "t1"},
    {"type": "member.thinking.delta", "teamRunId": TEAM, "actorId": "m1", "taskId": "t1", "payload": {"delta": "想"}},
    {"type": "member.output.delta", "teamRunId": TEAM, "actorId": "m1", "taskId": "t1", "payload": {"delta": "找到 3 篇"}},
    {"type": "member.tool.started", "teamRunId": TEAM, "actorId": "m1", "taskId": "t1",
     "payload": {"toolCallId": "tc1", "name": "WebSearch", "input": {"q": "周报"}}},
    {"type": "member.tool.completed", "teamRunId": TEAM, "actorId": "m1", "taskId": "t1",
     "payload": {"toolCallId": "tc1", "name": "WebSearch", "output": "ok"}},
    {"type": "member.permission.required", "teamRunId": TEAM, "actorId": "m1", "payload": {"interactionId": "p1"}},
    {"type": "member.permission.resolved", "teamRunId": TEAM, "actorId": "m1", "payload": {"interactionId": "p1"}},
    {"type": "member.iteration.recovery", "teamRunId": TEAM, "actorId": "m1", "taskId": "t1"},
    {"type": "mail.sent", "teamRunId": TEAM, "actorId": "m1",
     "payload": {"mail": {"id": "mail-1", "to": "leader", "body": "完成"}}},
    {"type": "mail.read", "teamRunId": TEAM, "actorId": "leader", "payload": {"mail": {"id": "mail-1"}}},
    {"type": "task.retry.scheduled", "teamRunId": TEAM, "taskId": "t1"},
    {"type": "task.unlocked", "teamRunId": TEAM, "taskId": "t1"},
    {"type": "task.completed", "teamRunId": TEAM, "actorId": "m1", "taskId": "t1", "status": "completed"},
    {"type": "run.phase.changed", "teamRunId": TEAM, "phase": "review"},
    {"type": "run.completed", "teamRunId": TEAM, "status": "completed"},
    {"type": "unknown.type", "teamRunId": TEAM},
]
_VOLATILE_KEY = re.compile(r"(At|Ms|_at|_ms|timestamp)$")
_RANDOM_ID = re.compile(r"^domain-[0-9a-f]{32}$")


def _normalize(value: Any, ids: dict[str, str]) -> Any:
    """去掉时间字段，把随机领域事件 ID 按出现顺序替换为 event-1、event-2……"""

    if isinstance(value, dict):
        return {key: _normalize(value[key], ids) for key in sorted(value) if not _VOLATILE_KEY.search(key)}
    if isinstance(value, list):
        return [_normalize(item, ids) for item in value]
    if isinstance(value, str) and _RANDOM_ID.match(value):
        return ids.setdefault(value, f"event-{len(ids) + 1}")
    return value


def _snapshot() -> list[dict[str, Any]]:
    events = DomainEventRepository()
    events.ensure_stream("stream-team-golden", "run-team-golden")
    normalizer = ChatProjectionNormalizer(
        stream_key="stream-team-golden",
        run_id="run-team-golden",
        session_id="session-team-golden",
        events=events,
    )
    normalizer.start(agent_name="组长")
    frames = []
    for item in OUTBOX:
        produced = normalizer.ingest("TEAM_DOMAIN_EVENT", item)
        frames.append({"after": item["type"], "events": [event.model_dump(mode="json") for event in produced]})
    return _normalize(frames, {})


def test_team_event_rules_match_golden() -> None:
    actual = _snapshot()
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected
```

备选：仍按原方案改写为规则表。不采用：理由见上；降低的是复杂度指标，不是副本数量。

### 14. W7b：其余 4 个热点按阶段拆分

> 过渡态说明：本节对 `stream_leader_agent_reply` 的拆分用 `stop / skip_event / break_loop` 标志复刻原控制流，拆出的阶段函数仍共享同一个 `_LeaderStream` 上下文。它降低的是单函数复杂度，不是功能之间的耦合；HITL、Plan、Skill、Subagent、恢复仍通过同一个上下文对象互相影响。终态（loop 只做驱动与状态机，其余能力作为事件订阅者）由 `unify-chat-runtime-events` 实现，本节只要求"只搬不改、测试失败集合不变"。

通用规则：拆出的函数复杂度都不超过 15；只移动代码，不改写条件或顺序；每拆完一个函数都跑一遍相关测试与 Decision 2 的全量对比。原函数中的 `return`、`continue`、`break` 在抽出的函数里用上下文对象上的标志表示，由原函数检查：

```python
@dataclass(slots=True)
class _LeaderStream:
    # 原函数的全部参数，以及在多个阶段之间共享的局部变量（pending_hitl_batches、pending_subagent_batches、event_type、event_data 等）
    ...
    stop: bool = False        # 原代码的 return
    skip_event: bool = False  # 原代码的 continue（每个事件开始时重置）
    break_loop: bool = False  # 原代码的 break
```

**`stream_leader_agent_reply`**（`src/runtime/chat/leader_stream.py`，快照复杂度 70，620 行）。它是异步生成器，阶段函数也写成异步生成器，原函数按顺序驱动：

```python
async def stream_leader_agent_reply(...原签名...) -> AsyncGenerator[str, None]:
    """消费 Leader AgentScope 事件流，并处理 HITL / clarification 早停。"""

    ctx = _LeaderStream(...原参数..., pending_hitl_batches=[], pending_subagent_batches=[])
    async for event in agent.reply_stream(user_msg):
        ctx.skip_event = False
        for phase in _LEADER_EVENT_PHASES:
            async for chunk in phase(ctx, event):
                yield chunk
            if ctx.stop:
                return
            if ctx.skip_event or ctx.break_loop:
                break
        if ctx.break_loop:
            break
    for finish in _LEADER_FINISH_PHASES:
        async for chunk in finish(ctx):
            yield chunk
        if ctx.stop:
            return
```

阶段划分（括号内为快照行号，以语句内容为准）：

- `_LEADER_EVENT_PHASES`（循环体）：`_prepare_event`（229–234，取 `event_type`/`event_data` 并写入 ctx）、`_on_reply_start`（235–249）、`_on_exceed_max_iters`（250–277）、`_on_plan_mode`（278–283）、`_record_event_side_effects`（284–401：运行时事件、缓存用量、统计、权限、工具调用、Skill 生命周期、产物；超过 15 时在 330 行与 361 行处再拆成三段）、`_collect_pending_batches`（403–409）、`_on_text_delta`（411–420）、`_on_reply_end`（422–469）、`_emit_source_event`（471–498）、`_on_plan_review_ready`（499–508）。
- `_LEADER_FINISH_PHASES`（循环后）：`_drain_after_stream`（510–512）、`_handle_pending_subagent_batches`（514–595）、`_handle_scheduled_hitl`（597–601）、`_handle_pending_hitl`（603–691）、`_handle_clarification`（693–762）、`_handle_subagent_completion_block`（764–804）、`_attempt_final_answer_recovery`（806–819）。

**`_run_worker_turn`**（`src/group_studio/worker_runtime.py`，复杂度 33）：拆为 `_prepare_worker_turn(...) -> _WorkerTurn | None`（423–540：校验成员、构造 spec、写 run 与 message、发事件、创建 agent，返回 `_WorkerTurn` 数据类，持有 `run_id`、`message_id`、`agent`、`session`、`body_parts`、`thinking_parts`、`outcome`、`run_status`、`error_text`）；`async def _stream_worker_turn(turn) -> None`（541–671 的 try 块，原样搬入，异常处理不变）；`_finish_worker_turn(turn) -> None`（673–719）。原函数只剩三步调用。

**`evaluate_job_feasibility`**（`src/scheduler/feasibility.py`，复杂度 23，纯函数）：抽出 `_non_job_role_result`（102–133，返回 `FeasibilityResult | None`）、`_required_layer_result`（136–165，同上）、`_claim_optional_capabilities`（167–193，返回 `claimed_optional, missing, blocked, claimed`）、`_write_capability_result`（203–213）、`_scope_and_preview`（215–241，返回 `scope, preview`）、`_downgrade_and_suggestions`（243–260）。返回 `None` 表示继续往下走。

**`get_or_create_agent`**（`src/agents/factory.py`，复杂度 24）：抽出 `_agent_from_run_snapshot`（514–536）、`_cached_agent_or_none`（缓存命中与失效判断，快照约 552–593）、`_hosted_sandbox_runtime`（约 610–657）、`_construct_agent`（约 661–713）、`_register_agent`（约 715–726）。参数原样透传，不改签名。

验证：`uv run --no-sync ruff check src --select C901` 的输出中不再出现这 4 个函数名，并删除它们定义行上的 `# noqa: C901` 注释。`observe_source_event` 与 `_team_event` 保留豁免，注释改为 `# noqa: C901 - 待 unify-chat-runtime-events 删除`（Decision 13）。

### 15. W8a：复杂度豁免与双键读取的只减不增检查

`backend/test/test_c901_exemption_ratchet.py` 在 W7 开始前加入，`MAX_EXEMPTIONS` 填当时 `grep -rn "noqa: C901" src | wc -l` 的结果（快照 29）；W7 每去掉一个豁免就下调。

```python
"""复杂度豁免只减不增：src/ 中 `# noqa: C901` 的数量等于记录的上限，指定热点不得豁免。"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
# 开工时填 `grep -rn "noqa: C901" src | wc -l` 的结果；每去掉一个豁免就下调。
MAX_EXEMPTIONS = 29
# observe_source_event / _team_event 由 unify-chat-runtime-events 整体替换，不在本表（Decision 13）
HOTSPOTS = (
    "stream_leader_agent_reply",
    "_run_worker_turn",
    "evaluate_job_feasibility",
    "get_or_create_agent",
)


def _exemptions() -> list[str]:
    found: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "noqa: C901" in line:
                found.append(f"src/{path.relative_to(SRC).as_posix()}:{number}: {line.strip()}")
    return found


def test_c901_exemptions_match_recorded_maximum() -> None:
    count = len(_exemptions())
    assert count <= MAX_EXEMPTIONS, f"C901 豁免有 {count} 个，超过上限 {MAX_EXEMPTIONS}；新函数请拆小，不要加豁免"
    assert count == MAX_EXEMPTIONS, f"C901 豁免已减少到 {count} 个，请把 MAX_EXEMPTIONS 下调为 {count}"


def test_hotspots_have_no_exemption() -> None:
    pattern = re.compile(r"def (" + "|".join(HOTSPOTS) + r")\(")
    left = [item for item in _exemptions() if pattern.search(item)]
    assert not left, "这些热点函数仍带 C901 豁免：\n" + "\n".join(left)
```

`test_hotspots_have_no_exemption` 在 W7 完成前会失败，所以它与 W7 的最后一个热点同一提交加入；`test_c901_exemptions_match_recorded_maximum` 在 W7 开始时单独加入。

`backend/test/test_dual_key_lookup_ratchet.py` 在 W8 加入（W4 之后，路径已稳定）。`BASELINE` 由 `uv run python $T/dual_key_audit.py --json` 生成后粘贴，快照为 31 个文件共 134 处。检测逻辑内嵌在测试里，不依赖 `openspec/changes/` 下的脚本，变更归档后仍可运行。

```python
"""双键兼容读取只减不增：每个文件的数量等于 BASELINE 中的记录，未记录的文件必须为 0。

新代码请在边界处用 Pydantic 字段别名一次性归一，不要再写 x.get("a_b") or x.get("aB")。
BASELINE 由 openspec/changes/restructure-code-layering/tools/dual_key_audit.py --json 生成；路径相对 backend/。
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
BASELINE: dict[str, int] = {
    # 粘贴 dual_key_audit.py --json 的输出，例如 "src/storage/chat_session_records.py": 26,
}


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def _key_of(node: ast.AST) -> str | None:
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    ):
        return node.args[0].value
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
        return node.slice.value
    return None


def _count(path: Path) -> int:
    total = 0
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            names = {key for key in (_key_of(value) for value in node.values) if key}
            if any("_" in name and _camel(name) in names for name in names):
                total += 1
    return total


def _actual() -> dict[str, int]:
    result: dict[str, int] = {}
    for path in sorted((BACKEND / "src").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        count = _count(path)
        if count:
            result[path.relative_to(BACKEND).as_posix()] = count
    return result


def test_dual_key_lookups_do_not_grow() -> None:
    actual = _actual()
    grown = {path: (BASELINE.get(path, 0), count) for path, count in actual.items() if count > BASELINE.get(path, 0)}
    assert not grown, "这些文件新增了双键读取（基线, 当前）；请在边界处用 Pydantic 别名归一：" + repr(grown)


def test_dual_key_baseline_is_tight() -> None:
    actual = _actual()
    shrunk = {path: (count, actual.get(path, 0)) for path, count in BASELINE.items() if actual.get(path, 0) < count}
    assert not shrunk, "这些文件的双键读取已减少（基线, 当前），请下调 BASELINE（为 0 时删除该条）：" + repr(shrunk)
```

该测试已在沙箱快照上用生成的基线验证通过，统计结果与脚本一致（31 个文件、134 处）。

### 16. W8b：未引用代码、空包与易混命名

新文件 `backend/test/test_unreferenced_code.py`（判定规则与 `tools/dead_code_audit.py` 相同，沙箱快照上两者都列出 69 个，测试耗时约 2 秒）：

```python
"""src/ 中不保留无人引用的顶层函数与类。

判断标准同 tools/dead_code_audit.py：名字在本文件只出现在定义处，且在 src/、test/、*.spec 的其他文件中都没有以完整单词出现。
带装饰器的定义、以双下划线开头的名字不检查。确需保留的（例如通过字符串反射调用）写进 ALLOWED，并注明调用方。
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
# 路径:名字 -> 保留原因（写明谁通过什么方式调用）。
ALLOWED: dict[str, str] = {}
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _unreferenced() -> list[str]:
    files = sorted(path for path in (BACKEND / "src").rglob("*.py") if "__pycache__" not in path.parts)
    others = [*(BACKEND / "test").rglob("*.py"), *BACKEND.glob("*.spec")]
    texts = {path: path.read_text(encoding="utf-8") for path in [*files, *others]}
    tokens = {path: Counter(_IDENTIFIER.findall(text)) for path, text in texts.items()}
    total: Counter[str] = Counter()
    for counter in tokens.values():
        total.update(counter)
    found: list[str] = []
    for path in files:
        for node in ast.parse(texts[path]).body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.decorator_list or node.name.startswith("__"):
                continue
            own = tokens[path][node.name]
            if own > 1 or total[node.name] > own:
                continue
            found.append(f"{path.relative_to(BACKEND).as_posix()}:{node.name}")
    return found


def test_no_unreferenced_top_level_code() -> None:
    leftovers = [item for item in _unreferenced() if item not in ALLOWED]
    assert not leftovers, "这些顶层定义无人引用，请删除或加入 ALLOWED 并注明调用方：\n" + "\n".join(leftovers)


def test_allowed_entries_are_still_needed() -> None:
    current = set(_unreferenced())
    stale = [item for item in ALLOWED if item not in current]
    assert not stale, "这些 ALLOWED 条目已被引用或已删除，请移出 ALLOWED：\n" + "\n".join(stale)
```

清理步骤：

1. 运行 `uv run --no-sync pytest test/test_unreferenced_code.py`，得到清单（快照 69–71 个，约 520 行）。
2. 对每一项在仓库根目录执行 `grep -rnw "<名字>" backend frontend/src electron --include=*.py --include=*.ts --include=*.tsx --include=*.js --include=*.cjs --include=*.json --include=*.spec`。只有定义处一行的就删除整个定义；在 `getattr(`、`importlib`、字符串配置、前端或 Electron 中出现的，写进 `ALLOWED` 并注明调用方。
3. 删除后，若有导入变成未使用，只对该文件执行 `ruff check --select F401 <文件>` 并手工删除对应名字。
4. 一次删除会让更多函数变成无人引用，重复 1–3 直到测试通过。

空包：`git rm -r src/model`（只含一个空的 `__init__.py`，全仓库无引用）。之后 `tools/layer_baseline.py` 不再报告"未归层的顶层模块"。

易混命名：`src/catalog/skills.py`（旧 catalog Skill API 的兼容投影）与 `src/skills/catalog.py`（Skill 文件目录）名字互为颠倒。把前者改名为 `src/catalog/skill_projection.py`：

```bash
git mv src/catalog/skills.py src/catalog/skill_projection.py
grep -rl "src\.catalog\.skills" src test | xargs perl -pi -e 's/\bsrc\.catalog\.skills\b/src.catalog.skill_projection/g'
grep -rn "from src.catalog import" src test | grep -w skills   # 若有，改为 from src.catalog import skill_projection 并更新使用处的别名
```

快照中引用 6 处。改名后重新生成分层基线（`src.storage.database -> src.catalog.skills` 若在基线中，会变成新名字，条数不变）。

### 17. W9a：前端统一 API 客户端

新文件 `frontend/src/api/client.ts`：

```ts
/**
 * 前端访问后端的唯一入口：解析桌面端后端地址、检查状态码、把失败响应转换为 ApiError。
 *
 * - apiFetch：需要原始 Response 时使用（SSE 流、文件下载、需要读响应头）。
 * - apiJson：响应体是 JSON。
 * - apiVoid：不关心响应体（DELETE、部分 POST）。
 * 网络异常（TypeError）与取消（AbortError）原样抛出，不包装。
 */

import { resolveApiUrl } from '../utils/apiUrl';

export const DEFAULT_ERROR_MESSAGE = '请求失败';

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly detail: unknown;

  constructor(status: number, code: string, message: string, detail: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

function errorParts(detail: unknown, fallback: string): { code: string; message: string } {
  if (typeof detail === 'string') {
    const text = detail.trim();
    return { code: text, message: text || fallback };
  }
  if (detail && typeof detail === 'object') {
    const record = detail as { code?: unknown; message?: unknown };
    const code = typeof record.code === 'string' ? record.code : '';
    const message = typeof record.message === 'string' && record.message.trim() ? record.message : fallback;
    return { code, message };
  }
  return { code: '', message: fallback };
}

/** 把非 2xx 响应转换为 ApiError；响应体不是 JSON 时 message 使用 fallback。 */
export async function toApiError(response: Response, fallback = DEFAULT_ERROR_MESSAGE): Promise<ApiError> {
  const payload = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  const detail = payload && typeof payload === 'object' ? payload.detail : undefined;
  const { code, message } = errorParts(detail, fallback);
  return new ApiError(response.status, code, message, detail);
}

export async function apiFetch(path: string, init?: RequestInit, fallback = DEFAULT_ERROR_MESSAGE): Promise<Response> {
  const response = await fetch(resolveApiUrl(path), init);
  if (!response.ok) {
    throw await toApiError(response, fallback);
  }
  return response;
}

export async function apiJson<T>(path: string, init?: RequestInit, fallback = DEFAULT_ERROR_MESSAGE): Promise<T> {
  const response = await apiFetch(path, init, fallback);
  return (await response.json()) as T;
}

export async function apiVoid(path: string, init?: RequestInit, fallback = DEFAULT_ERROR_MESSAGE): Promise<void> {
  await apiFetch(path, init, fallback);
}

/** 生成 JSON 请求的 init：补 Content-Type 并序列化 body，保留调用方的其他字段。 */
export function jsonInit(body: unknown, init: RequestInit = {}): RequestInit {
  return {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init.headers as Record<string, string> | undefined) },
    body: JSON.stringify(body),
  };
}
```

`frontend/src/utils/apiUrl.ts`：删除 `installDesktopFetchBridge` 及其内部的 `window.fetch` 改写；保留 `setDesktopApiOrigin`、`getDesktopApiOrigin`、`resolveApiUrl`。`resolveApiUrl` 的判断改为 `if (typeof window === 'undefined' || !window.electronAPI || !value.startsWith('/api')) return value;`（Vitest 默认在 Node 环境运行，没有 `window`）。文件头注释改为"所有请求都经过 `src/api/client.ts` 调用 `resolveApiUrl`，不再改写全局 fetch"。`frontend/src/main.tsx`：删除 `installDesktopFetchBridge` 的导入与调用，`setDesktopApiOrigin(...)` 保持不变。

调用方改写规则（按原代码形态逐条对应）：

| 原写法 | 新写法 |
|---|---|
| `const res = await fetch(url, init); if (!res.ok) throw new Error(...); return await res.json();` | `return apiJson<T>(url, init, '<原中文错误文案>');` |
| `...; if (!res.ok) throw ...;`（不读响应体） | `await apiVoid(url, init, '<原文案>');` |
| `fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(x) })` | `apiJson<T>(url, jsonInit(x, { method: 'POST' }))` |
| `if (res.status === 404) return null;`（先于 `!res.ok`） | `try { return await apiJson<T>(url); } catch (error) { if (error instanceof ApiError && error.status === 404) return null; throw error; }` |
| `if (!res.ok) throw new Error(\`HTTP ${res.status}\`)` | `apiJson`/`apiVoid` 不传 fallback；错误文案变为后端 detail 或"请求失败"。这是本变更唯一可见的文案变化 |
| SSE：`const res = await fetch(url, { signal }); const reader = res.body!.getReader();`（`hooks/useEventStream.ts`） | `const res = await apiFetch(url, { signal }); const reader = res.body!.getReader();`；原来对 `res.ok` 的判断删除，改为 `catch (error)` 中按 `error instanceof ApiError` 走原失败分支，`AbortError` 分支保持不变 |
| 下载：`const res = await fetch(url); const blob = await res.blob();` | `const blob = await (await apiFetch(url)).blob();` |
| 非 `/api` 开头的外部地址 | 同样改用 `apiFetch`（`resolveApiUrl` 对非 `/api` 路径原样返回），保证检查规则没有例外 |

三个现有错误辅助函数的去向：

- `features/group-studio/api.ts` 的 `readDetail(response, fallback)`：只在 `detail` 是字符串时调用 `friendlyGroupStudioError(detail, fallback)`，否则返回 fallback。替换为同文件内的

```ts
async function groupStudioJson<T>(path: string, init: RequestInit | undefined, fallback: string): Promise<T> {
  try {
    return await apiJson<T>(path, init, fallback);
  } catch (error) {
    if (error instanceof ApiError) {
      throw new Error(typeof error.detail === 'string' ? friendlyGroupStudioError(error.detail, fallback) : fallback);
    }
    throw error;
  }
}
```

  并把该文件 22 处 `fetch` 改为调用 `groupStudioJson`（无响应体的写同样结构的 `groupStudioVoid`），然后删除 `readDetail`。这样抛出的错误文案与现在逐字相同。
- `features/sessions/SessionScopePanel.tsx` 的 `readErrorCode(response)`：删除，使用处改为 `error instanceof ApiError ? error.code : ''`。
- `utils/feedback.ts` 的 `getErrorMessage(error)`：保留。`ApiError` 是 `Error` 子类，现有逻辑直接取到 `message`。

新文件 `frontend/src/api/client.test.ts`：

```ts
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, apiFetch, apiJson, apiVoid, jsonInit } from './client';
import { setDesktopApiOrigin } from '../utils/apiUrl';

function respond(status: number, body: unknown, contentType = 'application/json'): Response {
  const text = typeof body === 'string' ? body : JSON.stringify(body);
  return new Response(text, { status, headers: { 'Content-Type': contentType } });
}

afterEach(() => {
  vi.unstubAllGlobals();
  setDesktopApiOrigin('http://127.0.0.1:8765');
});

describe('apiJson', () => {
  it('返回 JSON 响应体', async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(200, [{ id: 'a' }]));
    vi.stubGlobal('fetch', fetchMock);
    await expect(apiJson<{ id: string }[]>('/api/agents')).resolves.toEqual([{ id: 'a' }]);
    expect(fetchMock).toHaveBeenCalledWith('/api/agents', undefined);
  });

  it('结构化 detail 转为 ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      respond(409, { detail: { code: 'directory_outside_roots', message: '目录不在允许范围' } }),
    ));
    const error = await apiJson('/api/x').catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, code: 'directory_outside_roots', message: '目录不在允许范围' });
  });

  it('字符串 detail 同时作为 code 与 message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond(400, { detail: 'approval_batch_incomplete' })));
    await expect(apiJson('/api/x')).rejects.toMatchObject({
      status: 400,
      code: 'approval_batch_incomplete',
      message: 'approval_batch_incomplete',
    });
  });

  it('非 JSON 错误体使用 fallback', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond(502, '<html>bad gateway</html>', 'text/html')));
    await expect(apiJson('/api/x', undefined, '服务暂不可用')).rejects.toMatchObject({
      status: 502,
      code: '',
      message: '服务暂不可用',
    });
  });

  it('网络异常原样抛出', async () => {
    const failure = new TypeError('Failed to fetch');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(failure));
    await expect(apiVoid('/api/x')).rejects.toBe(failure);
  });
});

describe('apiFetch', () => {
  it('Electron 下把 /api 请求转到桌面后端', async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(200, {}));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('window', { electronAPI: {} });
    setDesktopApiOrigin('http://127.0.0.1:9000/');
    await apiFetch('/api/agents');
    expect(fetchMock).toHaveBeenCalledWith('http://127.0.0.1:9000/api/agents', undefined);
  });

  it('浏览器开发模式保留相对路径', async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(200, {}));
    vi.stubGlobal('fetch', fetchMock);
    vi.stubGlobal('window', {});
    await apiFetch('/api/agents');
    expect(fetchMock).toHaveBeenCalledWith('/api/agents', undefined);
  });
});

describe('jsonInit', () => {
  it('补 Content-Type 并保留其他字段', () => {
    const controller = new AbortController();
    const init = jsonInit({ a: 1 }, { method: 'POST', signal: controller.signal, headers: { 'X-Trace': '1' } });
    expect(init).toMatchObject({
      method: 'POST',
      body: '{"a":1}',
      headers: { 'Content-Type': 'application/json', 'X-Trace': '1' },
    });
    expect(init.signal).toBe(controller.signal);
  });
});
```

新文件 `frontend/src/api/noRawFetch.test.ts`。用 Vite 的 `import.meta.glob` 读取源码文本，不导入 `node:fs`，因为 `tsconfig.app.json` 的 `types` 只有 `vite/client`，`tsc -b` 会检查 `src/` 下的测试文件：

```ts
import { describe, expect, it } from 'vitest';

// 由 Vite 在测试时内联全部源码文本；不依赖 node:fs，因此 tsconfig.app.json 无需 Node 类型。
const sources = import.meta.glob<string>(
  ['../**/*.ts', '../**/*.tsx', '!../**/*.test.ts', '!../**/*.test.tsx', '!../**/*.d.ts'],
  { query: '?raw', import: 'default', eager: true },
);
const ALLOWED = new Set(['api/client.ts']);
const RAW_FETCH = /(^|[^.\w$])fetch\s*\(|\b(?:window|globalThis|self)\.fetch\b/;

describe('后端请求入口', () => {
  it('业务代码不直接调用 fetch', () => {
    const offenders: string[] = [];
    for (const [key, text] of Object.entries(sources)) {
      // Vite 把同目录文件写成 ./x.ts，其余写成 ../dir/x.ts；统一成相对 src/ 的路径。
      const file = key.startsWith('./') ? `api/${key.slice(2)}` : key.replace(/^\.\.\//, '');
      if (ALLOWED.has(file)) {
        continue;
      }
      text.split('\n').forEach((line, index) => {
        const code = line.replace(/\/\/.*$/, '');
        if (RAW_FETCH.test(code)) {
          offenders.push(`${file}:${index + 1}: ${line.trim()}`);
        }
      });
    }
    expect(offenders).toEqual([]);
  });
});
```

以上三个文件已在沙箱用 Vitest 5.0.5 + Vite 8 + TypeScript 6.0.2 验证：9 个用例通过；放入一个含 `await fetch('/api/agents')` 的文件时检查失败，注释里的 `fetch(` 与 `refetch(` 不误报；严格模式下 `tsc` 无错误。

迁移顺序：先提交 `client.ts`、`client.test.ts`、`apiUrl.ts` 的 `typeof window` 判断；再按目录分批迁移（`features/group-studio` → `features/auth` → `features/sessions` → `hooks` → `pages` → 其余），每批跑 `npm --prefix frontend run build` 与 `npm --prefix frontend run test`；最后一批提交时加入 `noRawFetch.test.ts` 并删除 `installDesktopFetchBridge`。

### 18. W9b：拆分 `ChatPage.tsx`

`frontend/src/pages/ChatPage.tsx`（995 行）中与会话界面无关的三块搬出，目标是文件不超过 750 行。只移动代码，不改渲染结构。行号为快照值，以语句内容为准：

1. `frontend/src/features/chat/chatSessionApi.ts`：页面中 5 处 `fetch`（保存会话模型、读取会话列表、读取会话消息、读取会话运行、删除会话）各封装为一个导出函数，使用 Decision 17 的 `apiJson`/`apiVoid`。读取运行时原代码对 404 不报错（`!response.ok && response.status !== 404`），按 Decision 17 表中的 404 写法保留该行为。
2. `frontend/src/features/chat/useHistoryPanel.ts`：搬入 `HISTORY_PANEL_TRANSITION_MS`、`historyCollapsed`/`historyPanelMounted` 两个 state、`historyTransitionTimerRef`/`historyExpandFrameRef` 两个 ref、`handleCollapseHistory`、`handleExpandHistory` 以及卸载时清理定时器的 effect（快照 89–90、100–101、274–312 行）。返回 `{ historyCollapsed, historyPanelMounted, handleCollapseHistory, handleExpandHistory }`。
3. `frontend/src/features/chat/useSessionModelChoice.ts`：搬入 `modelChoices`/`savingModelChoice` 两个 state 与 `selectModel`（快照 71–72、185–218 行）。参数为 `selectModel` 中引用的外部变量（`activeSessionId` 等）。页面其余位置还会调用 `setModelChoices`（快照 489、610 行），所以返回 `{ modelChoices, setModelChoices, savingModelChoice, selectModel }`。

验证：`npm --prefix frontend run build`、`npm --prefix frontend run lint`、`npm --prefix frontend run test` 通过；`wc -l frontend/src/pages/ChatPage.tsx` 不超过 750；手工验证切换会话、删除会话、收起与展开历史栏、切换模型。

### 19. 搬迁后同步配置与类型基线

W1、W2、W3、W4、W5、W8（改名）都会移动文件。每次移动后在同一提交中完成：

1. `backend/.basedpyright/baseline.json` 以文件路径为键，搬迁后旧条目失效，搬过去的错误会被当成新增。执行 `uv run --no-sync basedpyright --writebaseline` 重写基线。门禁会检查条数不增长，只搬迁时条数应不变；如果增加，说明搬迁引入了新的类型错误，先修正再提交。
2. `backend/pyproject.toml` 的 `[tool.ruff.lint.per-file-ignores]` 若引用了被搬文件（例如 `src/server/routes/chat/runtime.py`），改为新路径。
3. 重新生成分层基线并下调 `MAX_ENTRIES`（Decision 1）。
4. `grep -rn "<旧路径>" --include=*.md --include=*.toml --include=*.yml --include=*.yaml --include=*.json . | grep -v openspec/changes` 检查文档与配置里的旧路径。

## Risks / Trade-offs

- [归档后的代码与快照不同，脚本输出的数字会变] → 本文件的每一步都先给出判定规则，再给出快照期望值。以重算结果为准；只有当重算结果与规则矛盾时才需要停下来讨论。
- [monkeypatch 字符串或延迟导入漏改，运行时才报错] → `move_package.py`/`move_symbols.py` 同时改写字符串；每个波次都用全量测试的失败集合对比（Decision 2），并执行 `uv run --no-sync python -c "import src.server.main"`。
- [未合并的分支在大范围移动后冲突] → 每个波次合并前通知在途分支；分支作者在变基前对自己的分支运行同一条脚本命令（脚本对已搬迁的路径不做任何事），再变基。
- [`ClosingConnection` 改变了连接生命周期，块外继续使用连接的代码会报 `ProgrammingError`] → 已核对快照中没有这种写法，全量测试已验证。归档后执行 `grep -rn "= \(self\.connect\|_connect\|database\._connect\)()" src` 与 `grep -rn "with conn:" src` 复核，应无输出。
- [删除 fetch 桥后，漏改的请求在 Electron 打包版失效（开发模式有 Vite 代理，发现不了）] → `noRawFetch.test.ts` 拦截源码中的裸 `fetch`；合并前用打包版手测登录、对话、群聊工作室、文件下载。
- [金标测试对易变字段（时间戳、随机 id）敏感而误报] → 金标生成时归一化 `At/Ms/_at/_ms/timestamp` 结尾的键与 `domain-<hex>` id；连续运行 3 次一致后才提交。
- [`_job_service` 改为延迟创建] → `@cache` 保证每个进程只有一个实例，与原来模块级实例等价；测试若 patch `_job_service`，改为 patch `agent_registry._job_service` 为返回替身的函数。
- [`fastapi.HTTPException` 随 `_require_http_url` 进入 `src/tools/`] → 接受。改异常类型会改变错误响应，超出本变更范围。
- [保留的 15 条向上导入仍然存在] → 全部是存储层或配置层的延迟导入，已写进基线且只减不增；由后继变更 `split-storage-by-aggregate` 清零。
- [AGENTS.md 与代码脱节] → 文档与搬迁在同一提交产生；`test/test_agents_md.py` 检查目录齐全与测试锚点存在；改动政策写进每份 AGENTS.md。

## Migration Plan

1. 前置：`simplify-global-session-context` 已归档；`fix-runtime-correctness-and-quality-gates` 阶段 B 已合并（`reexport_audit.py` 输出 0、`scripts/quality-gates.sh` 已存在、C901 豁免已标注）。
2. 按 Decision 2 的顺序逐波次提交，每个波次单独开 PR 或单独提交，合并前通过 `scripts/quality-gates.sh` 与失败集合对比。
3. W9 可以与后端波次并行开发，合并顺序放最后。
4. `define-runtime-contracts` 与本变更无文件冲突，应在本变更开工前或并行完成；W4 搬迁时以其对账测试和 fixture 金标作为额外验证。
5. 全部完成后：分层基线 15 条；`MAX_ENTRIES = 15`；`server is a leaf` 契约生效；`MAX_EXEMPTIONS` 比开工时少 4；前端无裸 `fetch`；`src/agents`、`src/tools`、`src/common`、`src/models`、`src/runtime/chat`、`src/server` 各有 AGENTS.md。

### Rollback

- 每个波次是独立提交，用 `git revert <提交>` 回滚。后续波次依赖前面波次的路径（W4 依赖 W1，W6/W7 依赖 W4），所以要按合并的反序回滚。
- `ClosingConnection` 是单点改动：如果上线后发现块外使用连接的问题，可以只去掉 `_connect()`/`control_connect()` 中的 `factory=ClosingConnection`，`transaction()` 等公开入口仍然可用。
- 前端 W9 与后端无耦合，可单独回滚。回滚后 `installDesktopFetchBridge` 恢复，旧代码继续可用。
- 本变更不改数据库结构与接口，回滚不需要数据迁移。

## Open Questions

无阻塞问题。以下事项已明确交给后继变更，不在本变更内决定：

- `src/storage/database.py` 按聚合拆分、表所有权、基线 15 → 0：`split-storage-by-aggregate`（本变更归档后开工，可与下一项并行）。
- SourceEvent / RuntimeEvent / DomainEvent 合并为 `RunEvent`、三份持久化收成一份、loop 变为驱动器 + 订阅者、删除 `observe_source_event` 与 `_team_event` 的 C901 豁免：`unify-chat-runtime-events`（W4 之后开工；其 design 需先决定 loop 由本仓库拥有还是交给 AgentScope ReActAgent + hooks）。
- `contracts/` JSON Schema、前后端对账、fixture 金标、AGENTS.md 按目录拆分：`define-runtime-contracts`（现在即可开工，不依赖 `simplify-global-session-context`）。
- W9 是否由前端同学并行完成（不影响设计）。
