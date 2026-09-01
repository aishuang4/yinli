"""群聊工作室 PR13：FIFO 点名队列，工人之间不回 Host。"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.group_studio.host_runtime import run_host_discussion
from src.group_studio.settings import set_group_studio_settings
from src.group_studio.speaker_queue import get_queue
from src.group_studio.tools_host import GroupAssignFloor, HostToolContext
from src.group_studio.worker_runtime import run_worker_turn
from src.server.routes import group_studio, settings as settings_routes
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    ensure_db,
    get_group_studio_pending_speaker,
    get_group_studio_snapshot,
    list_group_studio_events_after,
    upsert_agent,
    upsert_project_resource_binding,
    DEFAULT_WORKSPACE_ID,
)
from test.group_studio_helpers import confirm_intake_for_tests


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


class _FakeEvent:
    def __init__(self, **payload: object) -> None:
        self._payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        return dict(self._payload)


class _TalkWorker:
    async def reply_stream(self, _message: object):
        yield _FakeEvent(type="TEXT_BLOCK_DELTA", delta="工人发言。")
        yield _FakeEvent(type="REPLY_END", finished_reason="completed")


def _create_discussing(client: TestClient, session_id: str) -> dict:
    created = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": session_id,
        "agentId": "default",
    })
    assert created.status_code == 200
    return confirm_intake_for_tests(client, created.json()["id"], goal="写研报")


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


def test_assign_member_ids_writes_fifo_queue(client):
    studio = _create_discussing(client, "sess-q-assign")
    group_id = studio["id"]
    first = next(item for item in studio["members"] if item["role"] == "worker")
    _bind_agent("agent-q2", "第二工人")
    added = client.post(f"/api/group-studio/{group_id}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "agent-q2",
    })
    assert added.status_code == 200, added.text
    second_id = added.json()["id"]
    ctx = HostToolContext(group_id=group_id, run_id="grun_q", host_member_id="host")
    asyncio.run(GroupAssignFloor(ctx)(memberIds=[first["id"], second_id]))
    assert get_queue(group_id) == [first["id"], second_id]
    assert get_group_studio_pending_speaker(group_id) == first["id"]
    events = list_group_studio_events_after(group_id, 0)
    queue_events = [item for item in events if item["type"] == "GROUP_SPEAKER_QUEUE"]
    assert queue_events
    assert queue_events[-1]["payload"]["kind"] == "assign"
    assert queue_events[-1]["payload"]["memberIds"] == [first["id"], second_id]


def test_workers_drain_queue_without_host(client):
    studio = _create_discussing(client, "sess-q-drain")
    group_id = studio["id"]
    first = next(item for item in studio["members"] if item["role"] == "worker")
    _bind_agent("agent-q2", "第二工人")
    second = client.post(f"/api/group-studio/{group_id}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "agent-q2",
    }).json()
    ctx = HostToolContext(group_id=group_id, run_id="grun_q2", host_member_id="host")
    asyncio.run(GroupAssignFloor(ctx)(memberIds=[first["id"], second["id"]]))
    asyncio.run(run_worker_turn(group_id, first["id"], agent_factory=lambda _spec: _TalkWorker()))
    assert get_queue(group_id) == [second["id"]]
    asyncio.run(run_worker_turn(group_id, second["id"], agent_factory=lambda _spec: _TalkWorker()))
    assert get_queue(group_id) == []
    snapshot = get_group_studio_snapshot(group_id)
    assert snapshot is not None
    workers = [item for item in snapshot["messages"] if item["role"] == "worker"]
    assert len(workers) >= 2


def test_human_message_clears_queue(client):
    studio = _create_discussing(client, "sess-q-human")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    ctx = HostToolContext(group_id=studio["id"], run_id="grun_h", host_member_id="host")
    asyncio.run(GroupAssignFloor(ctx)(memberId=worker["id"]))
    assert get_queue(studio["id"]) == [worker["id"]]
    posted = client.post(f"/api/group-studio/{studio['id']}/messages", json={
        "projectId": DEFAULT_PROJECT_ID,
        "body": "先停一下",
    })
    assert posted.status_code == 200
    assert get_queue(studio["id"]) == []
    events = list_group_studio_events_after(studio["id"], 0)
    kinds = [item["payload"].get("kind") for item in events if item["type"] == "GROUP_SPEAKER_QUEUE"]
    assert "interrupt" in kinds


def test_muted_head_is_skipped(client):
    studio = _create_discussing(client, "sess-q-mute")
    group_id = studio["id"]
    first = next(item for item in studio["members"] if item["role"] == "worker")
    _bind_agent("agent-q2", "第二工人")
    second = client.post(f"/api/group-studio/{group_id}/members", json={
        "projectId": DEFAULT_PROJECT_ID,
        "agentId": "agent-q2",
    }).json()
    ctx = HostToolContext(group_id=group_id, run_id="grun_m", host_member_id="host")
    asyncio.run(GroupAssignFloor(ctx)(memberIds=[first["id"], second["id"]]))
    muted = client.post(
        f"/api/group-studio/{group_id}/members/{first['id']}/mute",
        json={"projectId": DEFAULT_PROJECT_ID, "muted": True},
    )
    assert muted.status_code == 200, muted.text
    from src.group_studio.speaker_queue import pop_unrunnable_head

    pop_unrunnable_head(group_id)
    assert get_queue(group_id) == [second["id"]]


def test_assign_single_member_id_still_works(client):
    studio = _create_discussing(client, "sess-q-one")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    assigned = asyncio.run(run_host_discussion(
        studio["id"],
        agent_factory=lambda spec: _AssignOne(list(getattr(spec, "extra_tools", []) or []), worker["id"]),
    ))
    assert assigned == worker["id"]
    assert get_queue(studio["id"]) == [worker["id"]]


class _AssignOne:
    def __init__(self, tools: list, member_id: str) -> None:
        self._tools = tools
        self._member_id = member_id

    async def reply_stream(self, _message: object):
        tool = next(item for item in self._tools if item.name == "group_assign_floor")
        await tool(memberId=self._member_id)
        yield _FakeEvent(type="TEXT_BLOCK_DELTA", delta="请发言。")
        yield _FakeEvent(type="TOOL_RESULT_END", tool_call_name="group_assign_floor", state="success")
        yield _FakeEvent(type="REPLY_END", finished_reason="completed")
