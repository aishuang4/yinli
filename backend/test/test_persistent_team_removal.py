"""持久 Team 管理下线回归测试。"""

import sqlite3

import pytest
from pydantic import ValidationError

from src.server.main import app
from src.server.routes.agent_builder import BuilderPlanRequest
from src.storage import database


def test_persistent_team_api_is_not_registered() -> None:
    paths = {route.path for route in app.routes}

    assert not any(path == "/api/teams" or path.startswith("/api/teams/") for path in paths)


def test_agent_builder_rejects_legacy_team_mode() -> None:
    with pytest.raises(ValidationError):
        BuilderPlanRequest.model_validate({
            "requirement": "创建一个研究 Agent",
            "mode": "team",
        })


def test_startup_removes_legacy_team_storage(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "agent_workstation.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE json_store (
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (namespace, key)
            );
            CREATE TABLE teams (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE team_members (
                id TEXT PRIMARY KEY,
                team_id TEXT NOT NULL
            );
            INSERT INTO teams(id, name) VALUES ('legacy-team', 'Legacy Team');
            INSERT INTO team_members(id, team_id) VALUES ('legacy-member', 'legacy-team');
            INSERT INTO json_store(namespace, key, value, created_at, updated_at)
            VALUES ('team_templates', 'legacy-template', '{}', '', '');
        """)

    monkeypatch.setattr(
        "src.config.settings.settings.database_url",
        "sqlite+aiosqlite:///" + str(db_path),
    )
    database.forget_db(db_path)
    database.ensure_db()

    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        template_count = conn.execute(
            "SELECT COUNT(*) FROM json_store WHERE namespace = 'team_templates'"
        ).fetchone()[0]

    assert "teams" not in tables
    assert "team_members" not in tables
    assert template_count == 0
