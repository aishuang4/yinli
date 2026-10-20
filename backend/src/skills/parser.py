"""唯一的 UTF-8/frontmatter 解析器；hash 始终取原始字节。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import yaml

from src.skills.models import SkillError

MAX_MANUAL_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ParsedManual:
    """解析时的瞬时值，正文不进入 catalog。"""

    metadata: dict
    markdown: str
    name: str
    description: str
    content_hash: str


def parse_manual(raw: bytes, directory_name: str) -> ParsedManual:
    """兼容 BOM 与缺省元数据，非法 YAML 不降级成普通正文。"""

    if len(raw) > MAX_MANUAL_BYTES:
        raise SkillError("skill_invalid", "SKILL.md 超过 1 MiB 限制")
    try:
        markdown = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SkillError("skill_invalid", "SKILL.md 必须使用 UTF-8 编码") from exc
    metadata: dict = {}
    body = markdown
    lines = markdown.splitlines(keepends=True)
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            raise SkillError("skill_invalid", "SKILL.md frontmatter 缺少结束分隔线")
        try:
            value = yaml.safe_load("".join(lines[1:end]))
        except yaml.YAMLError as exc:
            raise SkillError("skill_invalid", "SKILL.md frontmatter YAML 无效") from exc
        if value is not None and not isinstance(value, dict):
            raise SkillError("skill_invalid", "SKILL.md frontmatter 必须是对象")
        metadata = value or {}
        body = "".join(lines[end + 1:])
    name = metadata.get("name") or directory_name
    description = metadata.get("description") or ""
    if not isinstance(name, str) or not name.strip() or not isinstance(description, str):
        raise SkillError("skill_invalid", "Skill name/description 必须是文本")
    if not description.strip():
        description = next((line.strip() for line in body.splitlines()
                            if line.strip() and not re.match(r"^\s*(#|```|~~~|<!--)", line)), "")
    if not description.strip():
        raise SkillError("skill_invalid", "Skill 缺少可用描述")
    return ParsedManual(metadata, markdown, name.strip(), description.strip(), hashlib.sha256(raw).hexdigest())
