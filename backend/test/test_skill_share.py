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
    row = next(item for item in database.list_skills() if item["id"] == "demo-share")
    assert row["availableToAllAgents"] is True
    assert "should-not-persist" in row["content"]


def test_replace_skills_round_trips_share_false(isolated_db) -> None:
    database.replace_skills({"demo-share": _skill_payload(availableToAllAgents=False, content="")})
    row = next(item for item in database.list_skills() if item["id"] == "demo-share")
    assert row["availableToAllAgents"] is False


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
    assert all(row["id"] != "legacy-skill" for row in rows)
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='skills'").fetchone() is None
    conn.close()


def test_save_user_skills_reloads_share_from_package(isolated_db) -> None:
    from src.server.routes.skills import _save_user_skills

    database.replace_skills({
        "user-share-flag": _skill_payload(
            id="user-share-flag",
            name="User Share",
            availableToAllAgents=False,
            content="正文留在 SKILL.md",
        ),
    })
    _save_user_skills()
    stored = next(item for item in database.list_skills() if item["id"] == "user-share-flag")
    assert stored["availableToAllAgents"] is False
    assert "正文留在 SKILL.md" in stored["content"]


def test_catalog_share_lock_returns_409() -> None:
    from src.server.routes.skills import SkillShareRequest, set_skill_shared

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(set_skill_shared("yinli-catalog", SkillShareRequest(availableToAllAgents=False)))
    assert exc_info.value.status_code == 409


def test_uninstalled_skill_cannot_share() -> None:
    from src.config.runtime_paths import tenant_root
    from src.server.routes.skills import SkillShareRequest, set_skill_shared
    from src.skills.registry import reload_skill_cache

    package = tenant_root() / ".agents" / "skills" / "not-installed-share"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: Pending\ndescription: 待安装\n---\n\n正文\n",
        encoding="utf-8",
    )
    reload_skill_cache()
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(set_skill_shared("not-installed-share", SkillShareRequest(availableToAllAgents=False)))
    assert exc_info.value.status_code == 409


def test_share_toggle_and_disable_drops_installed_lookup(isolated_db) -> None:
    from src.server.routes.skills import (
        SkillInstallRequest,
        SkillShareRequest,
        get_installed_skill,
        set_skill_enabled,
        set_skill_shared,
    )

    database.replace_skills({
        "toggle-share-lookup": _skill_payload(
            id="toggle-share-lookup",
            name="Toggle",
            content="正文",
        ),
    })
    updated = asyncio.run(set_skill_shared("toggle-share-lookup", SkillShareRequest(availableToAllAgents=False)))
    assert updated.available_to_all_agents is False
    assert get_installed_skill("toggle-share-lookup") is not None
    asyncio.run(set_skill_enabled("toggle-share-lookup", SkillInstallRequest(enabled=False)))
    assert get_installed_skill("toggle-share-lookup") is None


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
