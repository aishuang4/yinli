"""Chat 发送框强制开启 Skill：校验、并入 catalog、用户附注与预启用。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.server.routes.chat.prompts import _build_forced_skills_user_note, _build_skill_context_prompt
from src.server.routes.chat.run_context import (
    MAX_FORCED_SKILLS,
    _merge_forced_skills,
    resolve_forced_runtime_skills,
)
from src.server.routes.chat.skill_context_middleware import (
    activate_forced_skills,
    activated_skills,
)
from src.server.routes.chat.team_runtime.member_runtime import _member_runtime_skills
from src.server.routes.skills import Skill
from test.test_skill_context_middleware import SKILL_BODY, _FakeAgent


def _skill(
    skill_id: str,
    *,
    enabled: bool = True,
    content: str = "",
    available_to_all_agents: bool = True,
) -> Skill:
    return Skill(
        id=skill_id,
        name=skill_id,
        description=f"{skill_id} desc",
        category="test",
        enabled=enabled,
        content=content,
        availableToAllAgents=available_to_all_agents,
        updatedAt="2026-09-18T00:00:00Z",
    )


class _AllowAll:
    def filter_ids(self, _kind: str, ids: list[str]) -> list[str]:
        return list(ids)


class _AllowSome:
    def __init__(self, allowed: set[str]) -> None:
        self.allowed = allowed

    def filter_ids(self, _kind: str, ids: list[str]) -> list[str]:
        return [item for item in ids if item in self.allowed]


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> dict[str, Skill]:
    from src.catalog.skill_seed import seed_system_skills
    from src.skills.registry import register_user_skill, SkillRegisterRequest, get_installed_skill
    from src.skills.policy import set_skill_policy
    seed_system_skills()
    private = register_user_skill(SkillRegisterRequest(name="private-pack", description="private"))
    disabled = register_user_skill(SkillRegisterRequest(name="disabled-pack", description="disabled"))
    set_skill_policy(private._catalog_context.policy_path, private.id, availableToAllAgents=False)
    set_skill_policy(disabled._catalog_context.policy_path, disabled.id, enabled=False)
    skills = {key: get_installed_skill(key) for key in ("word-documents", "yinli-charts", "private-pack")}
    return skills


def test_resolve_forced_skills_requires_installed_and_project(installed) -> None:
    resolved = resolve_forced_runtime_skills(
        ["word-documents", "word-documents"],
        project_resources=_AllowAll(),
    )
    assert [skill.id for skill in resolved] == ["word-documents"]

    with pytest.raises(ValueError, match="不存在、停用或有歧义"):
        resolve_forced_runtime_skills(["missing-skill"], project_resources=_AllowAll())

    with pytest.raises(ValueError, match="未授权给当前 Project"):
        resolve_forced_runtime_skills(
            ["private-pack"],
            project_resources=_AllowSome(set()),
        )

    # 共享只绕过 Agent profile，不能越过 Project 白名单。
    with pytest.raises(ValueError, match="未授权给当前 Project"):
        resolve_forced_runtime_skills(["word-documents"], project_resources=_AllowSome(set()))


def test_resolve_forced_skills_caps_count(installed) -> None:
    ids = [f"skill-{index}" for index in range(MAX_FORCED_SKILLS + 1)]
    with pytest.raises(ValueError, match="最多强制开启"):
        resolve_forced_runtime_skills(ids, project_resources=_AllowAll())


def test_merge_forced_skills_appends_unbound_pack(installed) -> None:
    catalog = [_skill("yinli-catalog")]
    forced = [installed["word-documents"]]
    merged = _merge_forced_skills(catalog, forced)
    assert [skill.id for skill in merged] == ["yinli-catalog", "word-documents"]
    assert _merge_forced_skills(merged, forced) == merged


def test_forced_skill_user_note_does_not_embed_markdown() -> None:
    note = _build_forced_skills_user_note([_skill("word-documents", content=SKILL_BODY)])
    assert "<pinned-skill>" in note
    assert "word-documents" in note
    assert SKILL_BODY not in note
    assert "SKILL.md" not in note


def test_skill_context_prompt_mentions_pinned_block() -> None:
    text = _build_skill_context_prompt()
    assert "<pinned-skill>" in text
    assert "<activated-skill>" in text


def test_activate_forced_skills_writes_body_into_context(tmp_path, monkeypatch) -> None:
    package = tmp_path / "word-documents"
    package.mkdir()
    (package / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")
    skill = _skill("word-documents")
    from src.skills.sdk import resolved_path
    skill = skill.model_copy(update={"resource_path": str(package)})
    skill._resolved_skill = resolved_path(str(package))
    skill.id = skill._resolved_skill.candidate.id
    skill.load_name = skill._resolved_skill.candidate.load_name
    agent = _FakeAgent()

    assert activate_forced_skills(agent, [skill]) == ["word-documents"]
    assert skill.id in activated_skills(agent)
    corpus = " ".join(
        str(getattr(block, "hint", "") or getattr(block, "text", "") or "")
        for msg in agent.state.context
        for block in (msg.content or [])
    )
    assert '<activated-skill name="word-documents"' in corpus
    assert "步骤 1" in corpus


def test_member_runtime_includes_forced_but_not_shared_only() -> None:
    bound = _skill("bound-skill")
    shared = _skill("shared-only")
    forced = _skill("word-documents")
    ctx = SimpleNamespace(
        agent_config=SimpleNamespace(skills=["bound-skill"]),
        used_skills=[bound, shared, forced],
        forced_skills=[forced],
    )
    assert [skill.id for skill in _member_runtime_skills(ctx)] == ["bound-skill", "word-documents"]
