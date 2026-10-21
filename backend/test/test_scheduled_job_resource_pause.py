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
from src.server.routes.projects import archive_project_record
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    create_project,
    delete_agent,
    ensure_db,
    list_user_notifications,
    replace_mcp_servers,
    replace_skills,
    upsert_agent,
    upsert_http_tool,
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """每个用例使用独立 SQLite。"""

    monkeypatch.setattr("src.config.runtime_paths.USER_DATA_ROOT", tmp_path / "user-data")
    yield
    database.forget_db(database.db_path())


def _service() -> ScheduledJobService:
    ensure_db()
    return ScheduledJobService()


def _ensure_agent(agent_id: str) -> None:
    upsert_agent({
        "id": agent_id,
        "name": agent_id,
        "system_prompt": "prompt",
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
        },
    })


def _weekly_job(service: ScheduledJobService, **overrides):
    payload = {
        "project_slug": DEFAULT_PROJECT_ID,
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
        for item in list_user_notifications(project_slug=DEFAULT_PROJECT_ID)
        if item["job_id"] == job_id and item["kind"] == "job_paused"
    ]


def _named_project(tmp_path, name: str = "Reports") -> dict:
    root = tmp_path / name
    root.mkdir()
    return create_project({"name": name, "primaryPath": str(root)})


def test_archive_project_blocks_enable(isolated_db, tmp_path):
    """归档后不能确认或恢复该项目的 Job。默认项目不能归档。"""

    service = _service()
    project = _named_project(tmp_path)
    pending = _weekly_job(service, name="待确认周报", project_slug=project["slug"])
    active = _confirm(service, _weekly_job(service, name="已启用周报", project_slug=project["slug"]).id)

    with pytest.raises(HTTPException, match="默认项目不能归档") as exc_info:
        asyncio.run(archive_project_record(DEFAULT_PROJECT_ID))
    assert exc_info.value.status_code == 422

    archived = asyncio.run(archive_project_record(project["slug"]))
    assert archived.status == "archived"
    assert service.get_job(pending.id).status == "pending_confirmation"
    assert service.get_job(active.id).status == "active"
    with pytest.raises(ScheduledJobStateError, match="已归档"):
        service.confirm(pending.id)
    service.pause(active.id, reason="user")
    with pytest.raises(ScheduledJobStateError, match="已归档"):
        service.resume(active.id)


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


def test_skill_catalog_uninstall_pauses_only_jobs_that_froze_it(isolated_db, monkeypatch):
    """Skill 不再靠 Project binding。未绑定也能确认；从全局目录卸载才暂停冻结了它的 Job。"""

    service = _service()
    _ensure_skill("skill-report")
    needed = _confirm(
        service,
        _weekly_job(service, name="依赖技能", skill_ids=["skill-report"]).id,
    )
    other = _confirm(service, _weekly_job(service, name="不依赖技能").id)
    assert needed.status == "active"
    assert other.status == "active"

    skill = skills_routes.Skill.model_validate({
        "id": "skill-report",
        "name": "周报技能",
        "description": "",
        "category": "test",
        "status": "installed",
        "enabled": True,
        "updatedAt": "2026-08-20T00:00:00+00:00",
    })
    monkeypatch.setattr(skills_routes, "_skills", {"skill-report": skill})
    monkeypatch.setattr(skills_routes, "_upsert_external_override", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(skills_routes, "reload_skill_cache", lambda *_args, **_kwargs: None)

    asyncio.run(skills_routes.uninstall_skill("skill-report"))

    needed = service.get_job(needed.id)
    other = service.get_job(other.id)
    assert needed.status == "paused"
    assert needed.pause_reason == "skill_unbound"
    assert other.status == "active"
    assert _pause_notes(needed.id)
    assert not _pause_notes(other.id)


def test_confirm_after_archive_pending_job_fails(isolated_db, tmp_path):
    """归档后 pending Job 不能确认，REST 返回 409。"""

    service = _service()
    project = _named_project(tmp_path, "Archive")
    pending = _weekly_job(service, name="归档后不可确认", project_slug=project["slug"])
    asyncio.run(archive_project_record(project["slug"]))
    pending = service.get_job(pending.id)
    assert pending.status == "pending_confirmation"

    with pytest.raises(ScheduledJobStateError, match="已归档"):
        service.confirm(pending.id)

    set_scheduled_jobs_enabled(True)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(confirm_scheduled_job(pending.id))
    assert exc_info.value.status_code == 409
    assert "已归档" in str(exc_info.value.detail)

    frozen = service.get_job(pending.id)
    assert frozen.status == "pending_confirmation"


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
    })
    monkeypatch.setattr(agents_routes, "_agents", {"agent-1": agent})
    monkeypatch.setattr(
        agents_routes,
        "get_registered_agent",
        lambda agent_id: agents_routes._agents.get(agent_id),
    )
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
    })
    monkeypatch.setattr(skills_routes, "_skills", {"skill-report": skill})
    monkeypatch.setattr(skills_routes, "_upsert_external_override", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(skills_routes, "reload_skill_cache", lambda *_args, **_kwargs: None)

    asyncio.run(skills_routes.uninstall_skill("skill-report"))

    needed = service.get_job(needed.id)
    assert needed.status == "paused"
    assert needed.pause_reason == "skill_unbound"
    assert _pause_notes(needed.id)


def test_mcp_catalog_delete_ignores_tool_scope_flag(isolated_db, monkeypatch):
    """只开过工具范围的任务不因删除 MCP 被暂停。"""

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
        },
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
    })
    monkeypatch.setattr(mcp_routes, "_mcp_servers", {"mcp-custom": server})
    monkeypatch.setattr(mcp_routes, "_builtin_mcp_servers", {})
    monkeypatch.setattr(mcp_routes, "_save_user_mcp_servers", lambda: None)

    asyncio.run(mcp_routes.delete_mcp_server("mcp-custom"))

    needed = service.get_job(needed.id)
    other = service.get_job(other.id)
    assert needed.status == "active"
    assert other.status == "active"
    assert _pause_notes(needed.id) == []


def test_http_tool_delete_ignores_tool_scope_flag(isolated_db):
    """只开过 HTTP 工具范围的任务，不因目录里删掉一个 HTTP 工具而暂停。"""

    service = _service()
    upsert_http_tool({
        "id": "http-a",
        "name": "HTTP A",
        "url": "https://example.com/api",
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
    assert needed.status == "active"
    assert _pause_notes(needed.id) == []


def test_http_tool_scope_flag_does_not_block_confirm(isolated_db):
    """工具范围上的 HTTP 开关不再要求租户里先有可配置 HTTP 工具。"""

    service = _service()
    needed = _confirm(
        service,
        _weekly_job(
            service,
            name="依赖 HTTP",
            tool_scope={"allow_http": True},
        ).id,
    )
    assert needed.status == "active"


def test_resume_after_mcp_catalog_delete_ignores_tool_scope_flag(isolated_db):
    """只开过 MCP 工具范围时，目录删空也不阻止恢复。"""

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
        },
    })
    job = _confirm(
        service,
        _weekly_job(service, name="MCP 周报", tool_scope={"allow_mcp": True}).id,
    )
    service.pause(job.id, reason="user")
    replace_mcp_servers({})

    resumed = service.resume(job.id)
    assert resumed.status == "active"


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
