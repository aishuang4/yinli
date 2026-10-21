"""Chat 與定時任務共用的 Skill 注入材料。

正文來自 Skill.content。安裝刷新和凍結解析都已經讀過 SKILL.md，
與 AgentScope `Skill` viewer 返回的 markdown 是同一份。這裡不另寫 viewer。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.catalog.skills import frontmatter_identity, read_package_markdown
from src.runtime.capabilities import SINGLE_SKILL_MAX_CHARS, TOTAL_SKILL_MAX_CHARS

# 與前端 forcedSkills.ts 的 MAX_FORCED_SKILLS 保持一致。
MAX_STACKED_SKILLS = 5
_SUPPORTING_DIRS = ("references", "templates", "scripts", "assets")
# 斜杠後不能再有斜杠，避免把 /Users/foo 這種路徑當成 Skill。
_INLINE_SKILL = re.compile(r"(?<!\S)/([^\s/]+)")


class SkillInvocationError(Exception):
    """一份 Skill 沒能收成可注入的正文。"""

    def __init__(self, code: str, skill_name: str = "") -> None:
        self.code = code
        self.skill_name = skill_name
        detail = f"{code}: {skill_name}" if skill_name else code
        super().__init__(detail)


@dataclass(frozen=True)
class SkillInvocation:
    """一份已讀好的 Skill。markdown 是 SKILL.md 原文。"""

    name: str
    markdown: str
    directory: str
    supporting_files: tuple[str, ...]
    skill_id: str


@dataclass(frozen=True)
class SkillInvocationBatch:
    """一輪要注入的 Skill。missing / disabled 只在非嚴格模式出現。"""

    loaded: tuple[SkillInvocation, ...]
    missing: tuple[str, ...]
    disabled: tuple[str, ...]


def extract_inline_skill_commands(
    message: str,
    skills: list[Any] | tuple[Any, ...] | None,
) -> tuple[list[Any], str]:
    """從用戶原文按出現順序取最多 5 個斜杠 Skill，其餘文字當指令。

    對不上的斜杠、重複的第二次、第 6 個及之後都留在指令裡。
    """

    lookup: dict[str, Any] = {}
    for skill in skills or []:
        if getattr(skill, "enabled", True) is False:
            continue
        for key in (getattr(skill, "name", ""), getattr(skill, "id", "")):
            normalized = str(key or "").strip().casefold()
            if normalized and normalized not in lookup:
                lookup[normalized] = skill
    chosen: list[Any] = []
    seen: set[str] = set()
    spans: list[tuple[int, int]] = []
    for match in _INLINE_SKILL.finditer(message or ""):
        skill = lookup.get(match.group(1).casefold())
        if skill is None:
            continue
        skill_id = str(getattr(skill, "id", "") or "")
        if not skill_id or skill_id in seen:
            continue
        if len(chosen) >= MAX_STACKED_SKILLS:
            break
        seen.add(skill_id)
        chosen.append(skill)
        spans.append(match.span())
    instruction = message or ""
    for start, end in reversed(spans):
        instruction = instruction[:start] + instruction[end:]
    instruction = re.sub(r"[ \t]{2,}", " ", instruction).strip()
    return chosen, instruction


def skill_runtime_directory(skill: object, guardrails: object | None = None) -> str:
    """本地披露宿主包目錄；容器執行披露掛載後的 /workspace/skills 路徑。"""

    raw = str(getattr(skill, "resource_path", "") or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if path.name == "SKILL.md":
        path = path.parent
    backend = str(getattr(guardrails, "execution_backend", "") or "")
    from src.tenancy.context import current_tenant

    if current_tenant().is_hosted or backend == "docker":
        return f"/workspace/skills/{path.name}"
    return str(path)


def load_skill_invocations(
    skills: list[Any] | tuple[Any, ...] | None,
    *,
    guardrails: object | None = None,
    strict: bool = True,
) -> SkillInvocationBatch:
    """按傳入順序讀 Skill。同名只留第一次。

    strict 用於定時任務：缺正文、未啟用或同名會直接失敗。
    Chat 用 strict=False：缺的和停用的記在批次裡，呼叫端決定要不要寫進總頭。
    單份或合計超出預算時兩種模式都失敗，不截斷。
    """

    loaded: list[SkillInvocation] = []
    missing: list[str] = []
    disabled: list[str] = []
    seen: set[str] = set()
    total = 0
    for skill in skills or []:
        hint = str(getattr(skill, "name", "") or getattr(skill, "id", "") or "").strip()
        if getattr(skill, "enabled", True) is False:
            _reject_or_record(disabled, hint or "missing", "skill_disabled", strict)
            continue
        markdown = str(getattr(skill, "content", "") or "").strip()
        resource = str(getattr(skill, "resource_path", "") or "")
        if not markdown and resource:
            markdown = read_package_markdown(resource).strip()
        if not markdown:
            _reject_or_record(missing, hint or "missing", "skill_missing", strict)
            continue
        name, _description = frontmatter_identity(markdown, hint or "missing")
        if name in seen:
            if strict:
                raise SkillInvocationError("duplicate_skill", name)
            continue
        if len(markdown) > SINGLE_SKILL_MAX_CHARS or total + len(markdown) > TOTAL_SKILL_MAX_CHARS:
            raise SkillInvocationError("skill_context_budget_exceeded", name)
        total += len(markdown)
        seen.add(name)
        package = _package_dir(resource)
        loaded.append(SkillInvocation(
            name=name,
            markdown=markdown,
            directory=skill_runtime_directory(skill, guardrails),
            supporting_files=_supporting_files(package),
            skill_id=str(getattr(skill, "id", "") or ""),
        ))
    return SkillInvocationBatch(tuple(loaded), tuple(missing), tuple(disabled))


def _reject_or_record(bucket: list[str], name: str, code: str, strict: bool) -> None:
    if strict:
        raise SkillInvocationError(code, name)
    bucket.append(name)


def _package_dir(resource: str) -> Path | None:
    if not resource:
        return None
    path = Path(resource)
    if path.name == "SKILL.md":
        path = path.parent
    return path if path.is_dir() else None


def _supporting_files(package: Path | None) -> tuple[str, ...]:
    if package is None:
        return ()
    found: list[str] = []
    for subdir in _SUPPORTING_DIRS:
        root = package / subdir
        if not root.is_dir():
            continue
        for file in sorted(root.rglob("*")):
            if file.is_file() and not file.is_symlink():
                found.append(str(file.relative_to(package)))
    return tuple(found)
