"""待确认 Markdown 记忆候选：存储、API 采纳 / 拒绝。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.memory.markdown_store import MarkdownMemoryStore
from src.server.routes import memory as memory_routes
from src.storage import database


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    yield
    database.forget_db(database.db_path())


@pytest.fixture()
def client(isolated_db, tmp_path, monkeypatch):
    database.ensure_db()
    # 让候选采纳写到临时目录，而不是开发机的真实记忆文件。
    monkeypatch.setattr(
        "src.memory.markdown_reflection.MarkdownMemoryStore",
        lambda: MarkdownMemoryStore(root=tmp_path / "memory"),
    )
    app = FastAPI()
    app.include_router(memory_routes.router, prefix="/api/memory")
    with TestClient(app) as test_client:
        yield test_client


def _candidate(**overrides) -> dict:
    payload = {
        "scope": "user",
        "ownerId": "default",
        "section": "Stable Preferences",
        "operation": "append",
        "note": "回答默认用中文。",
        "reason": "用户多次表达。",
        "confidence": 0.9,
        "agentId": "agent-1",
        "agentName": "Agent",
        "sourceSessionId": "s-1",
        "sourceRunId": "run-1",
    }
    payload.update(overrides)
    return database.create_memory_markdown_candidate(payload)


def test_store_round_trip_and_pending_dedupe(isolated_db) -> None:
    created = _candidate()
    assert created["status"] == "pending"
    assert database.count_pending_memory_markdown_candidates() == 1
    assert database.find_pending_memory_markdown_candidate(scope="user", owner_id="default", note="回答默认用中文。", exact_text="")
    assert database.list_memory_markdown_candidates(status="pending")[0]["id"] == created["id"]
    assert database.list_memory_markdown_candidates(status="pending", scope="agent") == []

    updated = database.set_memory_markdown_candidate_status(created["id"], status="rejected", resolution="不需要")
    assert updated["status"] == "rejected" and updated["resolution"] == "不需要"
    assert database.count_pending_memory_markdown_candidates() == 0
    with pytest.raises(ValueError):
        database.set_memory_markdown_candidate_status(created["id"], status="bogus")


def test_api_accept_writes_markdown_and_reject_keeps_file_untouched(client, tmp_path) -> None:
    accept_me = _candidate()
    reject_me = _candidate(note="解释要非常详细。", scope="global_preference", section="Workspace Rules")

    listed = client.get("/api/memory/markdown-candidates").json()
    assert listed["pendingCount"] == 2
    assert {item["id"] for item in listed["records"]} == {accept_me["id"], reject_me["id"]}

    accepted = client.patch(f"/api/memory/markdown-candidates/{accept_me['id']}", json={"action": "accept"})
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    content = (tmp_path / "memory" / "users" / "user_memory.md").read_text(encoding="utf-8")
    assert "回答默认用中文。" in content

    rejected = client.patch(f"/api/memory/markdown-candidates/{reject_me['id']}", json={"action": "reject", "reason": "不是全局规则"})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert not (tmp_path / "memory" / "global").exists()

    # 已处理的不能再操作；不存在的 404
    again = client.patch(f"/api/memory/markdown-candidates/{accept_me['id']}", json={"action": "accept"})
    assert again.status_code == 400
    assert client.patch("/api/memory/markdown-candidates/nope", json={"action": "accept"}).status_code == 404
    assert client.get("/api/memory/markdown-candidates").json()["pendingCount"] == 0


def test_pending_count_follows_the_current_project(client) -> None:
    _candidate(note="这个项目还没处理。")
    _candidate(note="另一个项目的候选。", projectSlug="other-project")

    current = client.get("/api/memory/markdown-candidates?projectSlug=default").json()
    assert current["pendingCount"] == 1
    assert [item["note"] for item in current["records"]] == ["这个项目还没处理。"]

    other = client.get("/api/memory/markdown-candidates?projectSlug=other-project").json()
    assert other["pendingCount"] == 1


def test_api_accept_marks_failed_when_exact_text_missing(client, tmp_path) -> None:
    candidate = _candidate(operation="update", note="", exactText="- 不存在的旧条目", replacementText="新条目")
    response = client.patch(f"/api/memory/markdown-candidates/{candidate['id']}", json={"action": "accept"})
    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert "exact_text_not_found" in response.json()["resolution"]
