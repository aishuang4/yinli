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
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "skill-share.db")
    monkeypatch.setattr(database, "_initialized", False)
    yield
    monkeypatch.setattr(database, "_initialized", False)


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
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(database, "_initialized", False)
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

    rows = database.list_skills()
    assert rows[0]["id"] == "legacy-skill"
    assert rows[0]["availableToAllAgents"] is True
    monkeypatch.setattr(database, "_initialized", False)


def test_save_user_skills_persists_share_and_clears_content(isolated_db) -> None:
    from src.server.routes.skills import Skill, _save_user_skills, _skills

    skill = Skill(
        id="user-share-flag",
        name="User Share",
        description="",
        category="自定义",
        content="# should be emptied",
        enabled=True,
        status="installed",
        availableToAllAgents=False,
        updatedAt="2026-09-17T00:00:00Z",
    )
    _skills[skill.id] = skill
    _save_user_skills()
    stored = next(item for item in database.list_skills() if item["id"] == skill.id)
    assert stored["availableToAllAgents"] is False
    assert stored["content"] == ""
    _skills.pop(skill.id, None)


def test_catalog_share_lock_returns_409() -> None:
    from src.server.routes.skills import SkillShareRequest, set_skill_shared

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(set_skill_shared("yinli-catalog", SkillShareRequest(availableToAllAgents=False)))
    assert exc_info.value.status_code == 409


def test_uninstalled_skill_cannot_share() -> None:
    from src.server.routes.skills import Skill, SkillShareRequest, _skills, set_skill_shared

    skill = Skill(
        id="not-installed-share",
        name="Pending",
        description="",
        category="自定义",
        status="available",
        enabled=False,
        updatedAt="2026-09-17T00:00:00Z",
    )
    _skills[skill.id] = skill
    try:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(set_skill_shared(skill.id, SkillShareRequest(availableToAllAgents=False)))
        assert exc_info.value.status_code == 409
    finally:
        _skills.pop(skill.id, None)


def test_share_toggle_and_disable_drops_installed_lookup(isolated_db) -> None:
    from src.server.routes.skills import (
        Skill,
        SkillInstallRequest,
        SkillShareRequest,
        _skills,
        get_installed_skill,
        set_skill_enabled,
        set_skill_shared,
    )

    skill = Skill(
        id="toggle-share-lookup",
        name="Toggle",
        description="",
        category="自定义",
        status="installed",
        enabled=True,
        availableToAllAgents=True,
        updatedAt="2026-09-17T00:00:00Z",
    )
    _skills[skill.id] = skill
    try:
        updated = asyncio.run(set_skill_shared(skill.id, SkillShareRequest(availableToAllAgents=False)))
        assert updated.available_to_all_agents is False
        assert get_installed_skill(skill.id) is not None
        asyncio.run(set_skill_enabled(skill.id, SkillInstallRequest(enabled=False)))
        assert get_installed_skill(skill.id) is None
    finally:
        _skills.pop(skill.id, None)


def test_discover_merges_share_override(isolated_db, monkeypatch) -> None:
    from src.catalog.skills import DiscoveredSkill
    from src.server.routes import skills as skills_routes

    skills_routes._upsert_external_override("proj-pack", enabled=True, availableToAllAgents=False)
    item = DiscoveredSkill(
        id="proj-pack",
        name="Proj Pack",
        description="",
        trigger="",
        markdown="# SKILL",
        resource_path="/tmp/proj-pack",
        resources=["SKILL.md"],
        enabled=True,
        source_kind="external_agents",
        source_scope="project",
        source_path="/tmp/proj-pack",
        content_hash="abc",
    )
    monkeypatch.setattr(skills_routes, "discover_skill_packages", lambda *args, **kwargs: [item])
    found = skills_routes.discover_external_skills(Path("/tmp/project"))
    assert found[0].enabled is True
    assert found[0].available_to_all_agents is False


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
