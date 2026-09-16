"""群聊工作室结束后的记忆收口。"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.group_studio import archive_memory
from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID
from src.group_studio.service import get_studio
from src.group_studio.settings import set_group_studio_settings
from src.server.routes import group_studio, settings as settings_routes
from src.storage import database
from src.storage.database import DEFAULT_PROJECT_ID, ensure_db, insert_group_studio_message


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "agent_workstation.db")
    monkeypatch.setattr(database, "_initialized", False)
    monkeypatch.setattr("src.config.product_features.is_surface_enabled", lambda key: True)
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


def _studio_with_pin(client: TestClient):
    created = client.post("/api/group-studio/from-chat", json={
        "projectId": DEFAULT_PROJECT_ID,
        "chatSessionId": "sess-archive",
        "agentId": "default",
    })
    assert created.status_code == 200, created.text
    group_id = created.json()["id"]
    put = client.put(f"/api/group-studio/{group_id}/pin", json={
        "projectId": DEFAULT_PROJECT_ID,
        "goal": "为 Q3 销售数据做一份按地区汇总的分析报告",
    })
    assert put.status_code == 200, put.text
    insert_group_studio_message({
        "id": "gmsg_h1",
        "groupId": group_id,
        "speakerId": "human",
        "role": "human",
        "intent": "human_directive",
        "body": "按地区拆，异常值单独列表",
        "visibleInTimeline": True,
    })
    insert_group_studio_message({
        "id": "gmsg_w1",
        "groupId": group_id,
        "speakerId": "writer",
        "role": "worker",
        "intent": "discuss",
        "body": "按地区拆成华东 / 华北 / 华南三张表",
        "visibleInTimeline": True,
    })
    return get_studio(group_id)


def test_build_archive_digest_uses_pin_and_worker_text(client) -> None:
    studio = _studio_with_pin(client)

    goal_text, minutes = archive_memory.build_archive_digest(studio)

    assert "为 Q3 销售数据做一份按地区汇总的分析报告" in goal_text
    assert "按地区拆成华东 / 华北 / 华南三张表" in minutes
    agent_id, _name = archive_memory.owner_agent_for_archive(studio)
    assert agent_id == "default"
    assert agent_id != GROUP_STUDIO_HOST_AGENT_ID


def test_run_archive_memory_persists_node_and_reflects_with_worker_agent(client, monkeypatch) -> None:
    studio = _studio_with_pin(client)
    seen: dict = {}

    async def fake_reflect(item, *, model_name, rule_admission=None, **_kwargs):
        seen["item"] = item
        seen["model_name"] = model_name
        seen["rule_admission"] = rule_admission
        from src.memory.reflection import MemoryReflectionResult

        return MemoryReflectionResult(ignored_reason="")

    monkeypatch.setattr("src.memory.reflection.reflect_completed_run_memory_with_agents", fake_reflect)

    summary = asyncio.run(archive_memory.run_archive_memory(studio))

    assert summary["agentId"] == "default"
    assert summary["nodeId"]
    node = database.get_memory_node(summary["nodeId"])
    assert node["source_run_id"] == f"group:{studio.id}"
    assert node["source_session_id"] == "sess-archive"
    assert "按地区拆成华东" in node["agent_final_outputs"][0]
    assert seen["item"].agent_id == "default"
    assert seen["rule_admission"].should_write_markdown is True
    assert seen["rule_admission"].should_persist_history is True
    audits = database.list_run_events_by_type("MEMORY_AUDIT", limit=50)
    actions = {
        str((event.get("payload") or {}).get("memoryType") or "")
        for event in audits
        if str(event.get("sessionId") or event.get("session_id") or "") == "sess-archive"
    }
    assert {"group_archive_memory", "group_archive_reflection"} <= actions


def test_worker_spec_hard_blocks_destructive_shell(client) -> None:
    from src.group_studio.worker_runtime import WORKER_SHELL_BLACKLIST, build_worker_runtime_spec

    studio = _studio_with_pin(client)
    member = {
        "id": "gmbr_test",
        "groupId": studio.id,
        "role": "worker",
        "agentId": "default",
        "displayName": "默认助手",
        "status": "active",
        "muted": False,
    }
    spec, _ctx, tools = build_worker_runtime_spec(studio.model_dump(by_alias=True), member)
    for item in WORKER_SHELL_BLACKLIST:
        assert item in spec.guardrails.shell_blacklist
    assert len(spec.guardrails.shell_blacklist) >= len(WORKER_SHELL_BLACKLIST)
    assert [tool.name for tool in tools] == ["group_post_message"]
