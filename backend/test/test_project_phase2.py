"""阶段 2：Project slug、默认项目和旧库封存。"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from src.storage.database import (
    create_project,
    ensure_db,
    get_chat_session,
    get_project,
    list_chat_messages,
    list_projects,
)
from src.storage.legacy_archive import archive_legacy_layout
from src.storage.project_records import ProjectNameError, slugify_project_name


def test_fresh_tenant_gets_default_project():
    ensure_db()
    project = get_project("default")
    assert project is not None
    assert project["slug"] == "default"
    assert project["name"] == "默认项目"
    assert project["status"] == "active"
    assert project["folders"][0]["accessMode"] == "readwrite"
    assert project["folders"][0]["isPrimary"] is True
    assert Path(project["primaryPath"]).is_dir()
    assert [item["slug"] for item in list_projects()] == ["default"]


def test_default_project_can_send_a_message(monkeypatch):
    from src.server.routes.chat.handlers.send import start_chat_run
    from src.server.routes.chat.models import ChatMessage
    from src.server.routes.chat.projection.source_events import SourceEvent

    ensure_db()

    async def execute(*, ctx, run_data):
        del ctx, run_data
        yield SourceEvent("TEXT_BLOCK_DELTA", {"delta": "收到"})
        yield SourceEvent("REPLY_END", {"finished_reason": "completed"})

    async def no_habit(**kwargs):
        del kwargs
        return None

    monkeypatch.setattr("src.server.routes.chat.handlers.send.execute_chat_runtime", execute)
    monkeypatch.setattr("src.server.routes.chat.handlers.send.apply_habit_update", no_habit)
    monkeypatch.setattr("src.server.routes.chat.handlers.send.get_startup_error", lambda ctx, run_data: None)

    async def send():
        run = await start_chat_run(ChatMessage(message="你好", project_slug="default"))
        await run.async_task
        return run

    run = asyncio.run(send())
    session = get_chat_session(run.session_id)
    assert session is not None
    assert session["project_slug"] == "default"
    assert run.project_slug == "default"
    messages = list_chat_messages(run.session_id)
    assert any(item.get("role") == "user" and "你好" in str(item.get("content") or "") for item in messages)


def test_duplicate_name_and_reserved_slug_are_rejected(tmp_path: Path):
    ensure_db()
    created = create_project({"name": "演示项目", "primaryPath": str(tmp_path / "demo")})
    assert created["slug"] == slugify_project_name("演示项目")
    with pytest.raises(ProjectNameError):
        create_project({"name": "演示项目", "primaryPath": str(tmp_path / "other")})
    with pytest.raises(ProjectNameError):
        create_project({"name": "Default", "primaryPath": str(tmp_path / "reserved")})


def test_old_database_is_archived_before_new_state_db(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    legacy = data / "agent_workstation.db"
    conn = sqlite3.connect(legacy)
    conn.execute("CREATE TABLE workspaces (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO workspaces (id) VALUES ('default')")
    conn.commit()
    conn.close()
    (tmp_path / "workspaces").mkdir()
    (tmp_path / "workspaces" / "note.txt").write_text("old", encoding="utf-8")

    dest = archive_legacy_layout(data)
    assert dest is not None
    assert not legacy.exists()
    assert not (tmp_path / "workspaces").exists()
    assert (dest / "agent_workstation.db").is_file()
    assert (dest / "workspaces" / "note.txt").read_text(encoding="utf-8") == "old"
    assert archive_legacy_layout(data) is None
