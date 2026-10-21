"""合格 Chat 的 Skill 目录 = Agent 配置 ∪ 已启用共享包。

回归背景：系统包 seed 后曾被无条件追加；随后又改成除 yinli-catalog 外必须写进 profile。
当前合同：已启用且 availableToAllAgents 的包进入单 Agent / Team Leader 目录；
停用或关闭共享后仍须绑定；inject=False（Team Member）不因开关追加。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.server.routes.chat import run_context
from src.server.routes.chat.prompts import _build_skill_context_prompt
from src.server.routes.skills import Skill


def _skill(
    skill_id: str,
    *,
    source_scope: str = "system",
    enabled: bool = True,
    available_to_all_agents: bool = True,
) -> Skill:
    return Skill(
        id=skill_id,
        name=skill_id,
        description="",
        category="test",
        enabled=enabled,
        availableToAllAgents=available_to_all_agents,
        updatedAt="2026-09-14T00:00:00Z",
        sourceScope=source_scope,
    )


@pytest.fixture
def stub_installed(monkeypatch: pytest.MonkeyPatch) -> dict[str, Skill]:
    installed = {sid: _skill(sid) for sid in ("yinli-catalog", "yinli-charts", "word-documents")}
    monkeypatch.setattr("src.catalog.skill_seed.seed_system_skills", lambda: [])
    monkeypatch.setattr(
        "src.server.routes.skills.get_installed_skill",
        lambda sid: skill if (skill := installed.get(sid)) is not None and skill.enabled else None,
    )
    monkeypatch.setattr(
        "src.server.routes.skills.list_installed_enabled_skills",
        lambda: [skill for skill in installed.values() if skill.enabled],
    )
    return installed


def _merge(configured: list[str], external: list[Skill] | None = None, *, inject: bool = True) -> list[str]:
    merged = run_context._merge_file_catalog_skills(
        [],
        external_skills=external or [],
        inject=inject,
        configured_skill_ids=configured,
    )
    return [skill.id for skill in merged]


def test_agent_without_binding_gets_shared_system_packs(stub_installed) -> None:
    """缺省共享打开时，未绑定的助手也能看见办公包与说明书。"""

    assert set(_merge([])) == {"yinli-catalog", "yinli-charts", "word-documents"}


def test_unshared_system_skill_requires_binding(stub_installed) -> None:
    stub_installed["word-documents"] = _skill("word-documents", available_to_all_agents=False)
    stub_installed["yinli-charts"] = _skill("yinli-charts", available_to_all_agents=False)
    assert _merge([]) == ["yinli-catalog"]
    assert "word-documents" in _merge(["word-documents"])


def test_configured_system_skills_are_added(stub_installed) -> None:
    stub_installed["word-documents"] = _skill("word-documents", available_to_all_agents=False)
    stub_installed["yinli-charts"] = _skill("yinli-charts", available_to_all_agents=False)
    assert _merge(["yinli-charts"]) == ["yinli-catalog", "yinli-charts"]
    assert set(_merge(["word-documents", "yinli-charts"])) == {"yinli-catalog", "yinli-charts", "word-documents"}


def test_shared_project_skill_does_not_need_binding(stub_installed) -> None:
    project_skill = _skill("proj-skill", source_scope="project")
    assert "proj-skill" in _merge([], [project_skill])


def test_unshared_project_skill_requires_agent_configuration(stub_installed) -> None:
    """项目 .agents/skills 免 Project binding，但关掉共享后仍受 Agent 配置约束。"""

    project_skill = _skill("proj-skill", source_scope="project", available_to_all_agents=False)
    assert "proj-skill" not in _merge([], [project_skill])
    assert "proj-skill" in _merge(["proj-skill"], [project_skill])


def test_disabled_or_non_project_external_skill_is_ignored(stub_installed) -> None:
    stub_installed["word-documents"] = _skill("word-documents", available_to_all_agents=False)
    stub_installed["yinli-charts"] = _skill("yinli-charts", available_to_all_agents=False)
    disabled = _skill("proj-disabled", source_scope="project", enabled=False)
    user_scope = _skill("user-skill", source_scope="user")
    assert _merge(["proj-disabled", "user-skill"], [disabled, user_scope]) == ["yinli-catalog"]


def test_disabled_shared_skill_is_not_injected(stub_installed) -> None:
    stub_installed["word-documents"] = _skill("word-documents", enabled=False)
    assert "word-documents" not in _merge([])


def test_team_member_path_does_not_inject(stub_installed) -> None:
    assert _merge(["yinli-charts"], inject=False) == []


def test_shared_user_skill_joins_catalog(stub_installed) -> None:
    stub_installed["custom-shared"] = _skill("custom-shared")
    stub_installed["word-documents"] = _skill("word-documents", available_to_all_agents=False)
    stub_installed["yinli-charts"] = _skill("yinli-charts", available_to_all_agents=False)
    assert "custom-shared" in _merge([])


def test_frozen_job_skills_do_not_append_shared(stub_installed) -> None:
    """定时任务冻结集为空时，不得因共享开关追加办公包。"""

    from src.runtime.capabilities import resolve_capabilities

    bound = resolve_capabilities(
        mode="frozen",
        agent=SimpleNamespace(skills=[]),
        frozen=[],
    )
    assert bound.skills == ()


def test_skill_context_prompt_stays_on_demand() -> None:
    text = _build_skill_context_prompt()
    assert "Only Skill names and descriptions are listed up front" in text
    assert "<activated-skill>" in text
