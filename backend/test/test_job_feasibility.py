"""创建前能力门禁：发邮件矩阵与 ScheduleJob 硬闸门。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.scheduler.capability_catalog import CapabilityInventoryItem
from src.scheduler.feasibility import evaluate_job_feasibility
from src.scheduler.models import ScheduledJobValidationError
from src.scheduler.service import ScheduledJobService
from src.scheduler.settings import set_scheduled_jobs_settings
from src.storage import database
from src.storage.database import DEFAULT_PROJECT_ID, DEFAULT_WORKSPACE_ID, ensure_db, upsert_agent


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    yield
    database._initialized = False


def _file_builtins(*, read_only: bool = False) -> list[CapabilityInventoryItem]:
    rows = []
    for name, tags in (
        ("Read", ["file.read"]),
        ("Grep", ["file.read"]),
        ("Glob", ["file.read"]),
        ("Write", ["file.write"]),
        ("Edit", ["file.write"]),
        ("HttpRequest", ["http.call"]),
        ("Bash", ["shell.execute"]),
    ):
        rows.append(CapabilityInventoryItem(
            capability_id=f"builtin:{name}",
            kind="builtin",
            resource_id=name,
            tool_name=name,
            title=name,
            tags=tags,
            tag_source={tag: "declared" for tag in tags},
            unattended_ok=not (read_only and name in {"Write", "Edit", "HttpRequest", "Bash"}),
        ))
    return rows


def _gmail_send() -> CapabilityInventoryItem:
    return CapabilityInventoryItem(
        capability_id="mcp:gmail-send-email",
        kind="mcp",
        resource_id="gmail-send-email",
        server_id="gmail",
        tool_name="send_email",
        title="Send email",
        tags=["email.send"],
        tag_source={"email.send": "name"},
        unattended_ok=True,
    )


def test_email_without_channel_is_partial(monkeypatch):
    monkeypatch.setattr(
        "src.scheduler.feasibility.build_agent_capability_inventory",
        lambda *args, **kwargs: _file_builtins(),
    )
    monkeypatch.setattr(
        "src.server.routes.agents.get_registered_agent",
        lambda agent_id: SimpleNamespace(guardrails=SimpleNamespace(
            tool_permission_profile="trusted_tools",
        )),
    )
    result = evaluate_job_feasibility(
        task_intent="每周五给客户发周报邮件",
        agent_id="agent-1",
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        include_suggestions=False,
    )
    assert result.verdict == "partial"
    assert "email.send" in result.required_intents
    assert any(item.intent == "email.send" for item in result.missing)
    assert result.downgrade_offer is not None
    blob = result.message + str(result.downgrade_offer)
    assert "不能" in blob
    assert "邮件" in blob or "外发" in blob
    assert "生成" in blob


def test_email_http_request_only_is_not_ok(monkeypatch):
    monkeypatch.setattr(
        "src.scheduler.feasibility.build_agent_capability_inventory",
        lambda *args, **kwargs: _file_builtins(),
    )
    monkeypatch.setattr(
        "src.server.routes.agents.get_registered_agent",
        lambda agent_id: SimpleNamespace(guardrails=SimpleNamespace(
            tool_permission_profile="trusted_tools",
        )),
    )
    result = evaluate_job_feasibility(
        task_intent="发邮件给客户",
        agent_id="agent-1",
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        include_suggestions=False,
    )
    assert result.verdict != "ok"
    assert all(item.capability_id != "builtin:HttpRequest" or "email.send" not in item.tags for item in result.claimed)


def test_trusted_gmail_send_is_ok(monkeypatch):
    monkeypatch.setattr(
        "src.scheduler.feasibility.build_agent_capability_inventory",
        lambda *args, **kwargs: [*_file_builtins(), _gmail_send()],
    )
    monkeypatch.setattr(
        "src.server.routes.agents.get_registered_agent",
        lambda agent_id: SimpleNamespace(guardrails=SimpleNamespace(
            tool_permission_profile="trusted_tools",
        )),
    )
    result = evaluate_job_feasibility(
        task_intent="每周五给客户发周报邮件",
        agent_id="agent-1",
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        include_suggestions=False,
    )
    assert result.verdict == "ok"
    assert any(item.capability_id == "mcp:gmail-send-email" for item in result.claimed)
    assert "send_email" in result.firing_tools_preview
    assert "FinalAnswerStart" in result.firing_tools_preview
    assert "list_messages" not in result.firing_tools_preview


def test_send_only_without_channel_is_impossible(monkeypatch):
    monkeypatch.setattr(
        "src.scheduler.feasibility.build_agent_capability_inventory",
        lambda *args, **kwargs: _file_builtins(),
    )
    monkeypatch.setattr(
        "src.server.routes.agents.get_registered_agent",
        lambda agent_id: SimpleNamespace(guardrails=SimpleNamespace(
            tool_permission_profile="trusted_tools",
        )),
    )
    result = evaluate_job_feasibility(
        task_intent="只发送邮件给客户，不要生成文件",
        agent_id="agent-1",
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        include_suggestions=False,
    )
    assert result.verdict == "impossible"


def test_schedule_job_gate_rejects_email_without_accept_partial(isolated_db, monkeypatch):
    ensure_db()
    set_scheduled_jobs_settings(enabled=True, capability_gate=True)
    upsert_agent({
        "id": "agent-1",
        "name": "agent-1",
        "system_prompt": "p",
        "workspaceId": DEFAULT_WORKSPACE_ID,
        "status": "idle",
    })
    monkeypatch.setattr(
        "src.scheduler.feasibility.build_agent_capability_inventory",
        lambda *args, **kwargs: _file_builtins(),
    )
    monkeypatch.setattr(
        "src.server.routes.agents.get_registered_agent",
        lambda agent_id: SimpleNamespace(guardrails=SimpleNamespace(
            tool_permission_profile="trusted_tools",
        )),
    )
    service = ScheduledJobService()
    with pytest.raises(ScheduledJobValidationError, match="不能"):
        service.create_job(
            workspace_id=DEFAULT_WORKSPACE_ID,
            project_id=DEFAULT_PROJECT_ID,
            name="空头发信",
            agent_id="agent-1",
            schedule_kind="weekly",
            schedule_expr={"weekdays": ["fri"], "at": "18:00"},
            timezone="Asia/Shanghai",
            frozen_prompt="写入 outputs/weekly-report.md",
            task_intent="每周五给客户发周报邮件",
        )
    job = service.create_job(
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
        name="降级周报",
        agent_id="agent-1",
        schedule_kind="weekly",
        schedule_expr={"weekdays": ["fri"], "at": "18:00"},
        timezone="Asia/Shanghai",
        frozen_prompt="写入 outputs/weekly-report.md",
        task_intent="每周五给客户发周报邮件",
        accept_partial=True,
    )
    assert job.capability_contract.verdict == "partial"
    assert job.tool_scope.allow_mcp is False
    assert job.capability_contract.task_intent == "每周五给客户发周报邮件"
