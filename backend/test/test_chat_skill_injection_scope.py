"""单 Agent Chat 的 Skill 注入必须以 Agent 配置为上限。

回归背景：系统包 seed 后曾被无条件追加给所有 Agent，导致未配置 Skills 的 Agent 也调用了 Skill。
合同：file-catalog-v1 §9.2 只允许 `yinli-catalog` 无条件注入；其余系统包和项目 Skill 需在 Agent 配置里。
"""

from __future__ import annotations

import pytest

from src.server.routes.chat import run_context
from src.server.routes.skills import Skill


def _skill(skill_id: str, *, source_scope: str = "system", enabled: bool = True) -> Skill:
    return Skill(
        id=skill_id,
        name=skill_id,
        description="",
        category="test",
        enabled=enabled,
        updatedAt="2026-09-14T00:00:00Z",
        sourceScope=source_scope,
    )


@pytest.fixture
def stub_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    installed = {sid: _skill(sid) for sid in ("yinli-catalog", "yinli-charts", "word-documents")}
    monkeypatch.setattr("src.catalog.skill_seed.seed_system_skills", lambda: [])
    monkeypatch.setattr("src.server.routes.skills.get_installed_skill", lambda sid: installed.get(sid))


def _merge(configured: list[str], external: list[Skill] | None = None, *, inject: bool = True) -> list[str]:
    merged = run_context._merge_file_catalog_skills(
        [],
        external_skills=external or [],
        inject=inject,
        configured_skill_ids=configured,
    )
    return [skill.id for skill in merged]


def test_agent_without_skills_only_gets_catalog_guide(stub_installed) -> None:
    """未配置任何 Skill 的 Agent 只拿到目录说明书，不拿 word-documents / yinli-charts。"""

    assert _merge([]) == ["yinli-catalog"]


def test_configured_system_skills_are_added(stub_installed) -> None:
    assert _merge(["yinli-charts"]) == ["yinli-catalog", "yinli-charts"]
    assert set(_merge(["word-documents", "yinli-charts"])) == {"yinli-catalog", "yinli-charts", "word-documents"}


def test_project_skill_requires_agent_configuration(stub_installed) -> None:
    """项目 .agents/skills 免 Project binding，但仍受 Agent 配置约束。"""

    project_skill = _skill("proj-skill", source_scope="project")
    assert "proj-skill" not in _merge([], [project_skill])
    assert "proj-skill" in _merge(["proj-skill"], [project_skill])


def test_disabled_or_non_project_external_skill_is_ignored(stub_installed) -> None:
    disabled = _skill("proj-disabled", source_scope="project", enabled=False)
    user_scope = _skill("user-skill", source_scope="user")
    assert _merge(["proj-disabled", "user-skill"], [disabled, user_scope]) == ["yinli-catalog"]


def test_team_mode_does_not_inject(stub_installed) -> None:
    assert _merge(["yinli-charts"], inject=False) == []
