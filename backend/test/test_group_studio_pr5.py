"""群聊工作室 PR 5：独立 SSE 与 Host 串行讨论。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.group_studio.host_runtime import build_host_runtime_spec, run_host_discussion
from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID
from src.group_studio.settings import set_group_studio_settings
from src.group_studio.tools_host import HOST_STOP_TOOLS
from src.server.routes import group_studio, settings as settings_routes
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    ensure_db,
    get_group_studio_snapshot,
    list_group_studio_events_after,
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


def _create_discussing(client: TestClient, session_id: str = "sess-pr5") -> dict:
    created = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": session_id,
        "agentId": "default",
    })
    assert created.status_code == 200
    group_id = created.json()["id"]
    updated = client.put(f"/api/group-studio/{group_id}/task-card", json={
        "projectId": DEFAULT_PROJECT_ID,
        "goal": "写一份竞品分析",
    })
    assert updated.status_code == 200
    return client.get(f"/api/group-studio/{group_id}").json()


class _FakeEvent:
    def __init__(self, **payload: object) -> None:
        self._payload = payload

    def model_dump(self, mode: str = "json") -> dict:
        return dict(self._payload)


class _FakeAgent:
    async def reply_stream(self, _message: object):
        yield _FakeEvent(type="TEXT_BLOCK_DELTA", delta="先对齐目标，")
        yield _FakeEvent(type="TEXT_BLOCK_DELTA", delta="然后请研究员发言。")
        yield _FakeEvent(type="REPLY_END", finished_reason="completed")


def test_sse_state_envelope_format(client):
    from src.group_studio.stream import format_sse

    studio = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": "sess-sse",
        "agentId": "default",
    }).json()
    text = format_sse({
        "seq": None,
        "groupId": studio["id"],
        "runId": None,
        "type": "GROUP_STUDIO_STATE",
        "status": studio["status"],
        "payload": studio,
        "createdAt": studio["createdAt"],
    })
    assert text.startswith("event: GROUP_STUDIO_STATE\n")
    assert studio["id"] in text
    routes = {getattr(route, "path", "") for route in client.app.routes}
    assert "/api/group-studio/{group_id}/events" in routes


def test_host_turn_persists_message_but_not_delta(client):
    import asyncio

    studio = _create_discussing(client)
    group_id = studio["id"]
    asyncio.run(run_host_discussion(group_id, agent_factory=lambda _spec: _FakeAgent()))
    snapshot = get_group_studio_snapshot(group_id)
    assert snapshot is not None
    messages = snapshot["messages"]
    assert messages
    assert "对齐目标" in messages[-1]["body"]
    assert messages[-1]["role"] == "host"
    persisted = list_group_studio_events_after(group_id, 0)
    types = [item["type"] for item in persisted]
    assert "GROUP_MESSAGE_DELTA" not in types
    assert "GROUP_MESSAGE_OPEN" in types
    assert "GROUP_MESSAGE_CLOSE" in types
    assert "GROUP_FLOOR" in types
    assert "GROUP_RUN_ENDED" in types


def test_host_spec_has_no_default_tools(client):
    studio = _create_discussing(client, "sess-spec")
    host = next(item for item in studio["members"] if item["role"] == "host")
    spec, _ctx, tools = build_host_runtime_spec(studio, host)
    assert spec.include_default_tools is False
    assert spec.parent_agent_id == GROUP_STUDIO_HOST_AGENT_ID
    assert spec.tool_name_denylist >= {"AskUser", "FinalAnswerStart"}
    names = {tool.name for tool in tools}
    assert "group_patch_blackboard" not in names
    assert "group_assign_floor" in names
    assert HOST_STOP_TOOLS <= names
    assert spec.model_options.max_react_iters == 16


def test_pytest_does_not_auto_start_host(client):
    studio = _create_discussing(client, "sess-noauto")
    snapshot = get_group_studio_snapshot(studio["id"])
    assert snapshot is not None
    assert snapshot["currentRunId"] is None
    assert snapshot["messages"] == []


def test_group_studio_runtime_does_not_import_leader_stream():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "group_studio"
    forbidden = ("leader_stream", "PENDING_INTERACTIONS", "src.server.routes.chat.hitl")
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden):
            offenders.append(path.name)
    assert offenders == []
