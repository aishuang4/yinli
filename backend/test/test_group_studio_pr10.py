"""群聊工作室 PR 10：默认开启、隔离墙回归。"""

from __future__ import annotations

from pathlib import Path

from src.group_studio.models import GROUP_STUDIO_HOST_AGENT_ID
from src.group_studio.settings import is_group_studio_enabled
from src.project_resources import ProjectResourceAccess
from src.storage.engine import database
from src.storage.engine.database import ensure_db
import sqlite3
import pytest

BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    yield
    database.forget_db(database.db_path())


def _python_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return [path for path in root.rglob("*.py") if "__pycache__" not in path.parts]


def test_flag_defaults_enabled_without_json_record(isolated_db):
    ensure_db()
    assert is_group_studio_enabled() is True


def test_chat_handlers_and_team_runtime_do_not_import_group_studio():
    roots = [
        BACKEND_SRC / "server" / "routes" / "chat" / "handlers",
        BACKEND_SRC / "server" / "routes" / "chat" / "team_runtime",
    ]
    offenders: list[str] = []
    for root in roots:
        for path in _python_files(root):
            text = path.read_text(encoding="utf-8")
            if "src.group_studio" in text or "routes.group_studio" in text:
                offenders.append(str(path.relative_to(BACKEND_SRC)))
    assert offenders == []


def test_group_studio_does_not_import_chat_hitl_or_leader_stream():
    roots = [
        BACKEND_SRC / "group_studio",
        BACKEND_SRC / "server" / "routes" / "group_studio.py",
    ]
    forbidden = (
        "leader_stream",
        "PENDING_INTERACTIONS",
        "src.server.routes.chat.hitl",
        "team_runtime",
    )
    offenders: list[str] = []
    for root in roots:
        for path in _python_files(root):
            text = path.read_text(encoding="utf-8")
            if any(token in text for token in forbidden):
                offenders.append(str(path.relative_to(BACKEND_SRC)))
    assert offenders == []


def test_records_do_not_write_chat_tables():
    text = (BACKEND_SRC / "storage" / "records" / "group_studio_records.py").read_text(encoding="utf-8")
    assert "INSERT INTO chat_sessions" not in text
    assert "INSERT INTO chat_messages" not in text
    assert "INSERT INTO run_events" not in text


def test_no_persistent_teams_table(isolated_db):
    ensure_db()
    with sqlite3.connect(database.db_path()) as conn:
        names = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
    assert "teams" not in names
    assert "team_members" not in names
    assert "group_studios" in names
    assert "group_studio_events" in names


def test_require_agent_skips_host_sentinel():
    access = ProjectResourceAccess(
        project_slug="default",
        session_id="s",
        project_root=Path("."),
    )
    access.require_agent("default")
    access.require_agent(GROUP_STUDIO_HOST_AGENT_ID)
