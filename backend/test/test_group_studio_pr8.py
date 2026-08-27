"""群聊工作室 PR 8：插话、复述、熔断与暂停入列。"""

from __future__ import annotations

import asyncio

from agentscope.event import UserConfirmResultEvent
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.group_studio.errors import (
    ILLEGAL_TRANSITION,
    NOT_AWAITING_RESTATEMENT,
    RESTATEMENT_MISSING,
)
from src.group_studio.host_runtime import run_host_discussion
from src.group_studio.settings import set_group_studio_settings
from src.group_studio.worker_runtime import run_worker_turn
from src.server.routes import group_studio, settings as settings_routes
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    ensure_db,
    get_group_studio_snapshot,
    insert_group_studio_message,
    list_group_studio_events_after,
    update_group_studio_state,
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


def _create_discussing(client: TestClient, session_id: str = "sess-pr8") -> dict:
    created = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": session_id,
        "agentId": "default",
        "draftText": "完成竞品分析",
    })
    assert created.status_code == 200, created.text
    return created.json()


class _FakeEvent:
    def __init__(self, **payload: object) -> None:
        self._payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        return dict(self._payload)


class _TalkAgent:
    async def reply_stream(self, _message: object):
        yield _FakeEvent(type="TEXT_BLOCK_DELTA", delta="先对齐目标。")
        yield _FakeEvent(type="REPLY_END", finished_reason="completed")


class _HitlAgent:
    async def reply_stream(self, message: object):
        if isinstance(message, UserConfirmResultEvent):
            yield _FakeEvent(type="TEXT_BLOCK_DELTA", delta="不该继续。")
            yield _FakeEvent(type="REPLY_END", finished_reason="completed")
            return
        yield _FakeEvent(
            type="REQUIRE_USER_CONFIRM",
            reply_id="reply-pr8",
            tool_calls=[{
                "id": "tc-1",
                "name": "Bash",
                "type": "tool_call",
                "input": "{\"command\": \"ls\"}",
            }],
        )


def test_human_message_and_paused_queue(client):
    studio = _create_discussing(client, "sess-msg")
    posted = client.post(f"/api/group-studio/{studio['id']}/messages", json={
        "projectId": DEFAULT_PROJECT_ID,
        "body": "请改成只做国内竞品",
    })
    assert posted.status_code == 200, posted.text
    assert posted.json()["role"] == "human"
    assert posted.json()["body"] == "请改成只做国内竞品"

    paused = client.post(f"/api/group-studio/{studio['id']}/pause", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    queued = client.post(f"/api/group-studio/{studio['id']}/messages", json={
        "projectId": DEFAULT_PROJECT_ID,
        "body": "暂停时只入列",
    })
    assert queued.status_code == 200
    snapshot = client.get(f"/api/group-studio/{studio['id']}").json()
    assert snapshot["status"] == "paused"
    assert snapshot["currentRunId"] is None
    bodies = [item["body"] for item in snapshot["messages"]]
    assert "暂停时只入列" in bodies

    resumed = client.post(f"/api/group-studio/{studio['id']}/resume", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "discussing"


def test_pause_cancels_pending_approval(client):
    studio = _create_discussing(client, "sess-pause-hitl")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    group_id = studio["id"]

    async def scenario() -> None:
        task = asyncio.create_task(
            run_worker_turn(group_id, worker["id"], agent_factory=lambda _spec: _HitlAgent()),
        )
        approval = None
        for _ in range(50):
            await asyncio.sleep(0.02)
            snapshot = get_group_studio_snapshot(group_id)
            approval = (snapshot or {}).get("currentApproval")
            if approval and approval.get("status") == "pending":
                break
        assert approval is not None
        paused = client.post(f"/api/group-studio/{group_id}/pause", json={
            "projectId": DEFAULT_PROJECT_ID,
        })
        assert paused.status_code == 200
        await task

    asyncio.run(scenario())
    snapshot = get_group_studio_snapshot(group_id)
    assert snapshot is not None
    assert snapshot["status"] == "paused"
    assert snapshot["currentRunId"] is None
    types = [item["type"] for item in list_group_studio_events_after(group_id, 0)]
    assert "GROUP_INTERRUPT" in types
    assert "GROUP_APPROVAL" in types


def test_interrupt_without_run_is_ok(client):
    studio = _create_discussing(client, "sess-stop")
    response = client.post(f"/api/group-studio/{studio['id']}/interrupt", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert response.status_code == 200


def test_confirm_restatement_guards(client, monkeypatch):
    studio = _create_discussing(client, "sess-restate")
    group_id = studio["id"]
    missing = client.post(f"/api/group-studio/{group_id}/confirm-restatement", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert missing.status_code == 409
    assert missing.json()["detail"] == NOT_AWAITING_RESTATEMENT

    monkeypatch.setattr("src.group_studio.service.group_studio_has_runs", lambda _gid: True)
    changed = client.put(f"/api/group-studio/{group_id}/task-card", json={
        "projectId": DEFAULT_PROJECT_ID,
        "goal": "改成全球竞品",
    })
    assert changed.status_code == 200
    snapshot = client.get(f"/api/group-studio/{group_id}").json()
    assert snapshot["status"] == "awaiting_human"
    assert snapshot["awaitingRestatement"] is True

    no_host = client.post(f"/api/group-studio/{group_id}/confirm-restatement", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert no_host.status_code == 409
    assert no_host.json()["detail"] == RESTATEMENT_MISSING

    host = next(item for item in snapshot["members"] if item["role"] == "host")
    insert_group_studio_message({
        "id": "gmsg_restate",
        "groupId": group_id,
        "speakerId": host["id"],
        "role": "host",
        "intent": "restatement",
        "body": "新目标是全球竞品分析。",
        "visibleInTimeline": True,
    })
    confirmed = client.post(f"/api/group-studio/{group_id}/confirm-restatement", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "discussing"
    assert confirmed.json()["awaitingRestatement"] is False


def test_max_turns_trips_circuit(client):
    studio = _create_discussing(client, "sess-circuit")
    update_group_studio_state(studio["id"], budget={
        "maxTurns": 1,
        "maxNoProgressTurns": 9,
        "usedTurns": 0,
        "noProgressTurns": 0,
    })
    asyncio.run(run_host_discussion(studio["id"], agent_factory=lambda _spec: _TalkAgent()))
    snapshot = get_group_studio_snapshot(studio["id"])
    assert snapshot is not None
    assert snapshot["status"] == "circuit_broken"
    assert snapshot["budget"]["usedTurns"] == 1
    types = [item["type"] for item in list_group_studio_events_after(studio["id"], 0)]
    assert "GROUP_CIRCUIT" in types
    assert "GROUP_BUDGET" in types
    blocked = client.post(f"/api/group-studio/{studio['id']}/messages", json={
        "projectId": DEFAULT_PROJECT_ID,
        "body": "熔断后不能推进",
    })
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == "studio_circuit_broken"


def test_no_progress_from_max_iters(client):
    studio = _create_discussing(client, "sess-noprog")
    update_group_studio_state(studio["id"], budget={
        "maxTurns": 40,
        "maxNoProgressTurns": 1,
        "usedTurns": 0,
        "noProgressTurns": 0,
    })

    class _IterAgent:
        async def reply_stream(self, _message: object):
            yield _FakeEvent(type="EXCEED_MAX_ITERS")

    asyncio.run(run_host_discussion(studio["id"], agent_factory=lambda _spec: _IterAgent()))
    snapshot = get_group_studio_snapshot(studio["id"])
    assert snapshot is not None
    assert snapshot["status"] == "circuit_broken"
    assert snapshot["budget"]["noProgressTurns"] >= 1


def test_resume_clears_no_progress(client):
    studio = _create_discussing(client, "sess-resume-noprog")
    update_group_studio_state(studio["id"], budget={
        "maxTurns": 40,
        "maxNoProgressTurns": 3,
        "usedTurns": 4,
        "noProgressTurns": 3,
    }, status="circuit_broken", phase_before_pause="discussing")
    resumed = client.post(f"/api/group-studio/{studio['id']}/resume", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "discussing"
    assert resumed.json()["budget"]["noProgressTurns"] == 0


def test_pause_twice_is_illegal(client):
    studio = _create_discussing(client, "sess-pause2")
    first = client.post(f"/api/group-studio/{studio['id']}/pause", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert first.status_code == 200
    second = client.post(f"/api/group-studio/{studio['id']}/pause", json={
        "projectId": DEFAULT_PROJECT_ID,
    })
    assert second.status_code == 409
    assert second.json()["detail"] == ILLEGAL_TRANSITION
