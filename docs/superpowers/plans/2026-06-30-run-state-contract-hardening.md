# RunState Contract Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Chat 事件链路收束为稳定的 RunState v1 契约，避免后端新增 Plan/HITL/tool 功能时破坏前端流式状态。

**Architecture:** 后端 raw AgentScope 事件只进入 adapter/projector；业务事实使用 RuntimeEvent/PlanDomainEvent；前端主链路只消费 `RUN_STATE` 与枚举化 `RUN_STATE_PATCH`。后端 Pydantic schema 与前端 TypeScript 类型保持同名字段、同语义。

**Tech Stack:** Python 3.11 + Pydantic + FastAPI SSE，React 19 + TypeScript，pytest，Vite build。

---

## 执行进度

- [x] 后端 RunState v1 Pydantic schema 已新增。
- [x] `RUN_STATE_PATCH.kind` 已枚举化并通过回归测试覆盖。
- [x] projector 事件矩阵测试已覆盖 Plan、HITL、tool、final answer、clarification。
- [x] 前端 RunState TypeScript 类型已抽到 `frontend/src/types/runState.ts`，`useEventStream` 已按 `patch.kind` 分支消费。
- [x] Plan Mode 内部事件已收束为 `PlanDomainEvent`，统一转换为 `RUNTIME_EVENT` 后跨层输出。

## 文件结构

- Create: `backend/src/server/routes/chat/run_state_schema.py`
  - 定义 RunState v1 快照、轻量工具、审批、Plan、Patch Pydantic schema。
  - 定义 `RunStatePatchKind` 枚举，禁止自由散落 patch 字段。
- Modify: `backend/src/server/routes/chat/event_hub/projector.py`
  - 使用 schema 产出和归一化 RunState。
  - 保持 projector 仍是唯一 raw event -> RunState 转换层。
- Modify: `backend/src/server/routes/chat/background_runs.py`
  - `_run_state_sse()` 与 `_run_state_patch_sse()` 通过 schema 校验输出。
  - `RUN_STATE_PATCH` 增加 `kind`，并只使用枚举化 patch 分支。
- Create: `frontend/src/types/runState.ts`
  - 定义与后端 RunState v1 对齐的 TypeScript 类型。
  - 定义 `RunStatePatchKind` union，供 `useEventStream` 消费。
- Modify: `frontend/src/hooks/useEventStream.ts`
  - 从 `types/runState` 引入类型。
  - `applyRunStatePatch()` 按 `patch.kind` 分支处理，不再靠可选字段猜测事件含义。
  - 删除主链路对 raw event 类型常量的残余依赖。
- Modify: `backend/src/server/routes/chat/plan_mode/events.py`
  - 增加 `PlanDomainEvent` Pydantic schema 和 `build_plan_domain_event()`。
  - `build_plan_event()` 只负责将 PlanDomainEvent 转为 RuntimeEvent。
- Modify: `backend/src/server/routes/chat/plan_mode/manager.py`
  - Plan manager 只创建 PlanDomainEvent，再由统一出口转 RuntimeEvent。
- Modify: `backend/test/test_chat_runtime_regressions.py`
  - 增加 projector 事件矩阵测试，覆盖 Plan、HITL、tool、final answer、clarification。
  - 增加 patch kind 校验测试。
- Modify: `docs/chat-sse-event-contract.md`
  - 更新 RunState v1 schema 与 `RUN_STATE_PATCH.kind` 契约。

## Task 1: 写入 RunState v1 后端 schema

**Files:**
- Create: `backend/src/server/routes/chat/run_state_schema.py`
- Modify: `backend/src/server/routes/chat/event_hub/projector.py`
- Test: `backend/test/test_chat_runtime_regressions.py`

- [ ] **Step 1: 新增后端 Pydantic schema**

Create `backend/src/server/routes/chat/run_state_schema.py` with:

```python
"""Chat RunState v1 schema。

该模块是后端到前端 Chat 主链路的稳定契约。AgentScope raw event、
RuntimeEvent 和 middleware 内部事件都必须先投影成这里定义的结构，
前端不得直接依赖 raw event 作为 live 状态来源。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


RunStateStatus = Literal["running", "waiting", "completed", "failed", "cancelled", "cancelling"]
RunStatePatchKind = Literal[
    "snapshot_meta",
    "reasoning_delta",
    "final_answer_delta",
    "thinking_delta",
    "tool",
    "team_activities",
    "skill_calls",
    "model_usage",
    "approval",
    "approval_resolved",
    "runtime_event",
    "plans",
    "error",
]


class RunStateToolSummary(BaseModel):
    id: str
    name: str = "tool"
    status: str = "running"
    inputPreview: str = ""
    outputPreview: str = ""
    outputSize: int = 0
    outputTruncated: bool = False
    error: str = ""
    startedAt: str = ""
    completedAt: str = ""


class RunStateApprovalSummary(BaseModel):
    interactionId: str
    kind: str = "hitl"
    type: str = "hitl"
    status: str = "waiting"
    agentId: str = ""
    agentName: str = ""
    toolCalls: list[dict[str, Any]] = Field(default_factory=list)
    title: str = ""
    description: str = ""
    questions: list[dict[str, Any]] = Field(default_factory=list)
    createdAt: str = ""


class RunStatePlanStep(BaseModel):
    id: str | None = None
    planId: str | None = None
    stepIndex: int | None = None
    title: str | None = None
    description: str | None = None
    status: str | None = None
    assignedAgentId: str | None = None
    evidence: str | None = None
    result: str | None = None
    startedAt: str | None = None
    completedAt: str | None = None
    updatedAt: str | None = None


class RunStatePlanSummary(BaseModel):
    planId: str
    planFile: str = ""
    status: str = "running"
    markdown: str = ""
    steps: list[RunStatePlanStep] = Field(default_factory=list)
    updatedAt: str = ""


class RunStateSnapshot(BaseModel):
    version: int = 1
    runId: str
    sessionId: str
    agentId: str = ""
    agentName: str = ""
    teamId: str = ""
    teamName: str = ""
    mode: str = "agent"
    status: RunStateStatus = "running"
    task: str = ""
    createdAt: str = ""
    updatedAt: str = ""
    completedAt: str | None = None
    currentStage: str = ""
    reasoning: str = ""
    reasoningTruncated: bool = False
    finalAnswer: str = ""
    finalAnswerTruncated: bool = False
    thinking: str = ""
    tools: list[RunStateToolSummary] = Field(default_factory=list)
    teamActivities: list[dict[str, Any]] = Field(default_factory=list)
    skillCalls: list[dict[str, Any]] = Field(default_factory=list)
    modelCaches: list[dict[str, Any]] = Field(default_factory=list)
    modelName: str = ""
    tokenUsage: dict[str, Any] | None = None
    plans: list[RunStatePlanSummary] = Field(default_factory=list)
    approvals: list[RunStateApprovalSummary] = Field(default_factory=list)
    runtimeEvents: list[dict[str, Any]] = Field(default_factory=list)
    error: str = ""
    lastEventType: str = ""
    eventCount: int = 0


class RunStatePatch(BaseModel):
    kind: RunStatePatchKind
    runId: str
    sessionId: str
    status: RunStateStatus | str
    currentStage: str = ""
    updatedAt: str = ""
    completedAt: str | None = None
    lastEventType: str = ""
    eventCount: int = 0
    reasoningDelta: str | None = None
    finalAnswerDelta: str | None = None
    thinkingDelta: str | None = None
    tool: RunStateToolSummary | None = None
    teamActivities: list[dict[str, Any]] | None = None
    skillCalls: list[dict[str, Any]] | None = None
    modelCaches: list[dict[str, Any]] | None = None
    modelName: str | None = None
    tokenUsage: dict[str, Any] | None = None
    approval: RunStateApprovalSummary | None = None
    resolvedApprovalId: str | None = None
    runtimeEvent: dict[str, Any] | None = None
    plans: list[RunStatePlanSummary] | None = None
    error: str | None = None


def dump_run_state(state: dict[str, Any]) -> dict[str, Any]:
    """校验并输出 RunState 快照。"""

    return RunStateSnapshot.model_validate(state).model_dump(mode="json")


def dump_run_state_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """校验并输出 RunState patch。"""

    return RunStatePatch.model_validate(patch).model_dump(mode="json", exclude_none=True)
```

- [ ] **Step 2: 修改 projector 的 normalize 输出**

In `backend/src/server/routes/chat/event_hub/projector.py`, import and use `dump_run_state`:

```python
from src.server.routes.chat.run_state_schema import dump_run_state
```

At the end of `normalize_run_state()` replace raw return with:

```python
    return dump_run_state(next_state)
```

- [ ] **Step 3: 运行后端 schema 相关测试**

Run: `cd backend && uv run pytest -q test/test_chat_runtime_regressions.py::test_tool_result_runtime_event_preserves_failed_state`

Expected: PASS.

## Task 2: 枚举化 RUN_STATE_PATCH

**Files:**
- Modify: `backend/src/server/routes/chat/background_runs.py`
- Test: `backend/test/test_chat_runtime_regressions.py`

- [ ] **Step 1: 写 failing test，要求每个 patch 都有 kind**

Add a regression test:

```python
def test_run_state_patch_has_stable_kind_for_tool_plan_hitl_and_final_answer():
    run = _projection_run()
    run.state = initial_run_state(run)

    cases = [
        ("FINAL_ANSWER_DELTA", {"delta": "完成"}, "final_answer_delta"),
        ("TOOL_CALL_START", {"tool_call_id": "call-1", "tool_call_name": "Read"}, "tool"),
        ("HITL_REQUIRED", {"interactionId": "hitl-1", "type": "REQUIRE_USER_CONFIRM"}, "approval"),
        ("RUNTIME_EVENT", {"type": "PLAN_WAITING_APPROVAL", "status": "waiting", "payload": {"planId": "plan-1"}}, "plans"),
    ]

    for event_type, data, expected_kind in cases:
        run.state = project_event(run.state, event_type, data, run)
        patch = background_runs._run_state_patch_sse(run, event_type, data)
        assert f'"kind": "{expected_kind}"' in patch
```

- [ ] **Step 2: Run failing test**

Run: `cd backend && uv run pytest -q test/test_chat_runtime_regressions.py::test_run_state_patch_has_stable_kind_for_tool_plan_hitl_and_final_answer`

Expected: FAIL because `kind` is missing.

- [ ] **Step 3: Implement patch kind builder**

In `backend/src/server/routes/chat/background_runs.py`, import:

```python
from src.server.routes.chat.run_state_schema import dump_run_state, dump_run_state_patch
```

Change `_run_state_sse()` to validate through `dump_run_state()`.

Change `_run_state_patch_sse()` to initialize:

```python
    patch: dict[str, object] = {
        "kind": _run_state_patch_kind(event_type, data),
        ...
    }
```

Add helper:

```python
def _run_state_patch_kind(event_type: str, data: dict) -> str:
    if event_type == "REASONING_DELTA" or event_type == "TEXT_BLOCK_DELTA":
        return "reasoning_delta"
    if event_type == "FINAL_ANSWER_DELTA":
        return "final_answer_delta"
    if event_type == "THINKING_BLOCK_DELTA":
        return "thinking_delta"
    if event_type in {"TOOL_CALL_START", "TOOL_CALL_DELTA", "TOOL_CALL_END", "TOOL_RESULT_TEXT_DELTA", "TOOL_RESULT_END"}:
        return "tool"
    if event_type == "TEAM_ACTIVITY":
        return "team_activities"
    if event_type in {"SKILL_CONTEXT", "SKILL_END"}:
        return "skill_calls"
    if event_type in {"MODEL_CALL_START", "MODEL_CALL_END"}:
        return "model_usage"
    if event_type in {"HITL_REQUIRED", "CLARIFICATION_REQUIRED"}:
        return "approval"
    if event_type == SSE_EVENT_RUNTIME:
        runtime_type = str(data.get("type") or "")
        if runtime_type == "HITL_RESOLVED":
            return "approval_resolved"
        if runtime_type.startswith("PLAN_"):
            return "plans"
        return "runtime_event"
    if event_type == "ERROR":
        return "error"
    return "snapshot_meta"
```

Before returning patch SSE:

```python
    return _sse_event("RUN_STATE_PATCH", dump_run_state_patch(patch))
```

- [ ] **Step 4: Run patch kind test**

Run: `cd backend && uv run pytest -q test/test_chat_runtime_regressions.py::test_run_state_patch_has_stable_kind_for_tool_plan_hitl_and_final_answer`

Expected: PASS.

## Task 3: 增加 projector 事件矩阵测试

**Files:**
- Modify: `backend/test/test_chat_runtime_regressions.py`

- [ ] **Step 1: Add matrix test**

Add:

```python
def test_projector_event_matrix_for_run_state_v1_contract():
    run = _projection_run()
    state = initial_run_state(run)
    events = [
        ("TOOL_CALL_START", {"tool_call_id": "call-read", "tool_call_name": "Read"}),
        ("TOOL_CALL_DELTA", {"tool_call_id": "call-read", "delta": '{"file_path":"README.md"}'}),
        ("TOOL_RESULT_TEXT_DELTA", {"tool_call_id": "call-read", "delta": "hello"}),
        ("TOOL_RESULT_END", {"tool_call_id": "call-read", "state": "success"}),
        ("FINAL_ANSWER_DELTA", {"delta": "最终答案"}),
        ("HITL_REQUIRED", {"interactionId": "hitl-1", "type": "REQUIRE_USER_CONFIRM", "toolCalls": [{"id": "call-bash", "name": "Bash"}]}),
        ("CLARIFICATION_REQUIRED", {"id": "clarify-1", "title": "需要信息", "questions": [{"id": "q1", "type": "text"}]}),
        ("RUNTIME_EVENT", {"type": "PLAN_TASKS_UPDATED", "status": "running", "payload": {"planId": "plan-1", "planFile": ".agent-workstation/plans/s/plan-1.md", "steps": [{"id": "s1", "title": "测试", "status": "pending"}]}}),
    ]
    for event_type, data in events:
        state = project_event(state, event_type, data, run)

    normalized = normalize_run_state(state)
    assert normalized["version"] == 1
    assert normalized["tools"][0]["id"] == "call-read"
    assert normalized["tools"][0]["status"] == "completed"
    assert normalized["finalAnswer"] == "最终答案"
    assert any(item["interactionId"] == "hitl-1" and item["kind"] == "hitl" for item in normalized["approvals"])
    assert any(item["interactionId"] == "clarify-1" and item["kind"] == "clarification" for item in normalized["approvals"])
    assert normalized["plans"][0]["planId"] == "plan-1"
    assert normalized["plans"][0]["steps"][0]["title"] == "测试"
```

- [ ] **Step 2: Run matrix test**

Run: `cd backend && uv run pytest -q test/test_chat_runtime_regressions.py::test_projector_event_matrix_for_run_state_v1_contract`

Expected: PASS after Task 1 schema is in place.

## Task 4: 前端 RunState 类型对齐与 patch kind 消费

**Files:**
- Create: `frontend/src/types/runState.ts`
- Modify: `frontend/src/hooks/useEventStream.ts`

- [ ] **Step 1: Create frontend RunState contract types**

Create `frontend/src/types/runState.ts` with TypeScript interfaces matching `run_state_schema.py`.

- [ ] **Step 2: Import shared types in useEventStream**

Remove local `RunStateToolSummary`, `RunStateApprovalSummary`, `ChatRunStateSnapshot`, `ChatRunStatePatch` interfaces from `useEventStream.ts` and import them:

```ts
import type {
  ChatRunStatePatch,
  ChatRunStateSnapshot,
  RunStateApprovalSummary,
  RunStateToolSummary,
} from '../types/runState';
```

- [ ] **Step 3: Switch applyRunStatePatch to `patch.kind`**

Change `applyRunStatePatch()` to branch from `patch.kind`:

```ts
const patchKind = patch.kind || 'snapshot_meta';
let toolCalls = patchKind === 'tool' && patch.tool
  ? mergeToolCalls(state.toolCalls, [toolCallFromRunStateTool(patch.tool)])
  : state.toolCalls;
```

Only merge approvals when `patchKind === 'approval'`, only resolve when `patchKind === 'approval_resolved'`, only update plans when `patchKind === 'plans'`.

- [ ] **Step 4: Remove residual runtime delta merge dependencies**

Remove `RUNTIME_EVENT_TYPES` import from `useEventStream.ts` if it is only used for `shouldMergeRuntimeDelta()`. Replace `shouldMergeRuntimeDelta()` with string comparisons or remove delta merge entirely if no UI requires compacting runtime deltas.

- [ ] **Step 5: Run frontend build**

Run: `cd frontend && npm run build`

Expected: PASS.

## Task 5: PlanDomainEvent 统一出口

**Files:**
- Modify: `backend/src/server/routes/chat/plan_mode/events.py`
- Modify: `backend/src/server/routes/chat/plan_mode/manager.py`
- Test: `backend/test/test_plan_mode_tools.py`

- [ ] **Step 1: Add PlanDomainEvent schema**

In `plan_mode/events.py`, add:

```python
class PlanDomainEvent(BaseModel):
    type: str
    status: str
    reason: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
```

Add:

```python
def build_plan_domain_event(event_type: str, *, status: str, reason: str = "", payload: dict[str, Any] | None = None) -> PlanDomainEvent:
    return PlanDomainEvent(type=event_type, status=status, reason=reason, payload=payload or {})


def plan_domain_event_to_runtime_event(event: PlanDomainEvent, *, context: PlanModeContext) -> dict[str, Any]:
    return build_runtime_event(
        event.type,
        session_id=context.session_id,
        run_id=context.run_id,
        scope="plan",
        source="runtime",
        agent_id=context.agent_id,
        agent_name=context.agent_name,
        status=event.status,
        reason=event.reason,
        payload={
            "planId": context.plan_id,
            "planFile": context.current_plan_file,
            **event.payload,
        },
    )
```

Make `build_plan_event()` call the two functions above for compatibility.

- [ ] **Step 2: Update manager internal event queue**

In `PlanModeManager.__init__`, change `self.runtime_events: list[dict] = []` to `self.domain_events: list[PlanDomainEvent] = []`.

Change `drain_runtime_events()` to convert domain events:

```python
events = [plan_domain_event_to_runtime_event(event, context=self.context) for event in self.domain_events]
self.domain_events.clear()
return events
```

Change `_append_runtime_event()` to append `build_plan_domain_event(...)`.

- [ ] **Step 3: Run plan tool tests**

Run: `cd backend && uv run pytest -q test/test_plan_mode_tools.py`

Expected: PASS.

## Task 6: 更新文档并全量验证

**Files:**
- Modify: `docs/chat-sse-event-contract.md`
- Modify: `docs/project-todo.md`

- [ ] **Step 1: Update contract docs**

Document:

- `RUN_STATE_PATCH.kind` is required.
- Frontend live state only consumes `RUN_STATE` and `RUN_STATE_PATCH`.
- Plan middleware emits PlanDomainEvent internally and only RuntimeEvent crosses the backend boundary.

- [ ] **Step 2: Run backend tests**

Run: `cd backend && uv run pytest -q`

Expected: all tests pass.

- [ ] **Step 3: Run frontend build**

Run: `cd frontend && npm run build`

Expected: build succeeds.

- [ ] **Step 4: Run diff check**

Run: `git diff --check`

Expected: no output.

## Self-review

- 覆盖用户要求：RunState v1 schema、patch 枚举、projector 测试矩阵、前端 RunState 消费、PlanDomainEvent 统一出口。
- 无旧 Plan Runtime / Subagent Runtime 依赖。
- 保留兼容 raw SSE 作为后端输入和 Trace 输入，不再作为前端 live 主状态。
- 每个任务都有明确文件、测试命令和验收标准。
