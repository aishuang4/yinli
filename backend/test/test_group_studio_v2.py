"""群聊工作室 V2：进群即 open、mentions 队列、旧阶段接口 410。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import sqlite3

from src.group_studio.errors import (
    ALREADY_MEMBER,
    DEFAULT_AGENT_FORBIDDEN,
    HOST_REQUIRED,
    ILLEGAL_TRANSITION,
    NO_ELIGIBLE_SPEAKER,
    NO_LAST_TURN,
    STUDIO_BUSY,
    STUDIO_NOT_OPEN,
)
from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID
from src.group_studio.speaker_queue import (
    build_turn_queue,
    enqueue_handoff,
    get_wave_spoken,
    is_runnable_worker,
    note_wave_spoken,
    set_queue,
)
from src.group_studio.settings import set_group_studio_settings
from src.server.routes import group_studio, settings as settings_routes
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    ensure_db,
    get_group_studio_max_event_seq,
    insert_group_studio_message,
    insert_group_studio_run,
    list_group_studio_events_after,
    list_group_studio_messages,
    set_group_studio_current_run_id,
)


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    monkeypatch.setattr(
        "src.config.product_features.is_surface_enabled",
        lambda key: True,
    )
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


def _create(client: TestClient, session_id: str = "sess-v2", agent_id: str = "default") -> dict:
    response = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": session_id,
        "agentId": agent_id,
        "draftText": "这句只预填，不要落库",
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_from_chat_opens_without_host_or_first_message(client):
    body = _create(client)
    assert body["status"] == "open"
    assert body["pin"]["goal"] == ""
    assert all(item.get("role") != "host" for item in body["members"])
    assert body["messages"] == []
    assert "workItems" not in body
    assert "blackboard" not in body
    assert "awaitingIntakeConfirm" not in body
    messages = list_group_studio_messages(body["id"])
    assert messages == []


def test_legacy_discussing_status_normalizes_to_open(client):
    body = _create(client)
    with sqlite3.connect(database.DB_PATH) as conn:
        conn.execute("UPDATE group_studios SET status = 'discussing' WHERE id = ?", (body["id"],))
        conn.commit()
    snapshot = client.get(f"/api/group-studio/{body['id']}")
    assert snapshot.status_code == 200
    payload = snapshot.json()
    assert payload["status"] == "open"
    assert "workItems" not in payload
    assert "blackboard" not in payload
    assert "awaitingIntakeConfirm" not in payload
    assert "pin" in payload


def test_legacy_phase_routes_are_gone(client):
    body = _create(client)
    group_id = body["id"]
    for path in (
        f"/api/group-studio/{group_id}/confirm-intake",
        f"/api/group-studio/{group_id}/confirm-restatement",
        f"/api/group-studio/{group_id}/start-execution",
        f"/api/group-studio/{group_id}/start-review",
    ):
        response = client.post(path, json={"projectId": DEFAULT_PROJECT_ID})
        assert response.status_code == 410, path
        assert response.json()["detail"] == ILLEGAL_TRANSITION
        snapshot = client.get(f"/api/group-studio/{group_id}")
        assert snapshot.status_code == 200
        assert snapshot.json()["status"] == "open"


def test_host_sentinel_cannot_join(client):
    body = _create(client)
    response = client.post(f"/api/group-studio/{body['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": GROUP_STUDIO_HOST_AGENT_ID,
    })
    assert response.status_code == 409
    assert response.json()["detail"] == HOST_REQUIRED
    snapshot = client.get(f"/api/group-studio/{body['id']}")
    assert all(item.get("agentId") != GROUP_STUDIO_HOST_AGENT_ID for item in snapshot.json()["members"])


def test_empty_mentions_queues_workers_only(client, monkeypatch):
    monkeypatch.setattr(
        "src.group_studio.service._resolve_worker",
        lambda agent_id, _workspace_id: {"id": agent_id, "name": agent_id, "role": "chat"},
    )
    body = _create(client)
    first = client.post(f"/api/group-studio/{body['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "writer",
    })
    second = client.post(f"/api/group-studio/{body['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "reviewer",
    })
    assert first.status_code == 200 and second.status_code == 200
    posted = client.post(f"/api/group-studio/{body['id']}/messages", json={
        "projectId": DEFAULT_PROJECT_ID,
        "body": "大家都说一句",
        "mentions": [],
    })
    assert posted.status_code == 200, posted.text
    snapshot = client.get(f"/api/group-studio/{body['id']}").json()
    assert snapshot["speakerQueue"] == [first.json()["id"], second.json()["id"]]
    assert all(item["role"] != "host" for item in snapshot["messages"])
    assert all(item.get("role") != "host" for item in snapshot["members"])


def test_empty_room_message_does_not_schedule(client):
    body = _create(client)
    posted = client.post(f"/api/group-studio/{body['id']}/messages", json={
        "projectId": DEFAULT_PROJECT_ID,
        "body": "大家好",
        "mentions": [],
    })
    assert posted.status_code == 200, posted.text
    snapshot = client.get(f"/api/group-studio/{body['id']}")
    assert snapshot.status_code == 200
    roles = [item["role"] for item in snapshot.json()["messages"]]
    assert "human" in roles
    assert "system" in roles
    assert snapshot.json()["currentRunId"] is None


def test_mentions_ineligible_does_not_fallback(client):
    body = _create(client)
    posted = client.post(f"/api/group-studio/{body['id']}/messages", json={
        "projectId": DEFAULT_PROJECT_ID,
        "body": "点一个不存在的人",
        "mentions": ["gmbr_missing"],
    })
    assert posted.status_code == 200, posted.text
    texts = [item["body"] for item in list_group_studio_messages(body["id"])]
    assert any("不能发言" in str(item) for item in texts)
    events = list_group_studio_events_after(body["id"], 0)
    assert any(
        item["type"] == "GROUP_ERROR" and item["payload"].get("code") == NO_ELIGIBLE_SPEAKER
        for item in events
    )


def test_available_agents_excludes_seated_and_default(client, monkeypatch):
    class _Agent:
        def __init__(self, agent_id: str, name: str, role: str = "chat") -> None:
            self.id = agent_id
            self.name = name
            self.role = role
            self.description = name

    monkeypatch.setattr(
        "src.server.routes.agents.list_workspace_agents",
        lambda _workspace_id: [
            _Agent("default", "Assistant"),
            _Agent("writer", "写手"),
            _Agent("reviewer", "审稿"),
            _Agent("job-bot", "定时任务", role="job"),
        ],
    )
    monkeypatch.setattr(
        "src.group_studio.service._resolve_worker",
        lambda agent_id, _workspace_id: {"id": agent_id, "name": agent_id, "role": "chat"},
    )
    body = _create(client)
    listed = client.get(f"/api/group-studio/{body['id']}/available-agents")
    assert listed.status_code == 200, listed.text
    ids = {item["id"] for item in listed.json()}
    assert ids == {"writer", "reviewer"}

    added = client.post(f"/api/group-studio/{body['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "writer",
    })
    assert added.status_code == 200, added.text
    listed_after = client.get(f"/api/group-studio/{body['id']}/available-agents")
    assert {item["id"] for item in listed_after.json()} == {"reviewer"}


def test_default_agent_cannot_join(client):
    body = _create(client)
    response = client.post(f"/api/group-studio/{body['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "default",
    })
    assert response.status_code == 403
    assert response.json()["detail"] == DEFAULT_AGENT_FORBIDDEN


def test_add_member_and_already_member(client, monkeypatch):
    monkeypatch.setattr(
        "src.group_studio.service._resolve_worker",
        lambda agent_id, workspace_id: {"id": agent_id, "name": "写手", "role": "chat"},
    )
    body = _create(client)
    first = client.post(f"/api/group-studio/{body['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "writer",
    })
    assert first.status_code == 200, first.text
    assert first.json()["role"] == "worker"
    second = client.post(f"/api/group-studio/{body['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "writer",
    })
    assert second.status_code == 409
    assert second.json()["detail"] == ALREADY_MEMBER


def test_snapshot_current_speaker_follows_running_member(client, monkeypatch):
    monkeypatch.setattr(
        "src.group_studio.service._resolve_worker",
        lambda agent_id, _workspace_id: {"id": agent_id, "name": "写手", "role": "chat"},
    )
    body = _create(client)
    added = client.post(f"/api/group-studio/{body['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "writer",
    })
    assert added.status_code == 200, added.text
    member_id = added.json()["id"]
    insert_group_studio_run({
        "id": "grun_live",
        "groupId": body["id"],
        "kind": "discussion",
        "status": "running",
        "speakerMemberId": member_id,
    })
    set_group_studio_current_run_id(body["id"], "grun_live")
    snapshot = client.get(f"/api/group-studio/{body['id']}")
    assert snapshot.status_code == 200
    assert snapshot.json()["currentSpeakerMemberId"] == member_id
    assert snapshot.json()["currentRunId"] == "grun_live"


def test_close_requires_confirm_and_marks_done(client):
    body = _create(client)
    denied = client.post(f"/api/group-studio/{body['id']}/close", json={
        "projectId": DEFAULT_PROJECT_ID,
        "confirm": False,
    })
    assert denied.status_code == 409
    closed = client.post(f"/api/group-studio/{body['id']}/close", json={
        "projectId": DEFAULT_PROJECT_ID,
        "confirm": True,
    })
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "done"


def test_sse_default_after_seq_skips_history(client):
    body = _create(client)
    insert_group_studio_message({
        "id": "gmsg_hist",
        "groupId": body["id"],
        "speakerId": "human",
        "role": "human",
        "intent": "human_directive",
        "body": "历史句",
        "visibleInTimeline": True,
    })
    from src.group_studio.events import emit_envelope

    emit_envelope(group_id=body["id"], event_type="GROUP_PIN", payload={"goal": "x"})
    max_seq = get_group_studio_max_event_seq(body["id"])
    assert max_seq >= 1
    assert list_group_studio_events_after(body["id"], max_seq) == []


def test_build_turn_queue_rules():
    snapshot = {
        "members": [
            {"id": "a", "role": "worker", "status": "active", "muted": False, "joinedAt": "2026-01-01T00:00:02Z"},
            {"id": "b", "role": "worker", "status": "active", "muted": False, "joinedAt": "2026-01-01T00:00:01Z"},
            {"id": "c", "role": "worker", "status": "active", "muted": True, "joinedAt": "2026-01-01T00:00:00Z"},
            {"id": "h", "role": "host", "status": "active", "muted": False, "joinedAt": "2026-01-01T00:00:00Z"},
        ]
    }
    assert is_runnable_worker(snapshot["members"][3]) is False
    queue, kind = build_turn_queue(snapshot, [])
    assert kind == "all_round"
    assert queue == ["b", "a"]
    named, named_kind = build_turn_queue(snapshot, ["c", "a", "a", "missing"])
    assert named_kind == "assign"
    assert named == ["a"]
    empty, empty_kind = build_turn_queue(snapshot, ["c", "missing"])
    assert empty_kind == "assign"
    assert empty == []


def test_fresh_snapshot_has_null_last_turn(client):
    body = _create(client)
    assert body.get("lastTurn") is None
    assert body.get("canRerun") is False
    snapshot = client.get(f"/api/group-studio/{body['id']}")
    assert snapshot.status_code == 200
    payload = snapshot.json()
    assert payload["lastTurn"] is None
    assert payload["canRerun"] is False


def test_enqueue_handoff_appends_and_ignores_invalid(client, monkeypatch):
    monkeypatch.setattr(
        "src.group_studio.service._resolve_worker",
        lambda agent_id, _workspace_id: {"id": agent_id, "name": agent_id, "role": "chat"},
    )
    body = _create(client)
    first = client.post(f"/api/group-studio/{body['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "writer",
    })
    second = client.post(f"/api/group-studio/{body['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "reviewer",
    })
    third = client.post(f"/api/group-studio/{body['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "editor",
    })
    assert first.status_code == 200 and second.status_code == 200 and third.status_code == 200
    speaker = first.json()["id"]
    remaining = second.json()["id"]
    extra = third.json()["id"]
    group_id = body["id"]
    note_wave_spoken(group_id, speaker)
    set_queue(group_id, [remaining], kind="assign")
    assert enqueue_handoff(group_id, speaker, extra) is True
    assert client.get(f"/api/group-studio/{group_id}").json()["speakerQueue"] == [remaining, extra]
    assert enqueue_handoff(group_id, speaker, speaker) is False
    assert enqueue_handoff(group_id, speaker, remaining) is False
    muted = client.post(f"/api/group-studio/{group_id}/members/{extra}/mute", json={
        "projectId": DEFAULT_PROJECT_ID,
        "muted": True,
    })
    assert muted.status_code == 200
    set_queue(group_id, [remaining], kind="assign")
    assert enqueue_handoff(group_id, speaker, extra) is False
    assert client.get(f"/api/group-studio/{group_id}").json()["speakerQueue"] == [remaining]


def test_human_message_sets_last_turn_and_rerun(client, monkeypatch):
    monkeypatch.setattr(
        "src.group_studio.service._resolve_worker",
        lambda agent_id, _workspace_id: {"id": agent_id, "name": agent_id, "role": "chat"},
    )
    body = _create(client)
    first = client.post(f"/api/group-studio/{body['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "writer",
    })
    second = client.post(f"/api/group-studio/{body['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "reviewer",
    })
    assert first.status_code == 200 and second.status_code == 200
    left = first.json()["id"]
    right = second.json()["id"]
    posted = client.post(f"/api/group-studio/{body['id']}/messages", json={
        "projectId": DEFAULT_PROJECT_ID,
        "body": "请两位各说一次",
        "mentions": [left, right],
    })
    assert posted.status_code == 200, posted.text
    after_send = client.get(f"/api/group-studio/{body['id']}").json()
    assert after_send["lastTurn"] == {"kind": "assign", "mentions": [left, right]}
    assert after_send["speakerQueue"] == [left, right]
    assert after_send["canRerun"] is False
    assert all(item["role"] != "host" for item in after_send["messages"])

    busy = client.post(f"/api/group-studio/{body['id']}/rerun", json={"projectId": DEFAULT_PROJECT_ID})
    assert busy.status_code == 409
    assert busy.json()["detail"] == STUDIO_BUSY
    assert client.get(f"/api/group-studio/{body['id']}").json()["speakerQueue"] == [left, right]

    stopped = client.post(f"/api/group-studio/{body['id']}/interrupt", json={"projectId": DEFAULT_PROJECT_ID})
    assert stopped.status_code == 200
    ready = client.get(f"/api/group-studio/{body['id']}").json()
    assert ready["speakerQueue"] == []
    assert ready["canRerun"] is True
    assert ready["lastTurn"]["mentions"] == [left, right]
    count_before = len(ready["messages"])

    rerun = client.post(f"/api/group-studio/{body['id']}/rerun", json={"projectId": DEFAULT_PROJECT_ID})
    assert rerun.status_code == 200, rerun.text
    after = rerun.json()
    assert after["speakerQueue"] == [left, right]
    assert after["canRerun"] is False
    assert len(after["messages"]) == count_before
    assert all(item["role"] != "host" for item in after["messages"])


def test_rerun_guards(client, monkeypatch):
    monkeypatch.setattr(
        "src.group_studio.service._resolve_worker",
        lambda agent_id, _workspace_id: {"id": agent_id, "name": agent_id, "role": "chat"},
    )
    body = _create(client)
    missing = client.post(f"/api/group-studio/{body['id']}/rerun", json={"projectId": DEFAULT_PROJECT_ID})
    assert missing.status_code == 409
    assert missing.json()["detail"] == NO_LAST_TURN

    added = client.post(f"/api/group-studio/{body['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "writer",
    })
    assert added.status_code == 200
    member_id = added.json()["id"]
    posted = client.post(f"/api/group-studio/{body['id']}/messages", json={
        "projectId": DEFAULT_PROJECT_ID,
        "body": "你来说",
        "mentions": [member_id],
    })
    assert posted.status_code == 200
    client.post(f"/api/group-studio/{body['id']}/interrupt", json={"projectId": DEFAULT_PROJECT_ID})

    paused = client.post(f"/api/group-studio/{body['id']}/pause", json={"projectId": DEFAULT_PROJECT_ID})
    assert paused.status_code == 200
    denied = client.post(f"/api/group-studio/{body['id']}/rerun", json={"projectId": DEFAULT_PROJECT_ID})
    assert denied.status_code == 409
    assert denied.json()["detail"] == STUDIO_NOT_OPEN

    client.post(f"/api/group-studio/{body['id']}/resume", json={"projectId": DEFAULT_PROJECT_ID})
    muted = client.post(f"/api/group-studio/{body['id']}/members/{member_id}/mute", json={
        "projectId": DEFAULT_PROJECT_ID,
        "muted": True,
    })
    assert muted.status_code == 200
    empty = client.post(f"/api/group-studio/{body['id']}/rerun", json={"projectId": DEFAULT_PROJECT_ID})
    assert empty.status_code == 409
    assert empty.json()["detail"] == NO_ELIGIBLE_SPEAKER
    assert client.get(f"/api/group-studio/{body['id']}").json()["speakerQueue"] == []


def test_interrupt_clears_wave_spoken(client, monkeypatch):
    monkeypatch.setattr(
        "src.group_studio.service._resolve_worker",
        lambda agent_id, _workspace_id: {"id": agent_id, "name": agent_id, "role": "chat"},
    )
    body = _create(client)
    added = client.post(f"/api/group-studio/{body['id']}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "writer",
    })
    note_wave_spoken(body["id"], added.json()["id"])
    assert get_wave_spoken(body["id"]) == [added.json()["id"]]
    stopped = client.post(f"/api/group-studio/{body['id']}/interrupt", json={"projectId": DEFAULT_PROJECT_ID})
    assert stopped.status_code == 200
    assert get_wave_spoken(body["id"]) == []
