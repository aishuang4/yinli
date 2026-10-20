"""顯式歷史 Workspace / Project 夾具。不得 autouse。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.session_migration.legacy_fields import LEGACY_SESSION_COLUMNS
from src.sessions.records import create_session
from src.storage import database


@pytest.fixture()
def legacy_project(tmp_path: Path) -> dict[str, str]:
    database.ensure_db()
    root = tmp_path / "legacy-project"
    root.mkdir()
    database.create_workspace({"id": "w-legacy", "name": "歷史工作區"})
    database.create_project({
        "id": "p-legacy",
        "name": "歷史項目",
        "root_path": str(root),
        "workspace_id": "w-legacy",
    })
    session = create_session(title="未回填", agent_id="default")
    column = LEGACY_SESSION_COLUMNS[1]
    with database._lock, database._connect() as conn:
        conn.execute(
            f"UPDATE chat_sessions SET {column} = ?, working_directory = '' WHERE id = ?",
            ("p-legacy", session["id"]),
        )
        conn.commit()
    return {
        "workspace_id": "w-legacy",
        "project_id": "p-legacy",
        "session_id": session["id"],
        "root": str(root),
    }
