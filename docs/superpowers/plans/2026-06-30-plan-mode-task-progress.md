# Plan Mode Task Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 AgentScope Middleware Plan Mode 上补齐结构化任务列表、任务输出和前端实时进度展示，让审批后的 Build Mode 真正按计划执行并可观测。

**Architecture:** 不恢复旧 Plan Runtime；继续把 Plan Mode 作为 ReAct Runtime 的 middleware/hooks 增强。复用现有 `plans / plan_steps / plan_events` 数据结构，新增 `todo_write / task_list / task_output` 工具维护结构化步骤，并通过 `RUNTIME_EVENT` 发送 `PLAN_TASKS_UPDATED / PLAN_TASK_OUTPUT` 给前端。

**Tech Stack:** Python 3.11, FastAPI, AgentScope 2.0 `MiddlewareBase` / `ToolBase`, SQLite, Pydantic/dataclass, React 19/TypeScript, Vite.

---

## Design

### Current State

- `plan_enter / plan_write / plan_exit` 已经可用，Plan 阶段只读调查、写 markdown、等待审批。
- 用户批准后，前端带 `approvedPlanId` 发起新一轮 `/api/chat/send`，后端在 ReAct Runtime 加载 approved plan，并通过 `PlanModeMiddleware.on_system_prompt` 注入 Build Mode 提示。
- SQLite 已有 `plans`、`plan_steps`、`plan_events`，Pydantic 已有 `ChatPlanStep` / `ChatPlanRun`，但当前 manager 只维护 markdown，不维护结构化 steps。
- 前端 `runtimeEvents` 可以接收任意稳定 Runtime Event，但 `ProcessDetailsBlock` 目前只展示 markdown 和审批按钮。

### Target Behavior

1. Plan 阶段 Agent 调用 `todo_write` 写入结构化步骤，步骤落库到 `plan_steps`，前端立即显示任务列表。
2. `plan_exit` 后仍显示 markdown 审批，同时保留结构化任务列表，用户可以更清楚地批准什么。
3. Build Mode 中 Agent 可调用 `task_list` 读取当前 steps，并调用 `task_output` 更新每一步的 `status / evidence / result`。
4. 前端流式展示每个 task 的状态和输出摘要；刷新历史会从 runtime events 和 session synthetic plan message 中恢复审批入口与计划内容。
5. 新事件都走 `RUNTIME_EVENT`，`scope="plan"`，避免混入旧 Plan Runtime 设计。

### Runtime Events

| type | status | payload |
| --- | --- | --- |
| `PLAN_TASKS_UPDATED` | `running` | `{ planId, planFile, steps }` |
| `PLAN_TASK_OUTPUT` | `running|completed|failed` | `{ planId, planFile, step, steps }` |

`steps` 使用现有 `ChatPlanStep` camelCase 字段：`id`、`planId`、`stepIndex`、`title`、`description`、`status`、`assignedAgentId`、`evidence`、`result`、`startedAt`、`completedAt`、`updatedAt`。

### Tool Interfaces

`todo_write`

```json
{
  "items": [
    {
      "title": "定位 Plan Mode 工具注册点",
      "description": "阅读 plan_mode/tools.py 和 react_runtime.py，确认工具注入链路。",
      "status": "pending"
    }
  ]
}
```

`task_list`

```json
{}
```

`task_output`

```json
{
  "stepId": "plan-step-...",
  "stepIndex": 0,
  "status": "completed",
  "evidence": "已确认 build mode prompt 注入 approved plan markdown。",
  "result": "无需新增 Runtime 分支。"
}
```

`task_output` 支持 `stepId` 或 `stepIndex` 二选一。合法状态固定为 `pending | running | completed | failed | skipped`；`running` 自动设置 `startedAt`，`completed / failed / skipped` 自动设置 `completedAt`。

### File Structure

- Modify: `docs/chat-sse-event-contract.md`
  - 新增 `PLAN_TASKS_UPDATED` / `PLAN_TASK_OUTPUT` 稳定事件。
- Modify: `backend/src/server/routes/chat/event_types.py`
  - 注册新 Runtime Event 类型。
- Modify: `backend/src/storage/database.py`
  - 新增 `replace_plan_steps_record(plan_id, steps)`，复用现有 `plan_steps` 表。
- Modify: `backend/src/server/routes/chat/plan_mode/models.py`
  - 增加步骤状态常量和合法状态集合。
- Modify: `backend/src/server/routes/chat/plan_mode/manager.py`
  - 增加 `write_tasks()`、`list_tasks()`、`record_task_output()`。
- Modify: `backend/src/server/routes/chat/plan_mode/tools.py`
  - 增加 `TodoWriteTool`、`TaskListTool`、`TaskOutputTool`，并纳入 `build_plan_mode_tools()`。
- Modify: `backend/src/server/routes/chat/plan_mode/policy.py`
  - 将 `task_list`、`task_output` 纳入 Plan 控制工具；保留 `todo_write`。
- Modify: `backend/src/server/routes/chat/plan_mode/__init__.py`
  - 导出新工具。
- Modify: `backend/src/server/routes/chat/leader_stream.py`
  - 扩展工具结果兜底映射，覆盖任务级事件。
- Modify: `backend/src/server/routes/chat/handlers/resume.py`
  - 继续复用 `leader_stream._plan_runtime_event_from_tool_result()`，确保 HITL resume 后任务事件实时发送。
- Modify: `frontend/src/types/chatEvents.ts`
  - 增加任务事件常量。
- Modify: `frontend/src/features/chat/ProcessDetailsBlock.tsx`
  - Plan 面板展示 steps、状态 Tag、输出摘要。
- Test: `backend/test/test_plan_mode_tools.py`
  - 覆盖 manager/tools 的结构化步骤行为。
- Test: `backend/test/test_chat_runtime_regressions.py`
  - 覆盖工具结果兜底生成 `PLAN_TASKS_UPDATED / PLAN_TASK_OUTPUT`。

---

## Task 1: Contract and Data Structure

**Files:**
- Modify: `docs/chat-sse-event-contract.md`
- Modify: `backend/src/server/routes/chat/event_types.py`
- Modify: `frontend/src/types/chatEvents.ts`
- Modify: `backend/src/storage/database.py`
- Modify: `backend/src/server/routes/chat/plan_mode/models.py`

- [ ] **Step 1: Update SSE contract first**

Add these rows under “Plan Mode Runtime Events” in `docs/chat-sse-event-contract.md`:

```md
| `PLAN_TASKS_UPDATED` | `running` | `{ planId, planFile, steps }` |
| `PLAN_TASK_OUTPUT` | `running|completed|failed` | `{ planId, planFile, step, steps }` |
```

Add a short note after the table:

```md
`steps` 使用现有 `ChatPlanStep` camelCase 字段：`id`、`planId`、`stepIndex`、`title`、`description`、`status`、`assignedAgentId`、`evidence`、`result`、`startedAt`、`completedAt`、`updatedAt`。
```

- [ ] **Step 2: Add event constants**

Add backend literals in `backend/src/server/routes/chat/event_types.py`:

```python
    "PLAN_TASKS_UPDATED",
    "PLAN_TASK_OUTPUT",
```

Add frontend constants in `frontend/src/types/chatEvents.ts`:

```ts
  PLAN_TASKS_UPDATED: 'PLAN_TASKS_UPDATED',
  PLAN_TASK_OUTPUT: 'PLAN_TASK_OUTPUT',
```

- [ ] **Step 3: Add plan step status constants**

In `backend/src/server/routes/chat/plan_mode/models.py`, add:

```python
PLAN_STEP_STATUS_PENDING = "pending"
PLAN_STEP_STATUS_RUNNING = "running"
PLAN_STEP_STATUS_COMPLETED = "completed"
PLAN_STEP_STATUS_FAILED = "failed"
PLAN_STEP_STATUS_SKIPPED = "skipped"
PLAN_STEP_STATUSES = frozenset({
    PLAN_STEP_STATUS_PENDING,
    PLAN_STEP_STATUS_RUNNING,
    PLAN_STEP_STATUS_COMPLETED,
    PLAN_STEP_STATUS_FAILED,
    PLAN_STEP_STATUS_SKIPPED,
})
```

- [ ] **Step 4: Add bulk replace helper**

In `backend/src/storage/database.py`, add `replace_plan_steps_record(plan_id, steps)` near `update_plan_step_record()`:

```python
def replace_plan_steps_record(plan_id: str, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace all structured steps for a plan."""
    ensure_db()
    now = _utc_now()
    with _lock, _connect() as conn:
        conn.execute("DELETE FROM plan_steps WHERE plan_id = ?", (plan_id,))
        conn.executemany(
            """
            INSERT INTO plan_steps (
                id, plan_id, step_index, title, description, status,
                assigned_agent_id, evidence, result, started_at, completed_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    step["id"],
                    plan_id,
                    step["stepIndex"],
                    step["title"],
                    step.get("description", ""),
                    step.get("status", "pending"),
                    step.get("assignedAgentId"),
                    step.get("evidence", ""),
                    step.get("result", ""),
                    step.get("startedAt"),
                    step.get("completedAt"),
                    step.get("updatedAt") or now,
                )
                for step in steps
            ],
        )
        conn.commit()
    plan = get_plan_record(plan_id)
    return list((plan or {}).get("steps") or [])
```

- [ ] **Step 5: Run focused backend tests**

Run:

```bash
cd backend && uv run pytest test/test_chat_runtime_regressions.py -q
```

Expected: existing regression tests still pass.

---

## Task 2: Plan Task Tools and Manager

**Files:**
- Modify: `backend/src/server/routes/chat/plan_mode/manager.py`
- Modify: `backend/src/server/routes/chat/plan_mode/tools.py`
- Modify: `backend/src/server/routes/chat/plan_mode/policy.py`
- Modify: `backend/src/server/routes/chat/plan_mode/__init__.py`
- Create or Modify: `backend/test/test_plan_mode_tools.py`

- [ ] **Step 1: Write manager/tool regression tests**

Add tests covering:

```python
def test_todo_write_replaces_structured_steps(...):
    manager = PlanModeManager(...)
    manager.enter(goal="测试 Plan task", title="Plan task")
    context = manager.write_tasks(items=[{"title": "读数据结构", "description": "核对 plans 表"}])
    assert context.plan_id
    assert context.steps[0]["title"] == "读数据结构"
    assert manager.drain_runtime_events()[-1]["type"] == "PLAN_TASKS_UPDATED"


def test_task_output_updates_step_and_current_step(...):
    manager = PlanModeManager(...)
    manager.enter(goal="测试 Plan task", title="Plan task")
    manager.write_tasks(items=[{"title": "补工具"}])
    step = manager.list_tasks()[0]
    context = manager.record_task_output(step_id=step["id"], status="completed", evidence="测试通过", result="工具可用")
    assert context.steps[0]["status"] == "completed"
    assert context.steps[0]["result"] == "工具可用"
    assert manager.drain_runtime_events()[-1]["type"] == "PLAN_TASK_OUTPUT"
```

- [ ] **Step 2: Extend `PlanModeContext`**

Add to `PlanModeContext`:

```python
    steps: list[dict] = None
```

Then add a `__post_init__` method:

```python
    def __post_init__(self) -> None:
        if self.steps is None:
            self.steps = []
```

- [ ] **Step 3: Implement manager methods**

Add to `PlanModeManager`:

```python
    def write_tasks(self, *, items: list[dict]) -> PlanModeContext:
        """写入结构化计划步骤。"""
        self._ensure_plan_started()
        steps = self._normalize_steps(items)
        self.context.steps = steps
        self._append_runtime_event("PLAN_TASKS_UPDATED", status="running", payload={"steps": steps})
        if self.persist and self.context.plan_id:
            from src.storage.database import append_plan_event_record, replace_plan_steps_record
            self.context.steps = replace_plan_steps_record(self.context.plan_id, steps)
            append_plan_event_record(self.context.plan_id, step_id=None, event_type="PLAN_TASKS_UPDATED", content=f"steps={len(steps)}")
        return self.context

    def list_tasks(self) -> list[dict]:
        """返回当前结构化计划步骤。"""
        if self.persist and self.context.plan_id:
            from src.storage.database import get_plan_record
            plan = get_plan_record(self.context.plan_id)
            if plan:
                self.context.steps = list(plan.get("steps") or [])
        return list(self.context.steps or [])
```

Also implement `record_task_output()` with status validation, `current_step_id` update, event append, and `append_plan_event_record()`.

- [ ] **Step 4: Implement tools**

Add:

```python
class TodoWriteTool(_PlanToolBase):
    name: str = "todo_write"
    description: str = "Create or replace the structured task list for the active plan."


class TaskListTool(_PlanToolBase):
    name: str = "task_list"
    description: str = "List structured tasks for the current plan."


class TaskOutputTool(_PlanToolBase):
    name: str = "task_output"
    description: str = "Update one structured plan task with status, evidence, and result."
```

Return JSON payloads containing `planId`, `planFile`, `steps`, and for `task_output`, `step`.

- [ ] **Step 5: Update policy and exports**

Add `"task_list"` and `"task_output"` to `PLAN_CONTROL_TOOL_NAMES`.

Export `TodoWriteTool`, `TaskListTool`, `TaskOutputTool` from `plan_mode/__init__.py`.

- [ ] **Step 6: Run focused tests**

Run:

```bash
cd backend && uv run pytest test/test_plan_mode_tools.py -q
```

Expected: new tests pass.

---

## Task 3: Runtime Event Fallback and Build Prompt

**Files:**
- Modify: `backend/src/server/routes/chat/leader_stream.py`
- Modify: `backend/src/server/routes/chat/plan_mode/middleware.py`
- Modify: `backend/test/test_chat_runtime_regressions.py`

- [ ] **Step 1: Extend fallback mapping**

In `leader_stream.py`, update `_PLAN_TOOL_RUNTIME_EVENTS`:

```python
_PLAN_TOOL_RUNTIME_EVENTS = {
    "plan_enter": ("PLAN_MODE_ENTERED", "running"),
    "plan_write": ("PLAN_UPDATED", "running"),
    "plan_exit": ("PLAN_WAITING_APPROVAL", "waiting"),
    "todo_write": ("PLAN_TASKS_UPDATED", "running"),
    "task_output": ("PLAN_TASK_OUTPUT", ""),
}
```

For `task_output`, use `result.get("step", {}).get("status")` as Runtime Event status when present; fallback to `"running"`.

- [ ] **Step 2: Include task payloads in fallback**

Update `_plan_runtime_event_from_tool_result()` so payload copies:

```python
if "steps" in result:
    payload["steps"] = result.get("steps") or []
if "step" in result:
    payload["step"] = result.get("step") or {}
```

- [ ] **Step 3: Strengthen Build Mode prompt**

In `BUILD_MODE_HINT_TEMPLATE`, add:

```text
Use task_list before executing if structured tasks are available. After each
meaningful step, call task_output with the task status, evidence, and result so
the user can track progress in the UI.
```

- [ ] **Step 4: Add regression tests**

In `backend/test/test_chat_runtime_regressions.py`, add tests proving:

```python
todo_write tool result emits PLAN_TASKS_UPDATED
task_output tool result emits PLAN_TASK_OUTPUT and preserves step status
```

- [ ] **Step 5: Run regression tests**

Run:

```bash
cd backend && uv run pytest test/test_chat_runtime_regressions.py -q
```

Expected: all regression tests pass.

---

## Task 4: Frontend Plan Task Display

**Files:**
- Modify: `frontend/src/features/chat/ProcessDetailsBlock.tsx`
- Modify: `frontend/src/types/chatEvents.ts`

- [ ] **Step 1: Include task events in Plan block**

Update `getPlanModeEvents()` to include:

```ts
event.type === RUNTIME_EVENT_TYPES.PLAN_TASKS_UPDATED ||
event.type === RUNTIME_EVENT_TYPES.PLAN_TASK_OUTPUT
```

- [ ] **Step 2: Derive latest steps from events**

Inside `PlanModeRuntimeBlock`, derive:

```ts
const stepsEvent = [...relatedEvents].reverse().find((event) => Array.isArray(event.payload.steps));
const steps = Array.isArray(stepsEvent?.payload.steps) ? stepsEvent.payload.steps : [];
```

- [ ] **Step 3: Render compact task list**

Render below markdown:

```tsx
{steps.length > 0 && (
  <div className="space-y-2 rounded-md bg-[var(--color-bg-primary)] p-3">
    {steps.map((step) => (
      <div key={String(step.id)} className="border-b border-[var(--color-border)] pb-2 last:border-b-0 last:pb-0">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0 truncate text-xs font-medium text-[var(--color-text-primary)]">
            {Number(step.stepIndex) + 1}. {String(step.title || 'Untitled task')}
          </div>
          <Tag size="small">{String(step.status || 'pending')}</Tag>
        </div>
        {step.result && (
          <div className="mt-1 line-clamp-2 text-xs text-[var(--color-text-secondary)]">
            {String(step.result)}
          </div>
        )}
      </div>
    ))}
  </div>
)}
```

- [ ] **Step 4: Run frontend build**

Run:

```bash
cd frontend && npm run build
```

Expected: build passes.

---

## Task 5: End-to-End Verification

**Files:**
- No product files unless bugs are found.

- [ ] **Step 1: Run backend tests**

Run:

```bash
cd backend && uv run pytest -q
```

Expected: all backend tests pass.

- [ ] **Step 2: Run frontend build**

Run:

```bash
cd frontend && npm run build
```

Expected: frontend build passes.

- [ ] **Step 3: Manual test prompt**

Use this prompt in the Chat UI:

```text
进入 plan 模式，先只读调查当前仓库 Plan Mode 的任务进度工具缺口，写出包含 3 个结构化步骤的计划，调用 todo_write 同步任务列表，然后 plan_exit 等我审批。审批后按计划执行，每完成一步都调用 task_output 更新结果。
```

Expected:

- Plan 阶段出现 markdown 和结构化 task list。
- `plan_exit` 后审批按钮实时出现，不需要刷新。
- 批准后进入 Build Mode。
- 每个步骤执行后，Plan 面板中的 task 状态和结果实时更新。
- 最终答案只显示在最终回复区，过程文本不闪到正文。

---

## Self-Review

- Spec coverage: 数据结构、工具、事件兜底、前端展示、验证均有任务覆盖。
- Placeholder scan: 本计划没有 `TBD` 或开放式“以后补”步骤。
- Type consistency: 事件名使用 `PLAN_TASKS_UPDATED` / `PLAN_TASK_OUTPUT`；工具名使用 `todo_write` / `task_list` / `task_output`；step 字段使用现有 camelCase `ChatPlanStep`。
