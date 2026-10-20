"""Skill「所有 Agent 可用」开关的存储、覆盖与 API。"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.storage import database


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    yield


def _skill_payload(**overrides) -> dict:
    payload = {
        "id": "demo-share",
        "name": "Demo Share",
        "description": "demo",
        "category": "custom",
        "trigger": "",
        "content": "should-not-persist",
        "tags": [],
        "resourcePath": "",
        "resources": [],
        "status": "installed",
        "enabled": True,
        "installedAt": "2026-09-17T00:00:00Z",
        "updatedAt": "2026-09-17T00:00:00Z",
        "workspaceId": "default",
    }
    payload.update(overrides)
    return payload


def test_replace_skills_defaults_share_true(isolated_db) -> None:
    database.replace_skills({"demo-share": _skill_payload()})
    rows = database.list_skills()
    assert rows[0]["availableToAllAgents"] is True
    assert rows[0]["content"] == "should-not-persist"


def test_replace_skills_round_trips_share_false(isolated_db) -> None:
    database.replace_skills({"demo-share": _skill_payload(availableToAllAgents=False, content="")})
    rows = database.list_skills()
    assert rows[0]["availableToAllAgents"] is False
    assert rows[0]["content"] == ""


def test_legacy_skills_table_without_share_column_defaults_true(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "legacy-skills.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE skills (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL,
            trigger TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL,
            resource_path TEXT NOT NULL DEFAULT '',
            resources TEXT NOT NULL,
            status TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            installed_at TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO skills (
            id, name, description, category, trigger, content, tags,
            resource_path, resources, status, enabled, installed_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "legacy-skill", "Legacy", "", "custom", "", "", "[]",
            "", "[]", "installed", 1, None, "2026-09-17T00:00:00Z",
        ),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        "src.config.settings.settings.database_url",
        "sqlite+aiosqlite:///" + str(db_path),
    )
    database.forget_db(db_path)
    rows = database.list_skills()
    assert rows[0]["id"] == "legacy-skill"
    assert rows[0]["availableToAllAgents"] is True


def _registered():
    from src.skills.registry import register_user_skill, SkillRegisterRequest
    return register_user_skill(SkillRegisterRequest(name="demo-share", description="共享技能", content="说明书"))


def test_share_policy_does_not_persist_registration_row(isolated_db):
    from src.server.routes.skills import SkillShareRequest, set_skill_shared
    from src.skills.policy import read_policy
    item = _registered()
    asyncio.run(set_skill_shared(item.id, SkillShareRequest(availableToAllAgents=False)))
    assert database.list_skills() == []
    assert read_policy(item._catalog_context.policy_path).overrides[item.id].shared is False


def test_catalog_share_lock_returns_409():
    from src.server.routes.skills import SkillShareRequest, set_skill_shared
    with pytest.raises(HTTPException) as error:
        asyncio.run(set_skill_shared("yinli-catalog", SkillShareRequest(availableToAllAgents=False)))
    assert error.value.status_code == 409


def test_uninstalled_skill_cannot_share():
    from src.server.routes.skills import SkillShareRequest, set_skill_shared, uninstall_skill
    item = _registered()
    asyncio.run(uninstall_skill(item.id))
    with pytest.raises(HTTPException) as error:
        asyncio.run(set_skill_shared(item.id, SkillShareRequest(availableToAllAgents=False)))
    assert error.value.status_code == 409


def test_share_toggle_and_disable_drops_installed_lookup(isolated_db):
    from src.server.routes.skills import SkillInstallRequest, SkillShareRequest, set_skill_enabled, set_skill_shared
    from src.skills.registry import get_installed_skill
    item = _registered()
    assert asyncio.run(set_skill_shared(item.id, SkillShareRequest(availableToAllAgents=False))).available_to_all_agents is False
    assert get_installed_skill(item.id)
    asyncio.run(set_skill_enabled(item.id, SkillInstallRequest(enabled=False)))
    assert get_installed_skill(item.id) is None


def test_managed_policy_survives_manual_edit(isolated_db, tmp_path):
    from src.skills.registry import discover_external_skills
    from src.skills import current_skill_context
    from src.skills.policy import set_skill_policy
    directory = current_skill_context().tenant_root / "skills/project"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\ndescription: project\n---\n步骤")
    item = next(item for item in discover_external_skills(tmp_path) if item.name == "project")
    set_skill_policy(item._catalog_context.policy_path, item.id, enabled=False, availableToAllAgents=False)
    (directory / "SKILL.md").write_text("---\ndescription: changed\n---\n新步骤")
    updated = next(candidate for candidate in discover_external_skills(tmp_path) if candidate.id == item.id)
    assert not updated.enabled and not updated.available_to_all_agents


def test_member_runtime_skills_ignore_shared_only() -> None:
    from types import SimpleNamespace

    from src.server.routes.chat.team_runtime.member_runtime import _member_runtime_skills
    from src.server.routes.skills import Skill

    bound = Skill(
        id="bound-skill",
        name="Bound",
        description="",
        category="test",
        updatedAt="2026-09-17T00:00:00Z",
    )
    shared = Skill(
        id="shared-only",
        name="Shared",
        description="",
        category="test",
        updatedAt="2026-09-17T00:00:00Z",
    )
    ctx = SimpleNamespace(
        agent_config=SimpleNamespace(skills=["bound-skill"]),
        used_skills=[bound, shared],
    )
    assert [skill.id for skill in _member_runtime_skills(ctx)] == ["bound-skill"]
