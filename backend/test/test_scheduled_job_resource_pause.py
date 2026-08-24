"""Project / Agent / Skill 资源变更时立即暂停 Job。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.scheduler.service import ScheduledJobService
from src.server.routes import agents as agents_routes
from src.server.routes.workspaces import (
    ProjectResourceBindingRequest,
    ProjectUpdateRequest,
    put_project_resource,
    update_project_record,
)
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE_ID,
    ensure_db,
    list_user_notifications,
    replace_skills,
    upsert_project_resource_binding,
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """每个用例使用独立 SQLite。"""

    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    yield
    database._initialized = False


def _service() -> ScheduledJobService:
    ensure_db()
    return ScheduledJobService()


def _weekly_job(service: ScheduledJobService, **overrides):
    payload = {
        "workspace_id": DEFAULT_WORKSPACE_ID,
        "project_id": DEFAULT_PROJECT_ID,
        "name": "周五周报",
        "agent_id": "agent-1",
        "schedule_kind": "weekly",
        "schedule_expr": {"weekdays": ["fri"], "at": "18:00"},
        "timezone": "Asia/Shanghai",
        "frozen_prompt": "生成本周周报，写入 outputs/weekly-report.md",
    }
    payload.update(overrides)
    return service.create_job(**payload)


def _confirm(service: ScheduledJobService, job_id: str):
    return service.confirm(job_id, now=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc))


def _pause_notes(job_id: str) -> list[dict]:
    return [
        item
        for item in list_user_notifications(project_id=DEFAULT_PROJECT_ID)
        if item["job_id"] == job_id and item["kind"] == "job_paused"
    ]


def test_archive_project_pauses_jobs(isolated_db):
    """归档 Project 时立即暂停该项目下 active / pending Job 并写通知。"""

    service = _service()
    pending = _weekly_job(service, name="待确认周报")
    active = _confirm(service, _weekly_job(service, name="已启用周报").id)

    asyncio.run(update_project_record(
        DEFAULT_PROJECT_ID,
        ProjectUpdateRequest(status="archived"),
    ))

    pending = service.get_job(pending.id)
    active = service.get_job(active.id)
    assert pending.status == "paused"
    assert pending.pause_reason == "project_archived"
    assert active.status == "paused"
    assert active.pause_reason == "project_archived"
    assert _pause_notes(pending.id)
    assert _pause_notes(active.id)


def test_disable_agent_pauses_jobs(isolated_db, monkeypatch):
    """删除 Agent 时立即暂停其 Job 并写通知。"""

    service = _service()
    keep = _confirm(service, _weekly_job(service, name="保留任务").id)
    gone = _confirm(
        service,
        _weekly_job(service, name="失效任务", agent_id="agent-gone").id,
    )
    agent = agents_routes.AgentResponse.model_validate({
        "id": "agent-gone",
        "name": "agent-gone",
        "avatar": "",
        "description": "",
        "system_prompt": "prompt",
        "modelConfig": {},
        "mcpTools": [],
        "knowledgeTools": [],
        "skills": [],
        "tags": [],
        "guardrails": {},
        "status": "idle",
        "created_at": "2026-08-20T00:00:00+00:00",
        "updated_at": "2026-08-20T00:00:00+00:00",
        "workspaceId": DEFAULT_WORKSPACE_ID,
    })
    monkeypatch.setattr(agents_routes, "_agents", {"agent-gone": agent})

    asyncio.run(agents_routes.delete_agent("agent-gone"))

    gone = service.get_job(gone.id)
    keep = service.get_job(keep.id)
    assert gone.status == "paused"
    assert gone.pause_reason == "agent_missing"
    assert keep.status == "active"
    assert _pause_notes(gone.id)
    assert not _pause_notes(keep.id)


def test_skill_binding_removal_pauses_jobs_that_required_that_skill(isolated_db):
    """移出 Skill 绑定只暂停冻结了该 Skill 的 Job。"""

    service = _service()
    replace_skills({
        "skill-report": {
            "id": "skill-report",
            "name": "周报技能",
            "description": "",
            "category": "test",
            "status": "installed",
            "enabled": True,
            "updatedAt": "2026-08-20T00:00:00+00:00",
            "workspaceId": DEFAULT_WORKSPACE_ID,
        },
    })
    upsert_project_resource_binding({
        "project_id": DEFAULT_PROJECT_ID,
        "resource_type": "skill",
        "resource_id": "skill-report",
        "enabled": True,
    })
    needed = _confirm(
        service,
        _weekly_job(service, name="依赖技能", skill_ids=["skill-report"]).id,
    )
    other = _confirm(service, _weekly_job(service, name="不依赖技能").id)

    asyncio.run(put_project_resource(
        DEFAULT_PROJECT_ID,
        "skill",
        "skill-report",
        ProjectResourceBindingRequest(enabled=False),
    ))

    needed = service.get_job(needed.id)
    other = service.get_job(other.id)
    assert needed.status == "paused"
    assert needed.pause_reason == "skill_unbound"
    assert other.status == "active"
    assert _pause_notes(needed.id)
    assert not _pause_notes(other.id)
