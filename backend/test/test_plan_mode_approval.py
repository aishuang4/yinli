import asyncio

from src.server.routes.chat.plan_mode.approval import approve_plan, deny_plan
from src.server.routes.chat.plan_mode.manager import PlanModeManager
from src.server.routes.chat.plan_mode.middleware import PlanModeMiddleware


def test_approve_plan_updates_status_and_returns_runtime_event(monkeypatch):
    """批准计划时更新持久化状态，并返回 PLAN_APPROVED 事件。"""

    updates: list[tuple[str, dict]] = []
    events: list[tuple[str, dict]] = []
    plan = {
        "id": "plan-1",
        "sessionId": "session-1",
        "agentId": "agent-1",
        "teamId": None,
        "title": "计划",
        "goal": "实现功能",
        "status": "waiting_approval",
        "markdown": "# 计划\n\n- [ ] 执行",
        "currentStepId": None,
        "createdAt": "2026-06-29T00:00:00+00:00",
        "updatedAt": "2026-06-29T00:00:00+00:00",
        "completedAt": None,
        "steps": [],
    }

    monkeypatch.setattr(
        "src.server.routes.chat.plan_mode.approval.get_plan_record",
        lambda plan_id: plan if plan_id == "plan-1" else None,
    )
    monkeypatch.setattr(
        "src.server.routes.chat.plan_mode.approval.update_plan_record",
        lambda plan_id, **kwargs: updates.append((plan_id, kwargs)) or {**plan, **{"status": kwargs["status"]}},
    )
    monkeypatch.setattr(
        "src.server.routes.chat.plan_mode.approval.append_plan_event_record",
        lambda plan_id, **kwargs: events.append((plan_id, kwargs)) or {},
    )

    result = approve_plan("plan-1")

    assert result["plan"]["status"] == "approved"
    assert updates == [("plan-1", {"status": "approved"})]
    assert events[0][1]["event_type"] == "PLAN_APPROVED"
    assert result["runtimeEvent"]["type"] == "PLAN_APPROVED"
    assert result["runtimeEvent"]["payload"]["planId"] == "plan-1"


def test_deny_plan_updates_status_with_reason(monkeypatch):
    """拒绝计划时记录拒绝原因，并返回 PLAN_DENIED 事件。"""

    events: list[tuple[str, dict]] = []
    plan = {
        "id": "plan-2",
        "sessionId": "session-1",
        "agentId": "agent-1",
        "teamId": None,
        "title": "计划",
        "goal": "实现功能",
        "status": "waiting_approval",
        "markdown": "# 计划",
        "currentStepId": None,
        "createdAt": "2026-06-29T00:00:00+00:00",
        "updatedAt": "2026-06-29T00:00:00+00:00",
        "completedAt": None,
        "steps": [],
    }

    monkeypatch.setattr(
        "src.server.routes.chat.plan_mode.approval.get_plan_record",
        lambda plan_id: plan if plan_id == "plan-2" else None,
    )
    monkeypatch.setattr(
        "src.server.routes.chat.plan_mode.approval.update_plan_record",
        lambda plan_id, **kwargs: {**plan, **{"status": kwargs["status"]}},
    )
    monkeypatch.setattr(
        "src.server.routes.chat.plan_mode.approval.append_plan_event_record",
        lambda plan_id, **kwargs: events.append((plan_id, kwargs)) or {},
    )

    result = deny_plan("plan-2", reason="范围需要调整")

    assert result["plan"]["status"] == "denied"
    assert events[0][1]["event_type"] == "PLAN_DENIED"
    assert "范围需要调整" in events[0][1]["content"]
    assert result["runtimeEvent"]["type"] == "PLAN_DENIED"
    assert result["runtimeEvent"]["payload"]["reason"] == "范围需要调整"


def test_manager_loads_approved_plan_for_build_mode_prompt():
    """下一轮传入 approvedPlanId 后，middleware 注入 Build mode 和计划正文。"""

    manager = PlanModeManager(
        session_id="session-1",
        run_id="run-2",
        agent_id="agent-1",
        agent_name="Agent",
        persist=False,
    )
    manager.load_approved_plan({
        "id": "plan-1",
        "sessionId": "session-1",
        "agentId": "agent-1",
        "teamId": None,
        "title": "计划",
        "goal": "实现功能",
        "status": "approved",
        "markdown": "# 计划\n\n- [ ] 修改代码",
        "currentStepId": None,
        "createdAt": "2026-06-29T00:00:00+00:00",
        "updatedAt": "2026-06-29T00:00:00+00:00",
        "completedAt": None,
        "steps": [],
    })

    prompt = asyncio.run(PlanModeMiddleware(manager).on_system_prompt(object(), "base"))

    assert "BUILD mode is active" in prompt
    assert "# 计划" in prompt
    assert "修改代码" in prompt
