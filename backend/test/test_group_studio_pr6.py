"""群聊工作室 PR 6：黑板合并、静音/移除、共享文件库。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.group_studio.blackboard import merge_blackboard
from src.group_studio.errors import (
    GroupStudioError,
    HOST_REQUIRED,
    INVALID_UPLOAD,
    OPEN_QUESTIONS_DROPPED,
)
from src.group_studio.files import _reject_illegal_filename
from src.group_studio.host_runtime import build_host_runtime_spec
from src.group_studio.settings import set_group_studio_settings
from src.group_studio.tools_host import GroupPatchBlackboard, HostToolContext
from src.server.routes import group_studio, settings as settings_routes
from src.storage import database
from src.storage.database import DEFAULT_PROJECT_ID, ensure_db, list_group_studio_events_after


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


def _create_studio(client: TestClient, session_id: str = "sess-pr6") -> dict:
    response = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": session_id,
        "agentId": "default",
        "draftText": "完成竞品分析",
    })
    assert response.status_code == 200, response.text
    return response.json()


def _host_and_worker(studio: dict) -> tuple[dict, dict]:
    host = next(item for item in studio["members"] if item["role"] == "host")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    return host, worker


def test_merge_open_questions_drop_is_rejected():
    current = {
        "goal": "完成竞品分析",
        "constraints": [],
        "decisions": [],
        "openQuestions": [
            {"id": "gqst_keep", "text": "谁来写？", "askedBy": "host", "resolved": False, "resolution": ""},
            {"id": "gqst_done", "text": "已确认格式", "askedBy": "host", "resolved": True, "resolution": "表格"},
        ],
        "artifacts": [],
        "taskCardId": "gtask_1",
    }
    with pytest.raises(GroupStudioError) as exc:
        merge_blackboard(current, {"openQuestions": []})
    assert exc.value.status_code == 422
    assert exc.value.code == OPEN_QUESTIONS_DROPPED


def test_merge_keeps_unresolved_and_can_drop_resolved():
    current = {
        "goal": "g",
        "openQuestions": [
            {"id": "gqst_keep", "text": "范围？", "askedBy": "host", "resolved": False, "resolution": ""},
            {"id": "gqst_old", "text": "旧问题", "askedBy": "host", "resolved": True, "resolution": "ok"},
        ],
        "decisions": [],
        "artifacts": [],
        "taskCardId": "t",
    }
    merged = merge_blackboard(current, {
        "openQuestions": [
            {"id": "gqst_keep", "text": "范围？", "askedBy": "host", "resolved": False, "resolution": ""},
            {"id": "gqst_new", "text": "截止日？", "askedBy": "host", "resolved": False, "resolution": ""},
        ],
        "addDecisions": [{"text": "先出提纲"}],
    })
    ids = {item["id"] for item in merged["openQuestions"]}
    assert ids == {"gqst_keep", "gqst_new"}
    assert merged["decisions"][0]["text"] == "先出提纲"


def test_host_tool_includes_patch_blackboard(client):
    studio = _create_studio(client, "sess-tools")
    host, _worker = _host_and_worker(studio)
    _spec, _ctx, tools = build_host_runtime_spec(studio, host)
    names = {tool.name for tool in tools}
    assert "group_patch_blackboard" in names


def test_host_patch_then_drop_open_questions(client):
    import asyncio

    studio = _create_studio(client, "sess-board")
    ctx = HostToolContext(group_id=studio["id"], run_id="", host_member_id="host")
    tool = GroupPatchBlackboard(ctx)
    added = asyncio.run(tool(addQuestions=[{"text": "谁来写综述？"}]))
    assert "blackboard" in str(added.content)
    snapshot = client.get(f"/api/group-studio/{studio['id']}").json()
    questions = snapshot["blackboard"]["openQuestions"]
    assert len(questions) == 1
    dropped = asyncio.run(tool(openQuestions=[]))
    assert "open_questions_dropped" in str(dropped.content)
    after = client.get(f"/api/group-studio/{studio['id']}").json()
    assert len(after["blackboard"]["openQuestions"]) == 1
    events = list_group_studio_events_after(studio["id"], 0)
    assert any(item["type"] == "GROUP_BLACKBOARD" for item in events)


def test_mute_and_remove_host_required(client):
    studio = _create_studio(client, "sess-mute")
    host, worker = _host_and_worker(studio)
    muted_host = client.post(
        f"/api/group-studio/{studio['id']}/members/{host['id']}/mute",
        json={"projectId": DEFAULT_PROJECT_ID, "muted": True},
    )
    assert muted_host.status_code == 409
    assert muted_host.json()["detail"] == HOST_REQUIRED

    removed_host = client.post(
        f"/api/group-studio/{studio['id']}/members/{host['id']}/remove",
        json={"projectId": DEFAULT_PROJECT_ID},
    )
    assert removed_host.status_code == 409
    assert removed_host.json()["detail"] == HOST_REQUIRED

    muted = client.post(
        f"/api/group-studio/{studio['id']}/members/{worker['id']}/mute",
        json={"projectId": DEFAULT_PROJECT_ID, "muted": True},
    )
    assert muted.status_code == 200, muted.text
    assert muted.json()["muted"] is True
    assert muted.json()["role"] == "worker"

    unmuted = client.post(
        f"/api/group-studio/{studio['id']}/members/{worker['id']}/mute",
        json={"projectId": DEFAULT_PROJECT_ID, "muted": False},
    )
    assert unmuted.status_code == 200
    assert unmuted.json()["muted"] is False

    removed = client.post(
        f"/api/group-studio/{studio['id']}/members/{worker['id']}/remove",
        json={"projectId": DEFAULT_PROJECT_ID},
    )
    assert removed.status_code == 200
    assert removed.json()["status"] == "removed"
    snapshot = client.get(f"/api/group-studio/{studio['id']}").json()
    worker_row = next(item for item in snapshot["members"] if item["id"] == worker["id"])
    assert worker_row["status"] == "removed"


def test_upload_and_list_files(client, tmp_path, monkeypatch):
    from src.storage.database import update_project

    project_root = tmp_path / "project"
    project_root.mkdir()
    update_project(DEFAULT_PROJECT_ID, {"rootPath": str(project_root)})
    studio = _create_studio(client, "sess-files")
    uploaded = client.post(
        f"/api/group-studio/{studio['id']}/files",
        params={"projectId": DEFAULT_PROJECT_ID},
        files={"file": ("brief.txt", b"hello group", "text/plain")},
    )
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["title"] == "brief.txt"
    assert body["byteSize"] == len(b"hello group")
    assert "group-studios" in body["path"]
    listed = client.get(f"/api/group-studio/{studio['id']}/files")
    assert listed.status_code == 200
    files = listed.json()["files"]
    assert len(files) == 1
    assert files[0]["id"] == body["id"]
    snapshot = client.get(f"/api/group-studio/{studio['id']}").json()
    assert body["id"] in snapshot["blackboard"]["artifacts"]
    events = list_group_studio_events_after(studio["id"], 0)
    assert any(item["type"] == "GROUP_FILE" for item in events)
    saved = project_root / body["path"]
    assert saved.is_file()
    assert saved.read_bytes() == b"hello group"


def test_upload_rejects_illegal_path_and_oversize(client, tmp_path, monkeypatch):
    from src.storage.database import update_project

    project_root = tmp_path / "project"
    project_root.mkdir()
    update_project(DEFAULT_PROJECT_ID, {"rootPath": str(project_root)})
    studio = _create_studio(client, "sess-badfile")
    illegal = client.post(
        f"/api/group-studio/{studio['id']}/files",
        params={"projectId": DEFAULT_PROJECT_ID},
        files={"file": ("../secret.txt", b"nope", "text/plain")},
    )
    assert illegal.status_code == 422
    assert illegal.json()["detail"] == INVALID_UPLOAD

    monkeypatch.setattr(
        "src.group_studio.files.MAX_STUDIO_UPLOAD_BYTES",
        8,
    )
    oversize = client.post(
        f"/api/group-studio/{studio['id']}/files",
        params={"projectId": DEFAULT_PROJECT_ID},
        files={"file": ("big.txt", b"0123456789", "text/plain")},
    )
    assert oversize.status_code == 422
    assert oversize.json()["detail"] == INVALID_UPLOAD


def test_illegal_filename_rejected():
    with pytest.raises(GroupStudioError) as exc:
        _reject_illegal_filename("../secret.txt")
    assert exc.value.code == INVALID_UPLOAD
    with pytest.raises(GroupStudioError):
        _reject_illegal_filename("dir/note.txt")
