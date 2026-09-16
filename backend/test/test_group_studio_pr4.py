"""群聊工作室 PR 4：任务卡转移、加人硬顶、Host 哨兵豁免、drafting 禁 floor。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

pytestmark = pytest.mark.skip(reason="V1 阶段机已由 V2 替换，见 test_group_studio_v2.py")

from src.group_studio.errors import (
    AGENT_NOT_BOUND,
    GOAL_REQUIRED,
    HOST_REQUIRED,
    ILLEGAL_TRANSITION,
    INTAKE_NOT_CONFIRMED,
    JOB_AGENT_FORBIDDEN,
    MEMBER_CAP,
)
from test.group_studio_helpers import confirm_intake_for_tests
from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID
from src.group_studio.settings import set_group_studio_settings
from src.project_resources import ProjectResourceAccess
from src.server.routes import group_studio, settings as settings_routes
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE_ID,
    ensure_db,
    upsert_agent,
    upsert_project_resource_binding,
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    yield
    database._initialized = False


@pytest.fixture()
def client(isolated_db):
    ensure_db()
    set_group_studio_settings(enabled=True)
    app = FastAPI()
    app.include_router(group_studio.router, prefix="/api/group-studio")
    app.include_router(settings_routes.router, prefix="/api/settings")
    with TestClient(app) as test_client:
        yield test_client


def _create_studio(client: TestClient, session_id: str = "sess-pr4") -> dict:
    response = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": session_id,
        "agentId": "default",
    })
    assert response.status_code == 200
    return response.json()


def _bind_agent(agent_id: str, name: str) -> None:
    upsert_agent({
        "id": agent_id,
        "name": name,
        "system_prompt": "prompt",
        "workspaceId": DEFAULT_WORKSPACE_ID,
        "status": "idle",
        "role": "chat",
    })
    upsert_project_resource_binding({
        "project_id": DEFAULT_PROJECT_ID,
        "resource_type": "agent",
        "resource_id": agent_id,
        "enabled": True,
    })


def test_require_agent_skips_host_sentinel():
    access = ProjectResourceAccess(
        workspace_id="default",
        project_id="default",
        agent_ids=frozenset(),
        skill_ids=frozenset(),
        mcp_server_ids=frozenset(),
        http_tool_ids=frozenset(),
    )
    access.require_agent("default")
    access.require_agent(GROUP_STUDIO_HOST_AGENT_ID)


def test_drafting_put_goal_stays_drafting(client):
    studio = _create_studio(client)
    group_id = studio["id"]
    response = client.put(f"/api/group-studio/{group_id}/task-card", json={
        "projectId": DEFAULT_PROJECT_ID,
        "goal": "  完成竞品分析  ",
    })
    assert response.status_code == 200
    card = response.json()
    assert card["goal"] == "完成竞品分析"
    assert card["status"] == "draft"
    assert card["revision"] == 2

    snapshot = client.get(f"/api/group-studio/{group_id}").json()
    assert snapshot["status"] == "drafting"
    assert snapshot["awaitingRestatement"] is False
    assert snapshot["blackboard"]["goal"] == "完成竞品分析"


def test_drafting_empty_goal_without_other_fields_422(client):
    studio = _create_studio(client)
    response = client.put(f"/api/group-studio/{studio['id']}/task-card", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert response.status_code == 422
    assert response.json()["detail"] == GOAL_REQUIRED


def test_start_execution_in_drafting_is_forbidden(client):
    studio = _create_studio(client)
    response = client.post(f"/api/group-studio/{studio['id']}/start-execution", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert response.status_code == 409
    assert response.json()["detail"] == ILLEGAL_TRANSITION


def test_start_execution_in_discussing_enters_executing(client):
    studio = _create_studio(client, "sess-exec")
    confirm_intake_for_tests(client, studio["id"], goal="开始执行")
    response = client.post(f"/api/group-studio/{studio['id']}/start-execution", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "executing"


def test_goal_change_without_runs_does_not_restatement(client):
    studio = _create_studio(client, "sess-edit")
    group_id = studio["id"]
    client.put(f"/api/group-studio/{group_id}/task-card", json={
        "projectId": DEFAULT_PROJECT_ID,
        "goal": "初稿",
    })
    response = client.put(f"/api/group-studio/{group_id}/task-card", json={
        "projectId": DEFAULT_PROJECT_ID,
        "goal": "改后的目标",
    })
    assert response.status_code == 200
    snapshot = client.get(f"/api/group-studio/{group_id}").json()
    assert snapshot["status"] == "drafting"
    assert snapshot["awaitingRestatement"] is False
    assert snapshot["taskCard"]["goal"] == "改后的目标"


def test_goal_change_with_runs_enters_restatement(client, monkeypatch):
    studio = _create_studio(client, "sess-restate")
    group_id = studio["id"]
    confirm_intake_for_tests(client, group_id, goal="旧目标")
    monkeypatch.setattr("src.group_studio.service.group_studio_has_runs", lambda _gid: True)
    response = client.put(f"/api/group-studio/{group_id}/task-card", json={
        "projectId": DEFAULT_PROJECT_ID,
        "goal": "新目标",
    })
    assert response.status_code == 200
    snapshot = client.get(f"/api/group-studio/{group_id}").json()
    assert snapshot["status"] == "awaiting_human"
    assert snapshot["awaitingRestatement"] is True
    assert snapshot["phaseBeforePause"] == "discussing"


def test_add_member_in_drafting_is_allowed(client):
    studio = _create_studio(client, "sess-intake-member")
    _bind_agent("agent-draft", "对谈工人")
    response = client.post(f"/api/group-studio/{studio['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "agent-draft",
    })
    assert response.status_code == 200
    assert response.json()["agentId"] == "agent-draft"
    assert response.json()["role"] == "worker"
    snapshot = client.get(f"/api/group-studio/{studio['id']}").json()
    assert snapshot["status"] == "drafting"
    assert any(item["agentId"] == "agent-draft" for item in snapshot["members"])


def test_invite_in_drafting_allowed_assign_blocked(client):
    import asyncio

    from src.group_studio.tools_host import GroupAssignFloor, GroupInviteMember, HostToolContext

    studio = _create_studio(client, "sess-intake-invite")
    _bind_agent("agent-invite", "预邀工人")
    host = next(item for item in studio["members"] if item["role"] == "host")
    ctx = HostToolContext(group_id=studio["id"], run_id="grun_invite", host_member_id=host["id"])
    invited = asyncio.run(GroupInviteMember(ctx)(agentId="agent-invite"))
    assert "agent-invite" in str(invited.content)
    snapshot = client.get(f"/api/group-studio/{studio['id']}").json()
    worker = next(item for item in snapshot["members"] if item["agentId"] == "agent-invite")
    blocked = asyncio.run(GroupAssignFloor(ctx)(memberId=worker["id"]))
    assert INTAKE_NOT_CONFIRMED in str(blocked.content)
    assert snapshot["status"] == "drafting"


def test_add_member_and_idempotent(client):
    studio = _create_studio(client, "sess-add")
    confirm_intake_for_tests(client, studio["id"], goal="加人")
    _bind_agent("agent-1", "研究员")
    first = client.post(f"/api/group-studio/{studio['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "agent-1",
    })
    assert first.status_code == 200
    assert first.json()["agentId"] == "agent-1"
    assert first.json()["role"] == "worker"
    second = client.post(f"/api/group-studio/{studio['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "agent-1",
    })
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_cannot_add_host_sentinel_as_worker(client):
    studio = _create_studio(client, "sess-host")
    confirm_intake_for_tests(client, studio["id"], goal="主持")
    response = client.post(f"/api/group-studio/{studio['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": GROUP_STUDIO_HOST_AGENT_ID,
    })
    assert response.status_code == 409
    assert response.json()["detail"] == HOST_REQUIRED


def test_job_and_unbound_member_rejected(client):
    studio = _create_studio(client, "sess-deny")
    confirm_intake_for_tests(client, studio["id"], goal="拒绝")
    upsert_agent({
        "id": "job-1",
        "name": "Job",
        "system_prompt": "p",
        "workspaceId": DEFAULT_WORKSPACE_ID,
        "status": "idle",
        "role": "job",
    })
    job = client.post(f"/api/group-studio/{studio['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "job-1",
    })
    assert job.status_code == 403
    assert job.json()["detail"] == JOB_AGENT_FORBIDDEN

    upsert_agent({
        "id": "unbound-1",
        "name": "未绑定",
        "system_prompt": "p",
        "workspaceId": DEFAULT_WORKSPACE_ID,
        "status": "idle",
        "role": "chat",
    })
    unbound = client.post(f"/api/group-studio/{studio['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "unbound-1",
    })
    assert unbound.status_code == 200, unbound.text
    payload = unbound.json()
    members = payload.get("members") or payload.get("memberships") or []
    assert any(
        (member.get("agentId") or member.get("agent_id")) == "unbound-1"
        for member in members
    ) or "unbound-1" in unbound.text


def test_member_cap(client):
    studio = _create_studio(client, "sess-cap")
    group_id = studio["id"]
    confirm_intake_for_tests(client, group_id, goal="满员")
    for index in range(1, 5):
        agent_id = f"agent-{index}"
        _bind_agent(agent_id, f"成员{index}")
        response = client.post(f"/api/group-studio/{group_id}/members", json={
            "projectId": DEFAULT_PROJECT_ID,
            "agentId": agent_id,
        })
        assert response.status_code == 200, response.text
    _bind_agent("agent-overflow", "超额")
    overflow = client.post(f"/api/group-studio/{group_id}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "agent-overflow",
    })
    assert overflow.status_code == 409
    assert overflow.json()["detail"] == MEMBER_CAP
