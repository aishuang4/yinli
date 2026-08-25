"""Project / Agent / Skill 资源变更时立即暂停 Job。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from fastapi import HTTPException

from src.scheduler.models import ScheduledJobStateError
from src.scheduler.service import ScheduledJobService
from src.scheduler.settings import set_scheduled_jobs_enabled
from src.server.routes import agents as agents_routes
from src.server.routes import mcp as mcp_routes
from src.server.routes import skills as skills_routes
from src.server.routes import tools as tools_routes
from src.server.routes.scheduled_jobs import confirm_scheduled_job
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
    delete_agent,
    ensure_db,
    list_user_notifications,
    replace_mcp_servers,
    replace_skills,
    upsert_agent,
    upsert_http_tool,
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


def _ensure_agent(agent_id: str) -> None:
    upsert_agent({
        "id": agent_id,
        "name": agent_id,
        "system_prompt": "prompt",
        "workspaceId": DEFAULT_WORKSPACE_ID,
        "status": "idle",
    })


def _ensure_skill(skill_id: str) -> None:
    replace_skills({
        skill_id: {
            "id": skill_id,
            "name": skill_id,
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
        "resource_id": skill_id,
        "enabled": True,
    })


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
    _ensure_agent(str(payload["agent_id"]))
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


def test_confirm_after_archive_pending_job_fails(isolated_db):
    """归档后 pending Job 已暂停，confirm 必须 409 且保持 pause_reason。"""

    service = _service()
    pending = _weekly_job(service, name="归档后不可确认")
    asyncio.run(update_project_record(
        DEFAULT_PROJECT_ID,
        ProjectUpdateRequest(status="archived"),
    ))
    pending = service.get_job(pending.id)
    assert pending.status == "paused"
    assert pending.pause_reason == "project_archived"

    with pytest.raises(ScheduledJobStateError, match="pending_confirmation"):
        service.confirm(pending.id)
    with pytest.raises(ScheduledJobStateError, match="已归档"):
        service.resume(pending.id)

    set_scheduled_jobs_enabled(True)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(confirm_scheduled_job(pending.id))
    assert exc_info.value.status_code == 409

    frozen = service.get_job(pending.id)
    assert frozen.status == "paused"
    assert frozen.pause_reason == "project_archived"


def test_update_agent_removing_skill_pauses_jobs(isolated_db, monkeypatch):
    """从 Agent 配置摘掉 Skill 时，暂停冻结了该 Skill 的 Job。"""

    service = _service()
    _ensure_skill("skill-report")
    needed = _confirm(
        service,
        _weekly_job(service, name="依赖 Agent Skill", skill_ids=["skill-report"]).id,
    )
    other = _confirm(service, _weekly_job(service, name="无 Skill").id)
    agent = agents_routes.AgentResponse.model_validate({
        "id": "agent-1",
        "name": "agent-1",
        "avatar": "",
        "description": "",
        "system_prompt": "prompt",
        "modelConfig": {},
        "mcpTools": [],
        "knowledgeTools": [],
        "skills": ["skill-report"],
        "tags": [],
        "guardrails": {},
        "status": "idle",
        "created_at": "2026-08-20T00:00:00+00:00",
        "updated_at": "2026-08-20T00:00:00+00:00",
        "workspaceId": DEFAULT_WORKSPACE_ID,
    })
    monkeypatch.setattr(agents_routes, "_agents", {"agent-1": agent})
    monkeypatch.setattr(agents_routes, "invalidate_agent", lambda _agent_id: None)
    monkeypatch.setattr(agents_routes, "upsert_agent", lambda _data: None)

    asyncio.run(agents_routes.update_agent(
        "agent-1",
        agents_routes.AgentUpdate(skills=[]),
    ))

    needed = service.get_job(needed.id)
    other = service.get_job(other.id)
    assert needed.status == "paused"
    assert needed.pause_reason == "skill_unbound"
    assert other.status == "active"
    assert _pause_notes(needed.id)


def test_skill_uninstall_pauses_jobs(isolated_db, monkeypatch):
    """卸载 Skill 时立即暂停冻结该 Skill 的 Job。"""

    service = _service()
    _ensure_skill("skill-report")
    needed = _confirm(
        service,
        _weekly_job(service, name="依赖卸载 Skill", skill_ids=["skill-report"]).id,
    )
    skill = skills_routes.Skill.model_validate({
        "id": "skill-report",
        "name": "周报技能",
        "description": "",
        "category": "test",
        "status": "installed",
        "enabled": True,
        "updatedAt": "2026-08-20T00:00:00+00:00",
        "workspaceId": DEFAULT_WORKSPACE_ID,
    })
    monkeypatch.setattr(skills_routes, "_skills", {"skill-report": skill})
    monkeypatch.setattr(skills_routes, "_persist_if_user_skill", lambda _skill_id: None)

    asyncio.run(skills_routes.uninstall_skill("skill-report"))

    needed = service.get_job(needed.id)
    assert needed.status == "paused"
    assert needed.pause_reason == "skill_unbound"
    assert _pause_notes(needed.id)


def test_mcp_catalog_delete_pauses_jobs_requiring_mcp(isolated_db, monkeypatch):
    """删除 MCP 目录项时暂停 allow_mcp 的 Job。"""

    service = _service()
    replace_mcp_servers({
        "mcp-custom": {
            "id": "mcp-custom",
            "name": "MCP Custom",
            "description": "",
            "category": "test",
            "transport": "sse",
            "status": "installed",
            "enabled": True,
            "updatedAt": "2026-08-20T00:00:00+00:00",
            "workspaceId": DEFAULT_WORKSPACE_ID,
        },
    })
    upsert_project_resource_binding({
        "project_id": DEFAULT_PROJECT_ID,
        "resource_type": "mcp",
        "resource_id": "mcp-custom",
        "enabled": True,
    })
    needed = _confirm(
        service,
        _weekly_job(
            service,
            name="需要 MCP",
            tool_scope={"allow_mcp": True},
        ).id,
    )
    other = _confirm(service, _weekly_job(service, name="不需要 MCP").id)
    server = mcp_routes.McpServer.model_validate({
        "id": "mcp-custom",
        "name": "MCP Custom",
        "description": "",
        "category": "test",
        "transport": "sse",
        "status": "installed",
        "enabled": True,
        "updatedAt": "2026-08-20T00:00:00+00:00",
        "workspaceId": DEFAULT_WORKSPACE_ID,
    })
    monkeypatch.setattr(mcp_routes, "_mcp_servers", {"mcp-custom": server})
    monkeypatch.setattr(mcp_routes, "_builtin_mcp_servers", {})
    monkeypatch.setattr(mcp_routes, "_save_user_mcp_servers", lambda: None)

    asyncio.run(mcp_routes.delete_mcp_server("mcp-custom"))

    needed = service.get_job(needed.id)
    other = service.get_job(other.id)
    assert needed.status == "paused"
    assert needed.pause_reason == "needs_job_update"
    assert other.status == "active"
    assert _pause_notes(needed.id)


def test_http_tool_delete_pauses_jobs_requiring_http(isolated_db):
    """删除 HTTP 工具时暂停 allow_http 的 Job。"""

    service = _service()
    upsert_http_tool({
        "id": "http-a",
        "name": "HTTP A",
        "url": "https://example.com/api",
        "workspaceId": DEFAULT_WORKSPACE_ID,
    })
    upsert_project_resource_binding({
        "project_id": DEFAULT_PROJECT_ID,
        "resource_type": "http_tool",
        "resource_id": "http-a",
        "enabled": True,
    })
    needed = _confirm(
        service,
        _weekly_job(
            service,
            name="需要 HTTP",
            tool_scope={"allow_http": True},
        ).id,
    )

    asyncio.run(tools_routes.delete_http_tool_config("http-a"))

    needed = service.get_job(needed.id)
    assert needed.status == "paused"
    assert needed.pause_reason == "needs_job_update"
    assert _pause_notes(needed.id)


def test_http_binding_removal_pauses_jobs_that_required_http(isolated_db):
    """停用 HTTP Tool Project binding 时暂停 allow_http 的 Job。"""

    service = _service()
    upsert_http_tool({
        "id": "http-bound",
        "name": "HTTP Bound",
        "url": "https://example.com/bound",
        "workspaceId": DEFAULT_WORKSPACE_ID,
    })
    upsert_project_resource_binding({
        "project_id": DEFAULT_PROJECT_ID,
        "resource_type": "http_tool",
        "resource_id": "http-bound",
        "enabled": True,
    })
    needed = _confirm(
        service,
        _weekly_job(
            service,
            name="依赖 HTTP 绑定",
            tool_scope={"allow_http": True},
        ).id,
    )
    other = _confirm(service, _weekly_job(service, name="不依赖 HTTP").id)

    asyncio.run(put_project_resource(
        DEFAULT_PROJECT_ID,
        "http_tool",
        "http-bound",
        ProjectResourceBindingRequest(enabled=False),
    ))

    needed = service.get_job(needed.id)
    other = service.get_job(other.id)
    assert needed.status == "paused"
    assert needed.pause_reason == "needs_job_update"
    assert other.status == "active"
    assert _pause_notes(needed.id)


def test_resume_after_mcp_catalog_delete_fails(isolated_db):
    """MCP 从目录删除后，即使 binding 仍 enabled，resume 也必须失败。"""

    service = _service()
    replace_mcp_servers({
        "mcp-custom": {
            "id": "mcp-custom",
            "name": "MCP Custom",
            "description": "",
            "category": "test",
            "transport": "sse",
            "status": "installed",
            "enabled": True,
            "updatedAt": "2026-08-20T00:00:00+00:00",
            "workspaceId": DEFAULT_WORKSPACE_ID,
        },
    })
    upsert_project_resource_binding({
        "project_id": DEFAULT_PROJECT_ID,
        "resource_type": "mcp",
        "resource_id": "mcp-custom",
        "enabled": True,
    })
    job = _confirm(
        service,
        _weekly_job(service, name="MCP 周报", tool_scope={"allow_mcp": True}).id,
    )
    service.pause(job.id, reason="user")
    replace_mcp_servers({})

    with pytest.raises(ScheduledJobStateError, match="MCP"):
        service.resume(job.id)
    frozen = service.get_job(job.id)
    assert frozen.status == "paused"
    assert frozen.pause_reason == "user"


def test_resume_after_user_pause_and_agent_delete_fails(isolated_db):
    """用户先暂停后再删 Agent，resume 不得因 pause_reason=user 而放行。"""

    service = _service()
    job = _confirm(service, _weekly_job(service, name="先暂停再删 Agent").id)
    service.pause(job.id, reason="user")
    delete_agent("agent-1")

    with pytest.raises(ScheduledJobStateError, match="Agent"):
        service.resume(job.id)
    frozen = service.get_job(job.id)
    assert frozen.status == "paused"
    assert frozen.pause_reason == "user"
