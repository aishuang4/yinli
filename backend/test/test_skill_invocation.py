"""Chat 與定時任務共用的 Skill 注入材料。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.runtime.capabilities import SINGLE_SKILL_MAX_CHARS
from src.skills.invocation import (
    SkillInvocationError,
    extract_inline_skill_commands,
    load_skill_invocations,
    skill_runtime_directory,
)


def _skill(skill_id: str, *, name: str = "", content: str = "", resource_path: str = "", enabled: bool = True):
    return SimpleNamespace(
        id=skill_id,
        name=name or skill_id,
        content=content,
        resource_path=resource_path,
        enabled=enabled,
    )


def test_load_uses_frontmatter_name_and_keeps_full_markdown(tmp_path) -> None:
    package = tmp_path / "crew-qual-check"
    (package / "scripts").mkdir(parents=True)
    (package / "scripts" / "check.py").write_text("print(1)\n", encoding="utf-8")
    markdown = "---\nname: 证书核对\ndescription: 核对证书\n---\n\n原始步骤\n"
    (package / "SKILL.md").write_text(markdown, encoding="utf-8")
    skill = _skill("external-abc", content=markdown, resource_path=str(package))

    batch = load_skill_invocations([skill], strict=False)
    assert batch.missing == ()
    assert batch.loaded[0].name == "证书核对"
    assert batch.loaded[0].skill_id == "external-abc"
    assert batch.loaded[0].markdown == markdown.strip()
    assert batch.loaded[0].directory == str(package)
    assert batch.loaded[0].supporting_files == ("scripts/check.py",)


def test_strict_mode_rejects_duplicate_missing_and_budget() -> None:
    first = _skill("a", name="alpha", content="---\nname: alpha\ndescription: A\n---\n\nA")
    second = _skill("b", name="alpha", content="---\nname: alpha\ndescription: A\n---\n\nB")
    with pytest.raises(SkillInvocationError, match="duplicate_skill"):
        load_skill_invocations([first, second], strict=True)

    with pytest.raises(SkillInvocationError, match="skill_missing"):
        load_skill_invocations([_skill("gone", content="")], strict=True)

    huge = _skill("big", name="big", content="x" * (SINGLE_SKILL_MAX_CHARS + 1))
    with pytest.raises(SkillInvocationError, match="skill_context_budget_exceeded"):
        load_skill_invocations([huge], strict=False)


def test_extract_inline_skill_commands_keeps_the_rest_as_instruction() -> None:
    alpha = SimpleNamespace(id="pkg-a", name="alpha", enabled=True)
    beta = SimpleNamespace(id="pkg-b", name="beta", enabled=True)
    skills, instruction = extract_inline_skill_commands("请用 /alpha 核对 /beta 这份", [alpha, beta])
    assert [item.id for item in skills] == ["pkg-a", "pkg-b"]
    assert instruction == "请用 核对 这份"

    skills, instruction = extract_inline_skill_commands("/alpha /alpha 做", [alpha])
    assert [item.id for item in skills] == ["pkg-a"]
    assert instruction == "/alpha 做"

    extras = [SimpleNamespace(id=f"s{index}", name=f"s{index}", enabled=True) for index in range(6)]
    text = " ".join(f"/s{index}" for index in range(6)) + " 剩下"
    skills, instruction = extract_inline_skill_commands(text, extras)
    assert [item.id for item in skills] == ["s0", "s1", "s2", "s3", "s4"]
    assert "/s5" in instruction
    assert instruction.endswith("剩下")
    assert extract_inline_skill_commands("看看 /Users/foo", [alpha])[0] == []


def test_runtime_directory_uses_container_mount_for_docker(tmp_path) -> None:
    skill = _skill("word", resource_path=str(tmp_path / "word-documents"))
    guardrails = SimpleNamespace(execution_backend="docker")
    assert skill_runtime_directory(skill, guardrails) == "/workspace/skills/word-documents"
