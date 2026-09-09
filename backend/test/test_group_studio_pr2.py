"""群聊工作室 PR 2：from-chat 绑定、SQLite 持久化、chat_run_active。"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.group_studio.errors import (
    AGENT_NOT_BOUND,
    CHAT_RUN_ACTIVE,
    CROSS_PROJECT_SESSION,
    JOB_AGENT_FORBIDDEN,
)
from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID
from src.group_studio.settings import set_group_studio_settings
from src.server.routes import group_studio, settings as settings_routes
from src.server.routes.chat.store import _append_user_message, _load_store
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    DEFAULT_WORKSPACE_ID,
    archive_group_studio,
    create_project,
    ensure_db,
    get_active_group_studio_by_chat_session,
    load_chat_store,
    upsert_agent,
    upsert_chat_run,
    upsert_project_resource_binding,
)

RECORDS_PATH = Path(__file__).resolve().parents[1] / "src" / "storage" / "group_studio_records.py"


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


def _from_chat(client: TestClient, **overrides) -> object:
    payload = {
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": "sess-empty",
        "agentId": "default",
    }
    payload.update(overrides)
    return client.post("/api/group-studio/from-chat", json=payload)


def test_records_do_not_insert_chat_sessions():
    text = RECORDS_PATH.read_text(encoding="utf-8")
    assert "INSERT INTO chat_sessions" not in text
    assert "INSERT INTO chat_messages" not in text


def test_empty_draft_creates_drafting_studio(client):
    response = _from_chat(client, chatSessionId="sess-empty")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "drafting"
    assert body["taskCard"]["goal"] == ""
    assert body["taskCard"]["status"] == "draft"
    assert body["taskCard"]["seedFromChat"] is False
    assert body["chatSessionId"] == "sess-empty"
    assert body["currentRunId"] is None
    assert body["messages"] == []
    host = next(member for member in body["members"] if member["role"] == "host")
    worker = next(member for member in body["members"] if member["role"] == "worker")
    assert host["agentId"] == GROUP_STUDIO_HOST_AGENT_ID
    assert worker["agentId"] == "default"
    store = load_chat_store()
    session_ids = {item["id"] for item in store["sessions"]}
    assert "sess-empty" in session_ids


def test_empty_draft_is_hidden_from_history_list(client):
    created = _from_chat(client, chatSessionId="sess-blank-hidden")
    assert created.status_code == 200
    listed = client.get("/api/group-studio", params={"projectId": DEFAULT_PROJECT_ID})
    ids = [item["id"] for item in listed.json()["studios"]]
    assert created.json()["id"] not in ids


def test_reuse_blank_draft_returns_existing(client):
    first = _from_chat(client, chatSessionId="sess-blank-a")
    second = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": "sess-blank-b",
        "agentId": "default",
        "reuseBlankDraft": True,
    })
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


def test_draft_text_stays_drafting_and_seeds_message(client):
    response = _from_chat(client, chatSessionId="sess-draft", draftText="  写一份方案  ")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "drafting"
    assert body["taskCard"]["goal"] == ""
    assert body["taskCard"]["status"] == "draft"
    assert body["taskCard"]["seedFromChat"] is False
    bodies = [item["body"] for item in body["messages"] if item.get("visibleInTimeline")]
    assert "写一份方案" in bodies


def test_existing_messages_seed_from_last_user(client):
    _append_user_message(
        session_id="sess-talk",
        agent_id="default",
        agent_name="默认助手",
        mode="agent",
        model_name="m",
        content="请拆解这个任务",
        images=[],
        workspace_id=DEFAULT_WORKSPACE_ID,
        project_id=DEFAULT_PROJECT_ID,
    )
    response = _from_chat(client, chatSessionId="sess-talk", draftText="这段草稿应被忽略")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "drafting"
    assert body["taskCard"]["goal"] == ""
    assert body["taskCard"]["seedFromChat"] is True
    bodies = [item["body"] for item in body["messages"] if item.get("visibleInTimeline")]
    assert "请拆解这个任务" in bodies
    assert "这段草稿应被忽略" not in bodies


def test_second_from_chat_returns_same_studio(client):
    first = _from_chat(client, chatSessionId="sess-same")
    second = _from_chat(client, chatSessionId="sess-same", draftText="再次升级")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["taskCard"]["goal"] == ""


def test_archived_session_can_open_new_studio(client):
    first = _from_chat(client, chatSessionId="sess-arch")
    group_id = first.json()["id"]
    archive_group_studio(group_id)
    assert get_active_group_studio_by_chat_session("sess-arch") is None

    second = _from_chat(client, chatSessionId="sess-arch", draftText="新目标")
    assert second.status_code == 200
    assert second.json()["id"] != group_id
    assert second.json()["status"] == "drafting"


def test_get_and_by_chat(client):
    created = _from_chat(client, chatSessionId="sess-get", draftText="目标")
    group_id = created.json()["id"]

    fetched = client.get(f"/api/group-studio/{group_id}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == group_id
    assert fetched.json()["projectId"] == DEFAULT_PROJECT_ID

    by_chat = client.get("/api/group-studio/by-chat/sess-get")
    assert by_chat.status_code == 200
    assert by_chat.json()["id"] == group_id

    missing = client.get("/api/group-studio/by-chat/no-such")
    assert missing.status_code == 200
    assert missing.json() is None

    unknown = client.get("/api/group-studio/gstu_missing")
    assert unknown.status_code == 404


def test_chat_run_active_includes_waiting(client):
    upsert_chat_run({
        "id": "run-wait",
        "sessionId": "sess-wait",
        "status": "waiting_permission",
        "workspaceId": DEFAULT_WORKSPACE_ID,
        "projectId": DEFAULT_PROJECT_ID,
    })
    response = _from_chat(client, chatSessionId="sess-wait")

    assert response.status_code == 409
    assert response.json()["detail"] == CHAT_RUN_ACTIVE


def test_cross_project_session_rejected(client, tmp_path):
    first = _from_chat(client, chatSessionId="sess-cross")
    assert first.status_code == 200
    other_root = tmp_path / "other-project"
    other_root.mkdir()
    other = create_project({
        "id": "other-project",
        "workspaceId": DEFAULT_WORKSPACE_ID,
        "name": "另一个项目",
        "rootPath": str(other_root),
    })
    response = _from_chat(
        client,
        chatSessionId="sess-cross",
        projectId=other["id"],
    )
    assert response.status_code == 422
    assert response.json()["detail"] == CROSS_PROJECT_SESSION


def test_job_agent_forbidden(client):
    upsert_agent({
        "id": "job-1",
        "name": "Job",
        "system_prompt": "prompt",
        "workspaceId": DEFAULT_WORKSPACE_ID,
        "status": "idle",
        "role": "job",
    })
    response = _from_chat(client, chatSessionId="sess-job", agentId="job-1")
    assert response.status_code == 403
    assert response.json()["detail"] == JOB_AGENT_FORBIDDEN


def test_unbound_agent_is_allowed_within_workspace(client):
    upsert_agent({
        "id": "agent-unbound",
        "name": "未绑定",
        "system_prompt": "prompt",
        "workspaceId": DEFAULT_WORKSPACE_ID,
        "status": "idle",
        "role": "chat",
    })
    response = _from_chat(client, chatSessionId="sess-unbound", agentId="agent-unbound")
    assert response.status_code == 200
    assert any(member["agentId"] == "agent-unbound" for member in response.json()["members"])


def test_bound_agent_becomes_first_worker(client):
    upsert_agent({
        "id": "agent-1",
        "name": "研究员",
        "system_prompt": "prompt",
        "workspaceId": DEFAULT_WORKSPACE_ID,
        "status": "idle",
        "role": "chat",
    })
    upsert_project_resource_binding({
        "project_id": DEFAULT_PROJECT_ID,
        "resource_type": "agent",
        "resource_id": "agent-1",
        "enabled": True,
    })
    response = _from_chat(client, chatSessionId="sess-agent", agentId="agent-1")
    assert response.status_code == 200
    worker = next(member for member in response.json()["members"] if member["role"] == "worker")
    assert worker["agentId"] == "agent-1"
    assert worker["displayName"] == "研究员"


def test_workspace_dirs_created(client):
    from src.storage.database import get_project

    response = _from_chat(client, chatSessionId="sess-dirs")
    group_id = response.json()["id"]
    project = get_project(DEFAULT_PROJECT_ID)
    root = Path(str(project["rootPath"])) / ".agent-workstation" / "group-studios" / group_id
    assert (root / "inputs").is_dir()
    assert (root / "workspace").is_dir()
    assert (root / "outputs").is_dir()


def test_ensure_chat_session_used_not_sql(client):
    response = _from_chat(client, chatSessionId="sess-ensure")
    assert response.status_code == 200
    store = _load_store()
    session = next(item for item in store.sessions if item.id == "sess-ensure")
    assert session.kind == "chat"
    assert session.project_id == DEFAULT_PROJECT_ID
