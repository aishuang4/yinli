"""群聊工作室 PR 7：Worker 串行执行与独立审批。"""

from __future__ import annotations

import asyncio

from agentscope.event import UserConfirmResultEvent
from agentscope.permission import PermissionMode
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.group_studio.errors import APPROVAL_NOT_PENDING, ILLEGAL_TRANSITION, MODEL_NOT_CONFIGURED
from src.group_studio.host_runtime import run_host_discussion
from src.group_studio.settings import set_group_studio_settings
from src.group_studio.worker_runtime import build_worker_runtime_spec, run_worker_turn
from src.server.routes import group_studio, settings as settings_routes
from src.storage import database
from src.storage.database import (
    DEFAULT_PROJECT_ID,
    ensure_db,
    get_group_studio_snapshot,
    insert_group_studio_approval,
    insert_group_studio_run,
    list_group_studio_events_after,
    set_group_studio_current_run_id,
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


def _create_discussing(client: TestClient, session_id: str = "sess-pr7") -> dict:
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
        yield _FakeEvent(type="TEXT_BLOCK_DELTA", delta="建议先列提纲。")
        yield _FakeEvent(type="TOOL_RESULT_END", tool_call_name="group_post_message", state="success")
        yield _FakeEvent(type="REPLY_END", finished_reason="completed")


class _HitlAgent:
    async def reply_stream(self, message: object):
        if isinstance(message, UserConfirmResultEvent):
            yield _FakeEvent(type="TEXT_BLOCK_DELTA", delta="已获准继续。")
            yield _FakeEvent(type="REPLY_END", finished_reason="completed")
            return
        yield _FakeEvent(
            type="REQUIRE_USER_CONFIRM",
            reply_id="reply-worker-1",
            tool_calls=[{
                "id": "tc-1",
                "name": "Bash",
                "type": "tool_call",
                "input": "{\"command\": \"ls\"}",
            }],
        )


def test_worker_spec_bypasses_tool_permission(client):
    studio = _create_discussing(client, "sess-mode")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    spec, _ctx, tools = build_worker_runtime_spec(studio, worker)
    assert spec.include_default_tools is True
    assert spec.guardrails.human_approval is False
    assert spec.guardrails.network_enabled is True
    assert spec.guardrails.tool_permission_mode == "bypass"
    assert spec.guardrails.tool_permission_profile == "trusted_tools"
    assert spec.guardrails.permission_profile.to_agentscope_permission_mode() == PermissionMode.BYPASS
    assert spec.model_options.max_react_iters == 40
    names = {tool.name for tool in tools}
    assert "group_post_message" in names
    assert "AskUser" not in names


def test_worker_blocks_without_api_key(client, monkeypatch):
    from types import SimpleNamespace

    from src.group_studio import worker_runtime

    monkeypatch.setattr(
        "src.group_studio.startup_checks.get_effective_model_settings",
        lambda: SimpleNamespace(provider="deepseek", api_key=""),
    )
    called = {"create": False}

    def boom(_spec):
        called["create"] = True
        raise AssertionError("must not construct worker agent without api key")

    monkeypatch.setattr(worker_runtime, "create_main_agent_from_spec", boom)
    studio = _create_discussing(client, "sess-worker-no-key")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    asyncio.run(run_worker_turn(studio["id"], worker["id"]))
    events = list_group_studio_events_after(studio["id"], 0)
    error = next(item for item in events if item["type"] == "GROUP_ERROR")
    assert error["payload"]["code"] == MODEL_NOT_CONFIGURED
    assert called["create"] is False
    snapshot = get_group_studio_snapshot(studio["id"])
    assert snapshot is not None
    assert snapshot["currentRunId"] is None
    assert snapshot["messages"] == []


def test_worker_turn_persists_message_not_delta(client):
    studio = _create_discussing(client, "sess-worker")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    asyncio.run(run_worker_turn(studio["id"], worker["id"], agent_factory=lambda _spec: _TalkAgent()))
    snapshot = get_group_studio_snapshot(studio["id"])
    assert snapshot is not None
    bodies = [item["body"] for item in snapshot["messages"] if item["role"] == "worker"]
    assert any("提纲" in body for body in bodies)
    types = [item["type"] for item in list_group_studio_events_after(studio["id"], 0)]
    assert "GROUP_MESSAGE_DELTA" not in types
    assert "GROUP_MESSAGE_OPEN" in types
    assert "GROUP_MESSAGE_CLOSE" in types
    assert "GROUP_RUN_ENDED" in types
    assert snapshot["currentRunId"] is None


def test_serial_second_run_conflict(client):
    studio = _create_discussing(client, "sess-serial")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    insert_group_studio_run({
        "id": "grun_busy",
        "groupId": studio["id"],
        "kind": "discussion",
        "status": "running",
        "speakerMemberId": worker["id"],
    })
    set_group_studio_current_run_id(studio["id"], "grun_busy")
    with pytest.raises(Exception) as exc:
        asyncio.run(run_host_discussion(studio["id"], agent_factory=lambda _spec: _TalkAgent()))
    assert exc.value.status_code == 409
    assert exc.value.code == ILLEGAL_TRANSITION


def test_approval_pause_then_approve(client):
    studio = _create_discussing(client, "sess-hitl")
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
        assert approval["replyId"] == "reply-worker-1"
        decided = client.post(f"/api/group-studio/{group_id}/approvals", json={
            "projectId": DEFAULT_PROJECT_ID,
            "approvalId": approval["id"],
            "decision": "approve",
        })
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "approved"
        await task

    asyncio.run(scenario())
    snapshot = get_group_studio_snapshot(group_id)
    assert snapshot is not None
    assert snapshot["currentRunId"] is None
    bodies = [item["body"] for item in snapshot["messages"]]
    assert any("获准" in body for body in bodies)
    types = [item["type"] for item in list_group_studio_events_after(group_id, 0)]
    assert "GROUP_APPROVAL" in types


def test_approval_missing_reply_id_cannot_resume(client):
    studio = _create_discussing(client, "sess-noreply")
    worker = next(item for item in studio["members"] if item["role"] == "worker")
    inserted = insert_group_studio_approval({
        "id": "gapr_empty",
        "groupId": studio["id"],
        "runId": "grun_empty",
        "memberId": worker["id"],
        "replyId": "",
        "toolCalls": [{"id": "tc-1", "name": "Bash", "input": "{}"}],
        "status": "pending",
    })
    response = client.post(f"/api/group-studio/{studio['id']}/approvals", json={
        "projectId": DEFAULT_PROJECT_ID,
        "approvalId": inserted["id"],
        "decision": "approve",
    })
    assert response.status_code == 409
    assert response.json()["detail"] == APPROVAL_NOT_PENDING


def test_group_studio_does_not_use_chat_pending_map():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "group_studio"
    forbidden = ("leader_stream", "PENDING_INTERACTIONS", "src.server.routes.chat.hitl")
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden):
            offenders.append(path.name)
    assert offenders == []
