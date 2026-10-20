"""begin_run 與未遷移准入的契約。"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.catalog.agent_models import AgentResponse
from src.catalog.loader import invalidate_catalog, save_agent
from src.session_migration.legacy_fields import LEGACY_SCOPE_FIELDS, LEGACY_SESSION_COLUMNS
from src.session_migration.state import set_migration_flag
from src.sessions.errors import SessionError
from src.sessions.legacy_gate import require_migrated_session
from src.sessions.records import add_allowed_root, create_session, get_run_snapshot, upsert_resource_grant
from src.sessions.run_binding import begin_run
from src.skills.catalog import current_skill_context
from src.skills.policy import set_skill_policy
from src.skills.service import catalog_for
from src.storage import database


def _agent(agent_id: str, skills: list[str]) -> None:
    invalidate_catalog()
    save_agent(AgentResponse.model_validate({
        "id": agent_id,
        "name": agent_id,
        "avatar": "",
        "description": "desc",
        "system_prompt": "prompt",
        "modelConfig": {"maxReactIters": 8},
        "mcpTools": [],
        "knowledgeTools": [],
        "skills": skills,
        "tags": [],
        "guardrails": {},
        "status": "idle",
        "created_at": "2026-10-19T00:00:00+00:00",
        "updated_at": "2026-10-19T00:00:00+00:00",
        next(item for item in LEGACY_SCOPE_FIELDS if item.startswith("w")): "",
        "role": "chat",
        "jobToolkit": {},
    }))


def _private_skill(root: Path, name: str) -> str:
    package = root / "skills" / name
    package.mkdir(parents=True, exist_ok=True)
    (package / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 說明\n---\n正文\n",
        encoding="utf-8",
    )
    item = next(candidate for candidate in catalog_for(refresh=True).candidates if candidate.name == name)
    set_skill_policy(current_skill_context().policy_path, item.id, availableToAllAgents=False)
    return item.id


def test_begin_run_uses_passed_agent_not_session_agent(tmp_path: Path) -> None:
    from src.config.runtime_paths import tenant_root

    skill_a = _private_skill(tenant_root(), "skill-agent-a")
    skill_b = _private_skill(tenant_root(), "skill-agent-b")
    _agent("agent-a", [skill_a])
    _agent("agent-b", [skill_b])
    session = create_session(title="換 Agent", agent_id="agent-a")
    snapshot = begin_run(
        session["id"],
        "run-other-agent",
        agent_id="agent-b",
        start_model=lambda item: item,
    )
    skill_ids = {
        item["resourceId"]
        for item in snapshot["resources"]["items"]
        if item["resourceType"] == "skill"
    }
    assert skill_b in skill_ids
    assert skill_a not in skill_ids
    assert snapshot["agentSnapshot"]["agentId"] == "agent-b"


def test_invalid_working_directory_does_not_write_snapshot(tmp_path: Path) -> None:
    work = tmp_path / "missing-work"
    work.mkdir()
    session = create_session(title="失效目錄", working_directory=str(work))
    work.rmdir()
    with pytest.raises(SessionError) as exc_info:
        begin_run(session["id"], "run-missing-dir", agent_id="default", start_model=lambda item: item)
    assert exc_info.value.code == "directory_unavailable"
    assert get_run_snapshot("run-missing-dir") is None


def _stamp_legacy(session_id: str) -> None:
    column = LEGACY_SESSION_COLUMNS[1]
    with database._lock, database._connect() as conn:
        conn.execute(f"UPDATE chat_sessions SET {column} = ? WHERE id = ?", ("p-old", session_id))
        conn.commit()


def test_require_migrated_session_needs_all_five_conditions(tmp_path: Path) -> None:
    set_migration_flag(False, schema_version_value=0)
    blocked = create_session(title="未遷移")
    _stamp_legacy(blocked["id"])
    with pytest.raises(SessionError) as exc_info:
        require_migrated_session(blocked["id"])
    assert exc_info.value.code == "session_not_migrated"

    set_migration_flag(True, schema_version_value=1)
    require_migrated_session(blocked["id"])
    set_migration_flag(False, schema_version_value=0)

    without_scope = create_session(title="沒有舊欄位")
    require_migrated_session(without_scope["id"])

    work = tmp_path / "kept"
    work.mkdir()
    with_directory = create_session(title="有目錄", working_directory=str(work))
    _stamp_legacy(with_directory["id"])
    require_migrated_session(with_directory["id"])

    with_root = create_session(title="有授權根")
    _stamp_legacy(with_root["id"])
    add_allowed_root(with_root["id"], str(work), access_mode="read", source="user")
    require_migrated_session(with_root["id"])

    with_grant = create_session(title="有 grant")
    _stamp_legacy(with_grant["id"])
    upsert_resource_grant(with_grant["id"], "skill", "skill-any")
    require_migrated_session(with_grant["id"])
