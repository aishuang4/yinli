"""群聊工作室 PR14：工作项、原子认领、认领轮。"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

pytestmark = pytest.mark.skip(reason="V1 阶段机已由 V2 替换，见 test_group_studio_v2.py")

from src.group_studio.errors import INTAKE_NOT_CONFIRMED, WORK_ITEM_CLAIMED
from src.group_studio.settings import set_group_studio_settings
from src.group_studio.tools_host import GroupPublishWorkItems, HostToolContext
from src.group_studio.tools_worker import acquire_worker_tool_context
from src.group_studio.workboard import claim_item, finalize_host_dispatch, get_claim_round
from src.server.routes import group_studio, settings as settings_routes
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    ensure_db,
    get_group_studio_snapshot,
    list_group_studio_events_after,
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


def _create_discussing(client: TestClient, session_id: str) -> dict:
    created = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": session_id,
        "agentId": "default",
    })
    assert created.status_code == 200
    return confirm_intake_for_tests(client, created.json()["id"], goal="写研报")


def test_publish_creates_open_items_and_claim_round(client):
    studio = _create_discussing(client, "sess-wb-pub")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    ctx = HostToolContext(group_id=studio["id"], run_id="grun_pub", host_member_id="host")
    asyncio.run(GroupPublishWorkItems(ctx)(items=[{"title": "基本面"}, {"title": "估值"}]))
    snapshot = get_group_studio_snapshot(studio["id"])
    assert snapshot is not None
    items = snapshot["workItems"]
    assert len(items) == 2
    assert all(item["status"] == "open" for item in items)
    finalize_host_dispatch(ctx, studio["id"])
    assert get_claim_round(studio["id"]) == [worker["id"]]
    events = list_group_studio_events_after(studio["id"], 0)
    assert any(item["type"] == "GROUP_WORK_ITEM" for item in events)


def test_atomic_claim_conflict(client):
    studio = _create_discussing(client, "sess-wb-claim")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    ctx = HostToolContext(group_id=studio["id"], run_id="grun_c", host_member_id="host")
    asyncio.run(GroupPublishWorkItems(ctx)(items=[{"title": "调研"}]))
    item_id = get_group_studio_snapshot(studio["id"])["workItems"][0]["id"]
    first = claim_item(studio["id"], item_id, member_id=worker["id"], run_id="grun_w1")
    assert first["status"] == "claimed"
    with pytest.raises(Exception) as exc:
        claim_item(studio["id"], item_id, member_id=worker["id"], run_id="grun_w2")
    assert exc.value.code == WORK_ITEM_CLAIMED


def test_bubble_i_will_do_it_is_not_a_claim(client):
    studio = _create_discussing(client, "sess-wb-chat")
    ctx = HostToolContext(group_id=studio["id"], run_id="grun_chat", host_member_id="host")
    asyncio.run(GroupPublishWorkItems(ctx)(items=[{"title": "调研"}]))
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    wctx = acquire_worker_tool_context(studio["id"], worker["id"], worker["agentId"])
    wctx.begin_run("grun_talk")
    # 只发消息不认领
    from src.group_studio.tools_worker import GroupPostMessage

    asyncio.run(GroupPostMessage(wctx)(body="我来做调研"))
    item = get_group_studio_snapshot(studio["id"])["workItems"][0]
    assert item["status"] == "open"


def test_publish_in_drafting_forbidden(client):
    created = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": "sess-wb-draft",
        "agentId": "default",
    })
    ctx = HostToolContext(group_id=created.json()["id"], run_id="grun_d", host_member_id="host")
    result = asyncio.run(GroupPublishWorkItems(ctx)(items=[{"title": "调研"}]))
    text = str(result.content)
    assert INTAKE_NOT_CONFIRMED in text


def test_assignee_auto_enqueues_no_claim_round(client):
    studio = _create_discussing(client, "sess-wb-asg")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    ctx = HostToolContext(group_id=studio["id"], run_id="grun_a", host_member_id="host")
    asyncio.run(GroupPublishWorkItems(ctx)(items=[{
        "title": "基本面",
        "assigneeMemberId": worker["id"],
    }]))
    finalize_host_dispatch(ctx, studio["id"])
    assert get_claim_round(studio["id"]) == []
    snapshot = get_group_studio_snapshot(studio["id"])
    assert snapshot["speakerQueue"] == [worker["id"]]
    assert snapshot["workItems"][0]["status"] == "claimed"
