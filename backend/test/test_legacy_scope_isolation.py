"""舊 Project 證據不得再決定目錄或資源。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.runtime.session_context import build_session_context
from src.sessions.records import create_session, upsert_resource_grant
from src.sessions.run_access import candidate_resource_access
from src.storage import database
pytest_plugins = ["test.legacy_fixtures"]

from test.fresh_tenant import install_skill, register_agent


def test_runtime_never_reads_project_root(legacy_project, tmp_path: Path) -> None:
    work = tmp_path / "directory-b"
    work.mkdir()
    session_id = legacy_project["session_id"]
    with database._lock, database._connect() as conn:
        conn.execute(
            "UPDATE chat_sessions SET working_directory = ? WHERE id = ?",
            (str(work), session_id),
        )
        conn.commit()
    upsert_resource_grant(session_id, "skill", "skill-any")
    assert build_session_context(session_id).cwd == work.resolve()
    with database._lock, database._connect() as conn:
        conn.execute("UPDATE chat_sessions SET working_directory = '' WHERE id = ?", (session_id,))
        conn.commit()
    from src.config.runtime_paths import session_artifacts_root

    assert build_session_context(session_id).cwd == session_artifacts_root(session_id).resolve()
    assert build_session_context(session_id).cwd != Path(legacy_project["root"]).resolve()


def test_legacy_binding_never_gates_resources(legacy_project) -> None:
    skill_id = install_skill("binding-ignored", shared=False)
    register_agent("agent-binding", skills=(skill_id,))
    session_id = legacy_project["session_id"]
    with database._lock, database._connect() as conn:
        conn.execute(
            "UPDATE chat_sessions SET agent_id = ? WHERE id = ?",
            ("agent-binding", session_id),
        )
        conn.execute(
            """
            INSERT INTO project_resource_bindings (
                project_id, resource_type, resource_id, enabled, created_at, updated_at
            ) VALUES (?, 'skill', ?, 0, '2026-10-20T00:00:00Z', '2026-10-20T00:00:00Z')
            """,
            (legacy_project["project_id"], skill_id),
        )
        conn.commit()
    upsert_resource_grant(session_id, "mcp", "mcp-unrelated")
    access = candidate_resource_access(session_id, agent_id="agent-binding", permission_mode="manual")
    assert skill_id in access.skill_ids


def test_unmigrated_session_rejected_before_cutover(legacy_project) -> None:
    from src.server.routes.session_errors import session_error_http
    from src.session_migration.state import set_migration_flag
    from src.sessions.errors import SessionError
    from src.sessions.records import get_run_snapshot, get_session
    from src.sessions.run_binding import begin_run

    set_migration_flag(False, schema_version_value=0)
    session = get_session(legacy_project["session_id"])
    called = {"model": False}

    def start_model(_snapshot):
        called["model"] = True
        return _snapshot

    with pytest.raises(SessionError) as exc_info:
        begin_run(
            legacy_project["session_id"],
            "run-legacy",
            agent_id=str(session["agentId"]),
            start_model=start_model,
        )
    assert exc_info.value.code == "session_not_migrated"
    assert called["model"] is False
    assert get_run_snapshot("run-legacy") is None
    fresh = create_session(title="新對話", agent_id="default")
    begin_run(fresh["id"], "run-new", agent_id="default", start_model=start_model)
    assert called["model"] is True
    response = session_error_http(SessionError("session_not_migrated", ""), default_status=409)
    assert response.status_code == 409
