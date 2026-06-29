# Plan Mode Middleware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于 AgentScope Middleware hooks 实现第一版 Plan Mode：计划阶段只读调查和写计划，审批后进入 Build 模式继续按计划执行。

**Architecture:** 不恢复旧 Plan Runtime 分支；Plan Mode 作为 ReAct Runtime 的 middleware 增强能力接入。`on_system_prompt` 注入 PLAN/BUILD 模式提示，`on_acting` 动态拦截变更型工具，`plan_enter / plan_write / plan_exit` 工具维护计划状态并通过 Runtime Event 暴露给前端。

**Tech Stack:** Python 3.11, FastAPI, AgentScope 2.0 `MiddlewareBase`, SQLite, Pydantic/dataclass, React 19/TypeScript Runtime Event 展示。

---

## File Structure

- Create: `backend/src/server/routes/chat/plan_mode/__init__.py`
  - 导出 Plan Mode 的 manager、middleware 和工具工厂。
- Create: `backend/src/server/routes/chat/plan_mode/models.py`
  - 定义 `PlanModeContext`、状态常量和工具返回 payload 类型。
- Create: `backend/src/server/routes/chat/plan_mode/policy.py`
  - 定义 Plan Mode 下允许执行的工具名、Bash 只读命令判定、HTTP 只读判定。
- Create: `backend/src/server/routes/chat/plan_mode/manager.py`
  - 负责创建/更新 plan record，生成 plan 文件路径，维护每个 session/run 的 Plan Mode 状态。
- Create: `backend/src/server/routes/chat/plan_mode/events.py`
  - 封装 `PLAN_MODE_ENTERED`、`PLAN_UPDATED`、`PLAN_WAITING_APPROVAL`、`PLAN_APPROVED`、`PLAN_DENIED` 事件 payload。
- Create: `backend/src/server/routes/chat/plan_mode/tools.py`
  - 实现 `PlanEnterTool`、`PlanWriteTool`、`PlanExitTool`，风格对齐 `backend/src/tools/final_answer.py`。
- Create: `backend/src/server/routes/chat/plan_mode/middleware.py`
  - 实现 `PlanModeMiddleware(MiddlewareBase)`，使用 `on_system_prompt` 和 `on_acting`。
- Modify: `backend/src/agents/factory.py`
  - `get_or_create_agent()` 支持 `middlewares` 和 `extra_tools` 一起传入 Agent；缓存 key 由调用方负责包含 plan mode 标识。
- Modify: `backend/src/server/routes/chat/runtime_builder.py`
  - `MainAgentRuntimeSpec` 增加 `extra_tools`、`middlewares`、`runtime_features_cache_key`，构造主 Agent 时传入。
- Modify: `backend/src/server/routes/chat/react_runtime.py`
  - 在单 Agent 和 Team Leader 创建前初始化 Plan Mode manager/middleware/tools，注入 spec。
- Modify: `backend/src/server/routes/chat/event_types.py`
  - 增加 Plan Mode Runtime Event 类型。
- Modify: `frontend/src/types/chatEvents.ts`
  - 增加同名 Plan Mode Runtime Event 类型。
- Modify: `frontend/src/features/chat/ProcessDetailsBlock.tsx`
  - 在“已处理”区域展示 Plan Mode 当前状态和 plan markdown 摘要。
- Modify: `docs/chat-sse-event-contract.md`
  - 先更新稳定事件契约，再实现事件发送。
- Test: `backend/test/test_plan_mode_policy.py`
  - 覆盖工具允许/拒绝策略。
- Test: `backend/test/test_plan_mode_tools.py`
  - 覆盖 plan_enter/write/exit 对 manager 和 plan record 的影响。
- Test: `backend/test/test_plan_mode_middleware.py`
  - 覆盖 `on_system_prompt` 和 `on_acting` 的允许/拒绝行为。
- Test: `backend/test/test_chat_plan_mode_integration.py`
  - 覆盖 Runtime 注入 middleware/tools/cache key，不跑真实模型。

---

### Task 1: Define Plan Mode Event Contract

**Files:**
- Modify: `docs/chat-sse-event-contract.md`
- Modify: `backend/src/server/routes/chat/event_types.py`
- Modify: `frontend/src/types/chatEvents.ts`

- [ ] **Step 1: Update contract document first**

Add a “Plan Mode Runtime Events” section to `docs/chat-sse-event-contract.md`:

```md
## Plan Mode Runtime Events

Plan Mode 基于 AgentScope Middleware 实现，不恢复旧 Plan Runtime。所有事件都通过 `RUNTIME_EVENT` 发送，`scope` 使用 `plan`。

| type | status | payload |
| --- | --- | --- |
| `PLAN_MODE_ENTERED` | `running` | `{ planId, planFile, mode }` |
| `PLAN_UPDATED` | `running` | `{ planId, planFile, markdown, source }` |
| `PLAN_WAITING_APPROVAL` | `waiting` | `{ planId, planFile, markdown }` |
| `PLAN_APPROVED` | `completed` | `{ planId, planFile }` |
| `PLAN_DENIED` | `failed` | `{ planId, planFile, reason }` |
| `PLAN_TOOL_DENIED` | `completed` | `{ planId, toolCallId, toolName, message }` |
```

- [ ] **Step 2: Add backend event literals**

Modify `RuntimeEventType` in `backend/src/server/routes/chat/event_types.py`:

```python
    "PLAN_MODE_ENTERED",
    "PLAN_UPDATED",
    "PLAN_WAITING_APPROVAL",
    "PLAN_APPROVED",
    "PLAN_DENIED",
    "PLAN_TOOL_DENIED",
```

- [ ] **Step 3: Add frontend event constants**

Modify `RUNTIME_EVENT_TYPES` in `frontend/src/types/chatEvents.ts`:

```ts
  PLAN_MODE_ENTERED: 'PLAN_MODE_ENTERED',
  PLAN_UPDATED: 'PLAN_UPDATED',
  PLAN_WAITING_APPROVAL: 'PLAN_WAITING_APPROVAL',
  PLAN_APPROVED: 'PLAN_APPROVED',
  PLAN_DENIED: 'PLAN_DENIED',
  PLAN_TOOL_DENIED: 'PLAN_TOOL_DENIED',
```

- [ ] **Step 4: Verify type-only changes**

Run:

```bash
cd frontend && npm run build
```

Expected: TypeScript build succeeds or fails only on pre-existing unrelated errors. If it fails on the new constants, fix the constant object syntax.

---

### Task 2: Add Plan Mode Policy

**Files:**
- Create: `backend/src/server/routes/chat/plan_mode/__init__.py`
- Create: `backend/src/server/routes/chat/plan_mode/policy.py`
- Test: `backend/test/test_plan_mode_policy.py`

- [ ] **Step 1: Write failing policy tests**

Create `backend/test/test_plan_mode_policy.py`:

```python
from src.server.routes.chat.plan_mode.policy import (
    PLAN_CONTROL_TOOL_NAMES,
    is_plan_mode_tool_allowed,
)


def test_plan_control_tools_are_allowed() -> None:
    assert "plan_enter" in PLAN_CONTROL_TOOL_NAMES
    assert is_plan_mode_tool_allowed("plan_enter", "{}")
    assert is_plan_mode_tool_allowed("plan_write", "{}")
    assert is_plan_mode_tool_allowed("plan_exit", "{}")


def test_builtin_read_tools_are_allowed() -> None:
    assert is_plan_mode_tool_allowed("Read", '{"path": "README.md"}')
    assert is_plan_mode_tool_allowed("Grep", '{"pattern": "Plan"}')
    assert is_plan_mode_tool_allowed("Glob", '{"pattern": "*.py"}')


def test_write_tools_are_denied() -> None:
    assert not is_plan_mode_tool_allowed("Write", '{"path": "x", "content": "y"}')
    assert not is_plan_mode_tool_allowed("Edit", '{"path": "x", "old_str": "a", "new_str": "b"}')


def test_bash_read_only_commands_are_allowed() -> None:
    assert is_plan_mode_tool_allowed("Bash", '{"command": "pwd"}')
    assert is_plan_mode_tool_allowed("Bash", '{"command": "rg -n plan backend/src"}')
    assert is_plan_mode_tool_allowed("Bash", '{"command": "git status --short"}')
    assert is_plan_mode_tool_allowed("Bash", '{"command": "git diff -- backend/src/server/routes/chat"}')


def test_bash_mutating_commands_are_denied() -> None:
    assert not is_plan_mode_tool_allowed("Bash", '{"command": "rm -rf build"}')
    assert not is_plan_mode_tool_allowed("Bash", '{"command": "git add backend/src"}')
    assert not is_plan_mode_tool_allowed("Bash", '{"command": "npm install"}')
    assert not is_plan_mode_tool_allowed("Bash", '{"command": "python script.py > out.txt"}')


def test_http_get_and_head_are_allowed_but_mutations_denied() -> None:
    assert is_plan_mode_tool_allowed("HttpRequest", '{"url": "https://example.com", "method": "GET"}')
    assert is_plan_mode_tool_allowed("HttpRequest", '{"url": "https://example.com", "method": "HEAD"}')
    assert not is_plan_mode_tool_allowed("HttpRequest", '{"url": "https://example.com", "method": "POST"}')
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
cd backend && uv run pytest test/test_plan_mode_policy.py -q
```

Expected: FAIL because `src.server.routes.chat.plan_mode.policy` does not exist.

- [ ] **Step 3: Implement package and policy**

Create `backend/src/server/routes/chat/plan_mode/__init__.py`:

```python
"""Plan Mode 基于 AgentScope Middleware 的运行时增强。"""
```

Create `backend/src/server/routes/chat/plan_mode/policy.py`:

```python
"""Plan Mode 工具准入策略。

Plan Mode 只允许只读调查工具和计划控制工具执行；变更型工具由 middleware
合成 DENIED 工具结果，避免真实修改外部状态。
"""

import json
import shlex
from typing import Any


PLAN_CONTROL_TOOL_NAMES = frozenset({
    "plan_enter",
    "plan_write",
    "plan_exit",
    "todo_write",
    "FinalAnswerStart",
})

READ_ONLY_TOOL_NAMES = frozenset({
    "Read",
    "Grep",
    "Glob",
    "KnowledgeBaseSearch",
})

BASH_READ_ONLY_COMMANDS = frozenset({
    "cat",
    "find",
    "git",
    "grep",
    "head",
    "ls",
    "pwd",
    "rg",
    "sed",
    "tail",
    "wc",
})

GIT_READ_ONLY_SUBCOMMANDS = frozenset({
    "branch",
    "diff",
    "log",
    "show",
    "status",
})

BASH_MUTATION_TOKENS = frozenset({
    ">",
    ">>",
    "<",
    "|",
    "&&",
    "||",
    ";",
    "rm",
    "mv",
    "cp",
    "touch",
    "mkdir",
    "chmod",
    "chown",
    "git add",
    "git commit",
    "git push",
    "git merge",
    "git switch",
    "git checkout",
    "npm install",
    "pnpm install",
    "yarn install",
    "pip install",
    "uv add",
})


def is_plan_mode_tool_allowed(tool_name: str | None, raw_input: str | dict[str, Any] | None) -> bool:
    """判断 Plan Mode 下某个工具调用是否允许真实执行。"""

    if not tool_name:
        return False
    if tool_name in PLAN_CONTROL_TOOL_NAMES or tool_name in READ_ONLY_TOOL_NAMES:
        return True
    parsed_input = _parse_input(raw_input)
    if tool_name == "Bash":
        return _is_read_only_bash(str(parsed_input.get("command") or ""))
    if tool_name == "HttpRequest":
        return str(parsed_input.get("method") or "GET").upper() in {"GET", "HEAD"}
    return False


def _parse_input(raw_input: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(raw_input, dict):
        return raw_input
    if not raw_input:
        return {}
    try:
        value = json.loads(raw_input)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _is_read_only_bash(command: str) -> bool:
    compact = " ".join(command.strip().split())
    if not compact:
        return False
    lowered = compact.lower()
    if any(token in lowered for token in BASH_MUTATION_TOKENS):
        return False
    try:
        parts = shlex.split(compact)
    except ValueError:
        return False
    if not parts:
        return False
    executable = parts[0]
    if executable not in BASH_READ_ONLY_COMMANDS:
        return False
    if executable == "git":
        return len(parts) >= 2 and parts[1] in GIT_READ_ONLY_SUBCOMMANDS
    return True
```

- [ ] **Step 4: Run policy tests**

Run:

```bash
cd backend && uv run pytest test/test_plan_mode_policy.py -q
```

Expected: PASS.

---

### Task 3: Add Plan Mode Manager

**Files:**
- Create: `backend/src/server/routes/chat/plan_mode/models.py`
- Create: `backend/src/server/routes/chat/plan_mode/manager.py`
- Test: `backend/test/test_plan_mode_tools.py`

- [ ] **Step 1: Write failing manager/tool state tests**

Create `backend/test/test_plan_mode_tools.py`:

```python
from src.server.routes.chat.plan_mode.manager import PlanModeManager


def test_manager_enters_updates_and_exits_plan_mode() -> None:
    manager = PlanModeManager(
        session_id="session-1",
        run_id="run-1",
        agent_id="agent-1",
        agent_name="Agent",
        persist=False,
    )

    entered = manager.enter(goal="实现 Plan Mode", title="Plan Mode")
    assert entered.plan_active is True
    assert entered.plan_id
    assert entered.plan_file.endswith(".md")

    updated = manager.write(markdown="# Plan\n\n- [ ] Step 1", source="test")
    assert updated.markdown == "# Plan\n\n- [ ] Step 1"
    assert manager.context.current_plan_file == entered.plan_file

    waiting = manager.exit_for_approval()
    assert waiting.plan_active is False
    assert waiting.waiting_approval is True
    assert waiting.approved is False
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
cd backend && uv run pytest test/test_plan_mode_tools.py -q
```

Expected: FAIL because manager/models do not exist.

- [ ] **Step 3: Implement models**

Create `backend/src/server/routes/chat/plan_mode/models.py`:

```python
"""Plan Mode 状态模型。"""

from dataclasses import dataclass


PLAN_STATUS_DRAFT = "draft"
PLAN_STATUS_WAITING_APPROVAL = "waiting_approval"
PLAN_STATUS_APPROVED = "approved"
PLAN_STATUS_DENIED = "denied"


@dataclass
class PlanModeContext:
    """单次 Chat run 内的 Plan Mode 状态。"""

    session_id: str
    run_id: str
    agent_id: str
    agent_name: str
    plan_id: str = ""
    plan_active: bool = False
    waiting_approval: bool = False
    approved: bool = False
    current_plan_file: str = ""
    markdown: str = ""
```

- [ ] **Step 4: Implement manager**

Create `backend/src/server/routes/chat/plan_mode/manager.py`:

```python
"""Plan Mode 状态协调器。"""

import os
import uuid
from datetime import datetime, timezone

from src.server.routes.chat.plan_mode.models import (
    PLAN_STATUS_DRAFT,
    PLAN_STATUS_WAITING_APPROVAL,
    PlanModeContext,
)


class PlanModeManager:
    """管理一次 Chat run 的 Plan Mode 状态和持久化。"""

    def __init__(
        self,
        *,
        session_id: str,
        run_id: str,
        agent_id: str,
        agent_name: str,
        team_id: str | None = None,
        persist: bool = True,
    ) -> None:
        self.team_id = team_id
        self.persist = persist
        self.context = PlanModeContext(
            session_id=session_id,
            run_id=run_id,
            agent_id=agent_id,
            agent_name=agent_name,
        )

    def is_plan_active(self) -> bool:
        """返回当前是否处于只读计划阶段。"""

        return self.context.plan_active

    def plan_file_path(self) -> str:
        """返回当前计划文件路径。"""

        if self.context.current_plan_file:
            return self.context.current_plan_file
        return self._make_plan_file_path(self.context.plan_id or uuid.uuid4().hex)

    def enter(self, *, goal: str, title: str = "Runtime Plan") -> PlanModeContext:
        """进入 Plan Mode 并创建 plan 记录。"""

        plan_id = self.context.plan_id or f"plan-{uuid.uuid4().hex}"
        plan_file = self._make_plan_file_path(plan_id)
        markdown = self.context.markdown or f"# {title}\n\n## Goal\n{goal}\n"
        self.context.plan_id = plan_id
        self.context.plan_active = True
        self.context.waiting_approval = False
        self.context.current_plan_file = plan_file
        self.context.markdown = markdown
        if self.persist:
            self._create_plan_record(title=title, goal=goal, markdown=markdown)
        return self.context

    def write(self, *, markdown: str, source: str) -> PlanModeContext:
        """更新计划 markdown。"""

        self.context.markdown = markdown
        if self.persist and self.context.plan_id:
            from src.storage.database import append_plan_event_record, update_plan_record

            update_plan_record(self.context.plan_id, markdown=markdown)
            append_plan_event_record(
                self.context.plan_id,
                step_id=None,
                event_type="PLAN_UPDATED",
                content=f"source={source}",
            )
        return self.context

    def exit_for_approval(self) -> PlanModeContext:
        """退出只读计划阶段并等待用户审批。"""

        self.context.plan_active = False
        self.context.waiting_approval = True
        if self.persist and self.context.plan_id:
            from src.storage.database import append_plan_event_record, update_plan_record

            update_plan_record(self.context.plan_id, status=PLAN_STATUS_WAITING_APPROVAL)
            append_plan_event_record(
                self.context.plan_id,
                step_id=None,
                event_type="PLAN_WAITING_APPROVAL",
                content="Plan mode exited and waits for approval.",
            )
        return self.context

    def _create_plan_record(self, *, title: str, goal: str, markdown: str) -> None:
        from src.storage.database import create_plan_record

        now = datetime.now(timezone.utc).isoformat()
        create_plan_record(
            {
                "id": self.context.plan_id,
                "runId": self.context.run_id,
                "sessionId": self.context.session_id,
                "agentId": self.context.agent_id,
                "teamId": self.team_id,
                "title": title,
                "goal": goal,
                "status": PLAN_STATUS_DRAFT,
                "markdown": markdown,
                "currentStepId": None,
                "createdAt": now,
                "updatedAt": now,
                "completedAt": None,
            },
            [],
        )

    def _make_plan_file_path(self, plan_id: str) -> str:
        safe_session = self.context.session_id.replace("/", "-")
        return os.path.join(".agent-workstation", "plans", safe_session, f"{plan_id}.md")
```

- [ ] **Step 5: Run manager tests**

Run:

```bash
cd backend && uv run pytest test/test_plan_mode_tools.py -q
```

Expected: PASS.

---

### Task 4: Add Plan Control Tools

**Files:**
- Modify: `backend/src/server/routes/chat/plan_mode/tools.py`
- Test: `backend/test/test_plan_mode_tools.py`

- [ ] **Step 1: Extend failing tests for tools**

Append to `backend/test/test_plan_mode_tools.py`:

```python
import pytest

from src.server.routes.chat.plan_mode.tools import PlanEnterTool, PlanExitTool, PlanWriteTool


@pytest.mark.asyncio
async def test_plan_tools_update_manager_context() -> None:
    manager = PlanModeManager(
        session_id="session-2",
        run_id="run-2",
        agent_id="agent-2",
        agent_name="Agent",
        persist=False,
    )

    enter = await PlanEnterTool(manager)(goal="调查并制定计划", title="调查计划")
    assert enter.state.value == "success"
    assert manager.context.plan_active is True

    write = await PlanWriteTool(manager)(markdown="# 调查计划\n\n- [ ] 读取代码")
    assert write.state.value == "success"
    assert "读取代码" in manager.context.markdown

    exit_result = await PlanExitTool(manager)()
    assert exit_result.state.value == "success"
    assert manager.context.waiting_approval is True
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
cd backend && uv run pytest test/test_plan_mode_tools.py -q
```

Expected: FAIL because tools do not exist.

- [ ] **Step 3: Implement tools**

Create `backend/src/server/routes/chat/plan_mode/tools.py`:

```python
"""Plan Mode 控制工具。"""

import json
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionContext, PermissionDecision
from agentscope.tool import ToolBase, ToolChunk

from src.server.routes.chat.plan_mode.manager import PlanModeManager


class _PlanToolBase(ToolBase):
    """Plan Mode 工具基类，统一权限为允许。"""

    is_mcp: bool = False
    is_read_only: bool = True
    is_concurrency_safe: bool = False
    is_external_tool: bool = False
    is_state_injected: bool = False

    def __init__(self, manager: PlanModeManager) -> None:
        self.manager = manager

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} is controlled by Plan Mode middleware.",
            decision_reason="Plan Mode control tool",
        )

    def _success(self, payload: dict[str, Any]) -> ToolChunk:
        return ToolChunk(
            content=[TextBlock(text=json.dumps(payload, ensure_ascii=False, indent=2))],
            state=ToolResultState.SUCCESS,
            is_last=True,
        )


class PlanEnterTool(_PlanToolBase):
    """进入只读计划模式。"""

    name: str = "plan_enter"
    description: str = "Enter PLAN mode before investigation and planning."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "User goal for the plan."},
            "title": {"type": "string", "description": "Short plan title."},
        },
        "required": ["goal"],
    }

    async def __call__(self, goal: str, title: str = "Runtime Plan") -> ToolChunk:
        context = self.manager.enter(goal=goal, title=title)
        return self._success({
            "planId": context.plan_id,
            "planFile": context.current_plan_file,
            "planActive": context.plan_active,
        })


class PlanWriteTool(_PlanToolBase):
    """写入当前计划 markdown。"""

    name: str = "plan_write"
    description: str = "Write or replace the current PLAN mode markdown plan."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "markdown": {"type": "string", "description": "Complete plan markdown."},
        },
        "required": ["markdown"],
    }

    async def __call__(self, markdown: str) -> ToolChunk:
        context = self.manager.write(markdown=markdown, source="tool")
        return self._success({
            "planId": context.plan_id,
            "planFile": context.current_plan_file,
            "markdown": context.markdown,
        })


class PlanExitTool(_PlanToolBase):
    """退出计划模式并等待用户审批。"""

    name: str = "plan_exit"
    description: str = "Exit PLAN mode and ask the user to approve the written plan."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def __call__(self) -> ToolChunk:
        context = self.manager.exit_for_approval()
        return self._success({
            "planId": context.plan_id,
            "planFile": context.current_plan_file,
            "waitingApproval": context.waiting_approval,
            "markdown": context.markdown,
        })


def build_plan_mode_tools(manager: PlanModeManager) -> list[ToolBase]:
    """构造 Plan Mode 控制工具。"""

    return [
        PlanEnterTool(manager),
        PlanWriteTool(manager),
        PlanExitTool(manager),
    ]
```

- [ ] **Step 4: Run tool tests**

Run:

```bash
cd backend && uv run pytest test/test_plan_mode_tools.py -q
```

Expected: PASS.

---

### Task 5: Add Plan Mode Middleware

**Files:**
- Create: `backend/src/server/routes/chat/plan_mode/middleware.py`
- Test: `backend/test/test_plan_mode_middleware.py`

- [ ] **Step 1: Write failing middleware tests**

Create `backend/test/test_plan_mode_middleware.py`:

```python
from types import SimpleNamespace

import pytest
from agentscope.message import ToolResultState

from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.plan_mode.middleware import PlanModeMiddleware


class DummyToolCall:
    id = "tool-1"
    name = "Write"
    input = '{"path": "x", "content": "y"}'


@pytest.mark.asyncio
async def test_system_prompt_contains_plan_banner_when_active() -> None:
    manager = PlanModeManager(
        session_id="session-1",
        run_id="run-1",
        agent_id="agent-1",
        agent_name="Agent",
        persist=False,
    )
    manager.enter(goal="制定计划")
    middleware = PlanModeMiddleware(manager)

    prompt = await middleware.on_system_prompt(SimpleNamespace(name="Agent"), "base")

    assert "PLAN MODE is active" in prompt
    assert manager.context.current_plan_file in prompt


@pytest.mark.asyncio
async def test_system_prompt_contains_build_hint_after_approval() -> None:
    manager = PlanModeManager(
        session_id="session-1",
        run_id="run-1",
        agent_id="agent-1",
        agent_name="Agent",
        persist=False,
    )
    manager.enter(goal="制定计划")
    manager.context.plan_active = False
    manager.context.approved = True
    middleware = PlanModeMiddleware(manager)

    prompt = await middleware.on_system_prompt(SimpleNamespace(name="Agent"), "base")

    assert "BUILD mode" in prompt
    assert manager.context.current_plan_file in prompt


@pytest.mark.asyncio
async def test_on_acting_denies_mutating_tool() -> None:
    manager = PlanModeManager(
        session_id="session-1",
        run_id="run-1",
        agent_id="agent-1",
        agent_name="Agent",
        persist=False,
    )
    manager.enter(goal="制定计划")
    middleware = PlanModeMiddleware(manager)
    called_next = False

    async def next_handler(**kwargs):
        nonlocal called_next
        called_next = True
        yield "unexpected"

    chunks = [
        item
        async for item in middleware.on_acting(
            SimpleNamespace(name="Agent"),
            {"tool_call": DummyToolCall()},
            next_handler,
        )
    ]

    assert called_next is False
    assert chunks[-1].state == ToolResultState.DENIED
    assert "PLAN mode" in chunks[-1].content[0].text
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
cd backend && uv run pytest test/test_plan_mode_middleware.py -q
```

Expected: FAIL because middleware does not exist.

- [ ] **Step 3: Implement middleware**

Create `backend/src/server/routes/chat/plan_mode/middleware.py`:

```python
"""Plan Mode AgentScope Middleware。"""

from typing import Any, AsyncGenerator

from agentscope.message import TextBlock, ToolResultState
from agentscope.middleware import MiddlewareBase
from agentscope.tool import ToolResponse

from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.plan_mode.policy import is_plan_mode_tool_allowed


DENY_MESSAGE = (
    "Blocked: you are in PLAN mode (read-only). You may investigate and run "
    "read-only tools, record your plan with plan_write, and call plan_exit "
    "when ready to request approval. Do not modify files or run mutating "
    "commands until the plan is approved."
)

PLAN_BANNER_TEMPLATE = """

<system-reminder>
PLAN MODE is active (read-only). Plan file: {plan_file}
Investigate the problem and draft a concrete plan, but do NOT modify files,
run mutating commands, or otherwise change state. Record the plan with
plan_write. When the plan is complete, call plan_exit to ask the user for
approval. If you lack required information, ask one specific clarifying
question instead of inventing details.
</system-reminder>
"""

BUILD_MODE_HINT_TEMPLATE = """

<system-reminder>
BUILD mode is active. The approved plan exists at {plan_file}. Read or use it
as the execution guide, then execute the task step by step until complete.
Do not stop after merely producing the plan.
</system-reminder>
"""


class PlanModeMiddleware(MiddlewareBase):
    """通过 AgentScope hooks 实现 Plan/Build 动态模式。"""

    def __init__(self, manager: PlanModeManager) -> None:
        self.manager = manager

    async def on_system_prompt(self, agent: object, current_prompt: str) -> str:
        """按当前 Plan Mode 状态注入系统提示。"""

        base = current_prompt or ""
        if self.manager.context.plan_active:
            return base + PLAN_BANNER_TEMPLATE.format(
                plan_file=self.manager.plan_file_path(),
            )
        if self.manager.context.current_plan_file and self.manager.context.approved:
            return base + BUILD_MODE_HINT_TEMPLATE.format(
                plan_file=self.manager.context.current_plan_file,
            )
        return base

    async def on_acting(
        self,
        agent: object,
        input_kwargs: dict[str, Any],
        next_handler: object,
    ) -> AsyncGenerator[object, None]:
        """Plan Mode 下拒绝变更型工具调用。"""

        tool_call = input_kwargs.get("tool_call")
        if not self.manager.is_plan_active() or tool_call is None:
            async for item in next_handler(**input_kwargs):
                yield item
            return

        tool_name = str(getattr(tool_call, "name", "") or "")
        raw_input = getattr(tool_call, "input", None)
        if is_plan_mode_tool_allowed(tool_name, raw_input):
            async for item in next_handler(**input_kwargs):
                yield item
            return

        yield ToolResponse(
            id=str(getattr(tool_call, "id", "") or ""),
            content=[TextBlock(text=DENY_MESSAGE)],
            state=ToolResultState.DENIED,
        )
```

- [ ] **Step 4: Run middleware tests**

Run:

```bash
cd backend && uv run pytest test/test_plan_mode_middleware.py -q
```

Expected: PASS.

---

### Task 6: Inject Middleware And Tools Into Agent Factory

**Files:**
- Modify: `backend/src/agents/factory.py`
- Modify: `backend/src/server/routes/chat/runtime_builder.py`
- Test: `backend/test/test_chat_plan_mode_integration.py`

- [ ] **Step 1: Write failing integration tests for spec fields**

Create `backend/test/test_chat_plan_mode_integration.py`:

```python
from src.agents.runtime_types import AgentGuardrails, AgentModelOptions, RuntimeToolScope
from src.server.routes.chat.runtime_builder import RuntimeModelConfig, build_main_agent_runtime_spec


def test_plan_mode_cache_key_can_be_included_in_runtime_agent_id() -> None:
    spec = build_main_agent_runtime_spec(
        agent_id="agent-1",
        agent_name="Agent",
        session_id="session-1",
        system_prompt="prompt",
        model_config=RuntimeModelConfig(
            model_name="model-a",
            fallback_names=[],
            model_options=AgentModelOptions(),
        ),
        deep_thinking=False,
        used_skills=[],
        guardrails=AgentGuardrails(),
        mcp_servers=[],
        knowledge_tools=[],
        runtime_tool_scope=RuntimeToolScope(),
        guardrail_cache_key="guard",
        runtime_features_cache_key="plan-mode-v1",
        extra_tools=[],
        middlewares=[],
    )

    assert "features-plan-mode-v1" in spec.runtime_agent_id
    assert spec.extra_tools == []
    assert spec.middlewares == []
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
cd backend && uv run pytest test/test_chat_plan_mode_integration.py -q
```

Expected: FAIL because spec does not accept the new fields.

- [ ] **Step 3: Modify factory signature**

In `backend/src/agents/factory.py`, add imports and parameters:

```python
from agentscope.middleware import MiddlewareBase
```

Add to `get_or_create_agent()` parameters:

```python
    middlewares: list[MiddlewareBase] | None = None,
```

Pass into `Agent(...)`:

```python
        middlewares=middlewares or [],
```

- [ ] **Step 4: Modify runtime builder dataclass and cache key**

In `backend/src/server/routes/chat/runtime_builder.py`, import:

```python
from agentscope.middleware import MiddlewareBase
from agentscope.tool import ToolBase
```

Extend `MainAgentRuntimeSpec`:

```python
    extra_tools: list[ToolBase]
    middlewares: list[MiddlewareBase]
```

Add `runtime_features_cache_key` to `build_runtime_agent_cache_key()` and include it:

```python
    runtime_features_cache_key: str = "none",
```

Return string includes:

```python
        f"features-{runtime_features_cache_key}:"
```

Add optional parameters to `build_main_agent_runtime_spec()`:

```python
    runtime_features_cache_key: str = "none",
    extra_tools: list[ToolBase] | None = None,
    middlewares: list[MiddlewareBase] | None = None,
```

Set dataclass fields:

```python
        extra_tools=extra_tools or [],
        middlewares=middlewares or [],
```

Pass to factory in `create_main_agent_from_spec()`:

```python
        extra_tools=spec.extra_tools,
        middlewares=spec.middlewares,
```

- [ ] **Step 5: Run integration test**

Run:

```bash
cd backend && uv run pytest test/test_chat_plan_mode_integration.py -q
```

Expected: PASS.

---

### Task 7: Wire Plan Mode Into React Runtime

**Files:**
- Modify: `backend/src/server/routes/chat/react_runtime.py`
- Modify: `backend/src/server/routes/chat/runtime_builder.py`
- Test: `backend/test/test_chat_plan_mode_integration.py`

- [ ] **Step 1: Add a runtime wiring test**

Append to `backend/test/test_chat_plan_mode_integration.py`:

```python
from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.plan_mode.middleware import PlanModeMiddleware
from src.server.routes.chat.plan_mode.tools import build_plan_mode_tools


def test_plan_mode_tools_and_middleware_are_constructed_together() -> None:
    manager = PlanModeManager(
        session_id="session-1",
        run_id="run-1",
        agent_id="agent-1",
        agent_name="Agent",
        persist=False,
    )
    tools = build_plan_mode_tools(manager)
    middleware = PlanModeMiddleware(manager)

    assert [tool.name for tool in tools] == ["plan_enter", "plan_write", "plan_exit"]
    assert middleware.manager is manager
```

- [ ] **Step 2: Run test**

Run:

```bash
cd backend && uv run pytest test/test_chat_plan_mode_integration.py -q
```

Expected: PASS after previous tasks.

- [ ] **Step 3: Add helper in `react_runtime.py`**

Add imports:

```python
from src.server.routes.chat.background_runs import current_chat_background_run_id
from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.plan_mode.middleware import PlanModeMiddleware
from src.server.routes.chat.plan_mode.tools import build_plan_mode_tools
```

Add helper:

```python
def _build_plan_mode_runtime_parts(ctx: ChatRunContext, run_data: ChatMessage) -> tuple[str, list[object], list[object]]:
    """构造 Plan Mode middleware 和控制工具。"""

    manager = PlanModeManager(
        session_id=ctx.session_id,
        run_id=current_chat_background_run_id() or "",
        agent_id=ctx.agent_id,
        agent_name=ctx.agent_name,
        team_id=run_data.team_id,
        persist=ctx.persist_run,
    )
    tools = build_plan_mode_tools(manager)
    middlewares = [PlanModeMiddleware(manager)]
    return "plan-mode-v1", tools, middlewares
```

- [ ] **Step 4: Inject helper result when building main spec**

Before `build_main_agent_runtime_spec(...)` in `execute_react_runtime()`, add:

```python
        runtime_features_cache_key, extra_tools, middlewares = _build_plan_mode_runtime_parts(ctx, run_data)
```

Then pass:

```python
            runtime_features_cache_key=runtime_features_cache_key,
            extra_tools=extra_tools,
            middlewares=middlewares,
```

This makes Plan Mode tools available to every main Agent. Plan Mode is inactive until the model calls `plan_enter`; a later task can add automatic entry heuristics if desired.

- [ ] **Step 5: Run focused backend tests**

Run:

```bash
cd backend && uv run pytest test/test_plan_mode_policy.py test/test_plan_mode_tools.py test/test_plan_mode_middleware.py test/test_chat_plan_mode_integration.py -q
```

Expected: PASS.

---

### Task 8: Emit Plan Runtime Events From Tools And Middleware

**Files:**
- Modify: `backend/src/server/routes/chat/plan_mode/events.py`
- Modify: `backend/src/server/routes/chat/plan_mode/tools.py`
- Modify: `backend/src/server/routes/chat/plan_mode/middleware.py`
- Modify: `backend/src/server/routes/chat/leader_stream.py`
- Test: `backend/test/test_plan_mode_tools.py`

- [ ] **Step 1: Add event sink to manager**

Modify `PlanModeManager.__init__()`:

```python
        self.runtime_events: list[dict] = []
```

Add method:

```python
    def drain_runtime_events(self) -> list[dict]:
        """取出已生成的 Plan Mode Runtime Event。"""

        events = list(self.runtime_events)
        self.runtime_events.clear()
        return events
```

- [ ] **Step 2: Implement event builders**

Create `backend/src/server/routes/chat/plan_mode/events.py`:

```python
"""Plan Mode Runtime Event 构造函数。"""

from typing import Any

from src.server.routes.chat.runtime_events import build_runtime_event
from src.server.routes.chat.plan_mode.models import PlanModeContext


def build_plan_event(
    event_type: str,
    *,
    context: PlanModeContext,
    status: str,
    reason: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 Plan Mode Runtime Event。"""

    return build_runtime_event(
        event_type,
        session_id=context.session_id,
        run_id=context.run_id,
        scope="plan",
        source="runtime",
        agent_id=context.agent_id,
        agent_name=context.agent_name,
        status=status,
        reason=reason,
        payload={
            "planId": context.plan_id,
            "planFile": context.current_plan_file,
            **(payload or {}),
        },
    )
```

- [ ] **Step 3: Push events from manager actions**

In `manager.py`, import `build_plan_event` and append:

```python
        self.runtime_events.append(build_plan_event(
            "PLAN_MODE_ENTERED",
            context=self.context,
            status="running",
            payload={"mode": "plan"},
        ))
```

In `write()`:

```python
        self.runtime_events.append(build_plan_event(
            "PLAN_UPDATED",
            context=self.context,
            status="running",
            payload={"markdown": markdown, "source": source},
        ))
```

In `exit_for_approval()`:

```python
        self.runtime_events.append(build_plan_event(
            "PLAN_WAITING_APPROVAL",
            context=self.context,
            status="waiting",
            payload={"markdown": self.context.markdown},
        ))
```

- [ ] **Step 4: Emit drained events in leader stream**

AgentScope middleware itself cannot yield `RUNTIME_EVENT` strings; it returns ToolResponse chunks. The simplest V1 path is to drain events after every AgentScope event in `stream_leader_agent_reply()`.

Add optional parameter:

```python
    plan_mode_manager: object | None = None,
```

Inside the event loop, after `event_type = ...`, add:

```python
        if plan_mode_manager is not None:
            for runtime_event in plan_mode_manager.drain_runtime_events():
                if state.runtime_events is not None:
                    state.runtime_events.append(runtime_event)
                yield _sse_event("RUNTIME_EVENT", runtime_event)
```

Update the call in `react_runtime.py`:

```python
            plan_mode_manager=plan_mode_manager,
```

Keep the local variable returned from `_build_plan_mode_runtime_parts()`:

```python
        plan_mode_manager, runtime_features_cache_key, extra_tools, middlewares = ...
```

- [ ] **Step 5: Add tests for event generation**

Append to `backend/test/test_plan_mode_tools.py`:

```python
def test_manager_drains_plan_runtime_events() -> None:
    manager = PlanModeManager(
        session_id="session-3",
        run_id="run-3",
        agent_id="agent-3",
        agent_name="Agent",
        persist=False,
    )
    manager.enter(goal="制定计划")
    events = manager.drain_runtime_events()

    assert [event["type"] for event in events] == ["PLAN_MODE_ENTERED"]
    assert manager.drain_runtime_events() == []
```

- [ ] **Step 6: Run tests**

Run:

```bash
cd backend && uv run pytest test/test_plan_mode_tools.py test/test_plan_mode_middleware.py test/test_chat_plan_mode_integration.py -q
```

Expected: PASS.

---

### Task 9: Frontend Process Details Display

**Files:**
- Modify: `frontend/src/features/chat/ProcessDetailsBlock.tsx`
- Test: `cd frontend && npm run build`

- [ ] **Step 1: Add Plan Mode grouping helper**

In `ProcessDetailsBlock.tsx`, add a helper near existing runtime event grouping helpers:

```ts
function getPlanModeEvents(runtimeEvents: ChatRuntimeEvent[]): ChatRuntimeEvent[] {
  return runtimeEvents.filter((event) => (
    event.type === RUNTIME_EVENT_TYPES.PLAN_MODE_ENTERED ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_UPDATED ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_WAITING_APPROVAL ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_APPROVED ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_DENIED ||
    event.type === RUNTIME_EVENT_TYPES.PLAN_TOOL_DENIED
  ));
}
```

- [ ] **Step 2: Add a compact Plan Mode block**

Add a component in the same file:

```tsx
function PlanModeRuntimeView({ events }: { events: ChatRuntimeEvent[] }) {
  if (events.length === 0) {
    return null;
  }
  const latest = events[events.length - 1];
  const markdown = typeof latest.payload.markdown === 'string' ? latest.payload.markdown : '';
  const planFile = typeof latest.payload.planFile === 'string' ? latest.payload.planFile : '';

  return (
    <div className="process-detail-section">
      <div className="process-detail-section-title">Plan Mode</div>
      <div className="process-detail-muted">{latest.type}</div>
      {planFile ? <div className="process-detail-code">{planFile}</div> : null}
      {markdown ? <pre className="process-detail-pre">{markdown}</pre> : null}
    </div>
  );
}
```

Use existing CSS class names if `process-detail-section`, `process-detail-muted`, `process-detail-code`, or `process-detail-pre` already exist. If they do not exist, reuse the closest local class in this file instead of adding a new visual system.

- [ ] **Step 3: Render before tool details**

Where runtime details are rendered, compute:

```ts
const planModeEvents = getPlanModeEvents(runtimeEvents);
```

Render:

```tsx
<PlanModeRuntimeView events={planModeEvents} />
```

Place it before tool call details so the user sees the plan before tool execution traces.

- [ ] **Step 4: Build frontend**

Run:

```bash
cd frontend && npm run build
```

Expected: PASS.

---

### Task 10: Full Verification

**Files:**
- No new files; verify all changes.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
cd backend && uv run pytest test/test_plan_mode_policy.py test/test_plan_mode_tools.py test/test_plan_mode_middleware.py test/test_chat_plan_mode_integration.py -q
```

Expected: PASS.

- [ ] **Step 2: Run full backend test suite**

Run:

```bash
cd backend && uv run pytest
```

Expected: PASS.

- [ ] **Step 3: Run frontend build**

Run:

```bash
cd frontend && npm run build
```

Expected: PASS.

- [ ] **Step 4: Run git whitespace check**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 5: Manual smoke test**

Start the app:

```bash
./scripts/dev.sh
```

Use Chat with a prompt like:

```text
请先进入 plan mode，读取项目结构并写一个修改 Chat 事件展示的计划，暂时不要修改文件。
```

Expected:

- Agent can call read-only tools.
- `Write` / `Edit` / mutating `Bash` calls are denied while plan is active.
- `plan_write` updates Plan Mode block in Chat process details.
- `plan_exit` emits waiting approval state.
- Assistant does not claim a plan exists before calling `plan_write`.

---

## Deliberate V1 Non-Goals

- 不恢复旧 `runtime.kind=plan` 分支。
- 不实现 Team Leader Plan + Worker Plan 的多级计划。
- 不实现用户可编辑计划后 resume。
- 不实现 strict step-by-step guard。
- 不把 plan markdown 写入真实工作区文件；V1 先以 SQLite plan record 和 Runtime Event payload 为真相，`planFile` 是稳定逻辑路径。

## Self-Review

- Spec coverage: 覆盖 middleware hook、计划工具、工具拦截、Agent 注入、事件契约、前端展示和验证。
- Placeholder scan: 本计划没有未定义占位项；所有新增文件都有职责、测试和最小代码样例。
- Type consistency: `PlanModeManager`、`PlanModeMiddleware`、`build_plan_mode_tools`、`runtime_features_cache_key` 在后续任务中使用的名称与定义一致。
