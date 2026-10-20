"""Project / Agent / Skill 资源变更时立即暂停 Job。"""

from __future__ import annotations
from test.file_skill_fixtures import install_file_skills as replace_skills


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
    upsert_agent,
    upsert_http_tool,
    upsert_project_resource_binding,
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    """每个用例使用独立 SQLite。需要舊 Project 列的測試自行建立，不靠啟動引導。"""

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
    """Project 歸檔 API 已停用，不會反向暫停 Job。"""

    service = _service()
    pending = _weekly_job(service, name="待确认周报")
    active = _confirm(service, _weekly_job(service, name="已启用周报").id)
    pending_status = service.get_job(pending.id).status
    active_status = service.get_job(active.id).status

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(update_project_record(
            DEFAULT_PROJECT_ID,
            ProjectUpdateRequest(status="archived"),
        ))
    assert exc_info.value.status_code == 410
    assert service.get_job(pending.id).status == pending_status
    assert service.get_job(active.id).status == active_status
    assert not _pause_notes(pending.id)
    assert not _pause_notes(active.id)


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


def test_skill_binding_removal_does_not_push_state_into_jobs(isolated_db):
    """Project 解绑 Skill 不反向改 Job；firing 前置校验负责拒绝。"""

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

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(put_project_resource(
            DEFAULT_PROJECT_ID,
            "skill",
            "skill-report",
            ProjectResourceBindingRequest(enabled=False),
        ))
    assert exc_info.value.status_code == 410

    needed = service.get_job(needed.id)
    other = service.get_job(other.id)
    assert needed.status == "active"
    assert other.status == "active"
    assert not _pause_notes(needed.id)
    assert not _pause_notes(other.id)


def test_confirm_after_archive_pending_job_fails(isolated_db):
    """Project 更新 API 回 410，pending Job 仍可確認。"""

    service = _service()
    pending = _weekly_job(service, name="归档后不可确认")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(update_project_record(
            DEFAULT_PROJECT_ID,
            ProjectUpdateRequest(status="archived"),
        ))
    assert exc_info.value.status_code == 410

    confirmed = service.confirm(pending.id)
    assert confirmed.status == "active"


def test_update_agent_removing_skill_does_not_rewrite_frozen_jobs(isolated_db, monkeypatch):
    """Agent 后续配置变化不反向改写 Job 已冻结的 Skill 集合。"""

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
    assert needed.status == "active"
    assert other.status == "active"
    assert not _pause_notes(needed.id)


def test_skill_uninstall_does_not_scan_or_pause_jobs(isolated_db, monkeypatch):
    """Skill 资源变更不反向驱动 Job；Job 在自己的校验边界处理失效。"""

    service = _service()
    _ensure_skill("skill-report")
    needed = _confirm(
        service,
        _weekly_job(service, name="依赖卸载 Skill", skill_ids=["skill-report"]).id,
    )
    monkeypatch.setattr(
        ScheduledJobService,
        "list_jobs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("卸载 Skill 不得扫描 Job")),
    )

    asyncio.run(skills_routes.uninstall_skill("skill-report"))

    needed = service.get_job(needed.id)
    assert needed.status == "active"
    assert not _pause_notes(needed.id)


def test_mcp_catalog_delete_does_not_pause_until_resume(isolated_db, monkeypatch):
    """刪除 MCP 不掃描 Job；下一次 resume 才自檢失敗。"""

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
    replace_mcp_servers({})

    needed = service.get_job(needed.id)
    other = service.get_job(other.id)
    assert needed.status == "active"
    assert other.status == "active"
    assert not _pause_notes(needed.id)
    service.pause(needed.id, reason="user")
    with pytest.raises(ScheduledJobStateError, match="MCP"):
        service.resume(needed.id)


def test_http_tool_delete_does_not_pause_until_resume(isolated_db):
    """刪除 HTTP 工具不掃描 Job；resume 才自檢失敗。"""

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
    assert needed.status == "active"
    assert not _pause_notes(needed.id)
    service.pause(needed.id, reason="user")
    with pytest.raises(ScheduledJobStateError, match="HTTP"):
        service.resume(needed.id)


def test_session_grant_disable_fails_resume_without_pausing(isolated_db):
    """停用 Session grant 不改 Job 狀態；resume 自檢失敗。"""

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
    owner = database.get_scheduled_job(needed.id)["owner_session_id"]
    database.ensure_db()
    now = "2026-08-20T00:00:00+00:00"
    with database._lock, database._connect() as conn:
        conn.execute(
            """
            INSERT INTO session_resource_grants (
                session_id, resource_type, resource_id, enabled, config_override,
                grant_version, created_at, updated_at
            ) VALUES (?, 'http_tool', 'http-bound', 0, '{}', 1, ?, ?)
            """,
            (owner, now, now),
        )
        conn.commit()
    assert service.get_job(needed.id).status == "active"
    service.pause(needed.id, reason="user")
    with pytest.raises(ScheduledJobStateError, match="HTTP"):
        service.resume(needed.id)


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
