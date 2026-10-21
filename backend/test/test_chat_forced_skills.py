"""Chat 发送框强制开启 Skill：校验、并入 catalog、用户附注与预启用。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.server.routes.chat.prompts import (
    _build_skill_context_prompt,
    build_stacked_skill_user_message,
    compose_turn_user_text,
)
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
    name: str = "",
    enabled: bool = True,
    content: str = "",
    available_to_all_agents: bool = True,
    resource_path: str = "",
) -> Skill:
    return Skill(
        id=skill_id,
        name=name or skill_id,
        description=f"{skill_id} desc",
        resourcePath=resource_path,
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
    skills = {
        "word-documents": _skill("word-documents", content=SKILL_BODY),
        "yinli-charts": _skill("yinli-charts", content="# charts"),
        "private-pack": _skill("private-pack", available_to_all_agents=False),
        "disabled-pack": _skill("disabled-pack", enabled=False),
    }
    monkeypatch.setattr(
        "src.server.routes.chat.run_context.get_installed_skill",
        lambda sid: skill if (skill := skills.get(sid)) is not None and skill.enabled else None,
    )
    return skills


def test_resolve_forced_skills_requires_installed_and_project(installed) -> None:
    resolved = resolve_forced_runtime_skills(
        ["word-documents", "word-documents"],
        project_resources=_AllowAll(),
    )
    assert [skill.id for skill in resolved] == ["word-documents"]

    with pytest.raises(ValueError, match="不存在或未启用"):
        resolve_forced_runtime_skills(["missing-skill"], project_resources=_AllowAll())

    forced_private = resolve_forced_runtime_skills(
        ["private-pack"],
        project_resources=_AllowSome(set()),
    )
    assert [skill.id for skill in forced_private] == ["private-pack"]

    shared = resolve_forced_runtime_skills(
        ["word-documents"],
        project_resources=_AllowSome(set()),
    )
    assert [skill.id for skill in shared] == ["word-documents"]


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


def test_stacked_skill_user_message_puts_instruction_before_bodies() -> None:
    skills = [
        _skill(
            "pkg-a",
            name="alpha",
            content="---\nname: alpha\ndescription: A\n---\n\nBODY_ALPHA",
        ),
        _skill(
            "pkg-b",
            name="beta",
            content="---\nname: beta\ndescription: B\n---\n\nBODY_BETA",
        ),
    ]
    text = build_stacked_skill_user_message(skills, "帮我做 XYZ")
    instruction = text.index("User instruction: 帮我做 XYZ")
    alpha = text.index('[Loaded as part of the stacked skill invocation "alpha".]')
    beta = text.index('[Loaded as part of the stacked skill invocation "beta".]')
    assert instruction < alpha < text.index("BODY_ALPHA") < beta < text.index("BODY_BETA")
    assert 'loading 2 skills together' in text
    assert "Skills loaded: alpha, beta" in text
    assert "/alpha /beta" in text
    assert "pkg-a" not in text
    assert "pkg-b" not in text
    assert "Treat every skill below as active guidance" in text
    sent = compose_turn_user_text(message="帮我做 XYZ", skill_message=text, note="<attached_files>\n</attached_files>")
    assert sent.startswith("[IMPORTANT:")
    assert sent.index("BODY_BETA") < sent.index("<attached_files>")


def test_stacked_skill_user_message_skips_duplicate_and_missing() -> None:
    loaded = _skill("pkg-a", name="alpha", content="---\nname: alpha\ndescription: A\n---\n\nBODY_ALPHA")
    duplicate = _skill("pkg-a2", name="alpha", content="---\nname: alpha\ndescription: A\n---\n\nBODY_DUP")
    missing = _skill("gone", name="gone", content="")
    text = build_stacked_skill_user_message([loaded, duplicate, missing], "go")
    assert text.count("BODY_ALPHA") == 1
    assert "BODY_DUP" not in text
    assert "Skills missing (skipped): gone" in text
    assert "loading 1 skills together" in text
    assert build_stacked_skill_user_message([missing], "go") == ""


def test_skill_context_prompt_mentions_stacked_bundle() -> None:
    text = _build_skill_context_prompt()
    assert "stacked skill bundle" in text
    assert "<activated-skill>" in text


def test_activate_forced_skills_writes_body_into_context(tmp_path, monkeypatch) -> None:
    package = tmp_path / "word-documents"
    package.mkdir()
    (package / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")
    skill = _skill("word-documents")
    skill = skill.model_copy(update={"resource_path": str(package)})
    agent = _FakeAgent()

    assert activate_forced_skills(agent, [skill]) == ["word-documents"]
    assert "word-documents" in activated_skills(agent)
    corpus = " ".join(
        str(getattr(block, "hint", "") or getattr(block, "text", "") or "")
        for msg in agent.state.context
        for block in (msg.content or [])
    )
    assert "<activated-skill name=\"word-documents\">" in corpus
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
