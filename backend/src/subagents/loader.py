"""Subagent Markdown frontmatter 加载器。"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

from src.subagents.models import SubagentDefinition, SubagentLoadError
from src.subagents.permission_policy import (
    is_supported_subagent_permission_mode,
    normalize_subagent_permission_mode,
)


_FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<yaml>.*?)\n---\s*\n?(?P<body>.*)\Z", re.DOTALL)
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_KNOWN_FIELDS = {
    "name",
    "description",
    "tools",
    "disallowedTools",
    "model",
    "permissionMode",
    "maxTurns",
    "skills",
    "mcpServers",
    "color",
    "background",
    "isolation",
    "hooks",
    "memory",
    "effort",
    "initialPrompt",
}


def load_subagent_file(path: Path, *, scope: str) -> tuple[SubagentDefinition | None, SubagentLoadError | None]:
    """解析单个 Subagent Markdown 文件。"""

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, SubagentLoadError(scope=scope, path=str(path), message=f"读取失败: {exc}")

    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return None, SubagentLoadError(scope=scope, path=str(path), message="缺少 YAML frontmatter")

    try:
        frontmatter = yaml.safe_load(match.group("yaml")) or {}
    except yaml.YAMLError as exc:
        return None, SubagentLoadError(scope=scope, path=str(path), message=f"YAML 解析失败: {exc}")
    if not isinstance(frontmatter, dict):
        return None, SubagentLoadError(scope=scope, path=str(path), message="YAML frontmatter 必须是对象")

    name = str(frontmatter.get("name") or "").strip()
    description = str(frontmatter.get("description") or "").strip()
    if not name:
        return None, SubagentLoadError(scope=scope, path=str(path), message="缺少必填字段 name")
    if not _NAME_RE.match(name):
        return None, SubagentLoadError(scope=scope, path=str(path), message="name 只能包含字母、数字、下划线或短横线")
    if not description:
        return None, SubagentLoadError(scope=scope, path=str(path), message="缺少必填字段 description")

    body = match.group("body").strip()
    metadata = {
        key: value
        for key, value in frontmatter.items()
        if key not in _KNOWN_FIELDS
    }
    unsupported = {
        key: frontmatter[key]
        for key in ("background", "isolation", "hooks", "memory", "effort", "initialPrompt", "mcpServers")
        if key in frontmatter
    }
    if unsupported:
        metadata["recognizedUnsupportedFields"] = sorted(unsupported.keys())
        metadata["unsupportedFieldValues"] = unsupported
    raw_permission_mode = str(frontmatter.get("permissionMode") or "default").strip() or "default"
    if not is_supported_subagent_permission_mode(raw_permission_mode):
        return None, SubagentLoadError(
            scope=scope,
            path=str(path),
            message=f"不支持的 permissionMode: {raw_permission_mode}",
        )
    permission_mode = normalize_subagent_permission_mode(raw_permission_mode)

    return (
        SubagentDefinition(
            name=name,
            description=description,
            prompt=body,
            scope=scope,
            path=str(path),
            tools=_normalize_optional_string_list(frontmatter.get("tools")),
            disallowed_tools=_normalize_string_list(frontmatter.get("disallowedTools")),
            model=str(frontmatter.get("model") or "inherit").strip() or "inherit",
            permission_mode=permission_mode,
            max_turns=_normalize_max_turns(frontmatter.get("maxTurns")),
            skills=_normalize_string_list(frontmatter.get("skills")),
            mcp_servers=_normalize_list(frontmatter.get("mcpServers")),
            color=str(frontmatter.get("color") or "").strip(),
            metadata=metadata,
            content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
        ),
        None,
    )


def scan_subagent_directory(directory: Path, *, scope: str) -> tuple[list[SubagentDefinition], list[SubagentLoadError]]:
    """递归扫描目录下的 Subagent Markdown 文件。"""

    if not directory.exists():
        return [], []
    if not directory.is_dir():
        return [], [SubagentLoadError(scope=scope, path=str(directory), message="Subagent 路径不是目录")]

    definitions: list[SubagentDefinition] = []
    errors: list[SubagentLoadError] = []
    for path in sorted(directory.rglob("*.md")):
        definition, error = load_subagent_file(path, scope=scope)
        if definition:
            definitions.append(definition)
        if error:
            errors.append(error)
    return definitions, errors


def _normalize_optional_string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    return _normalize_string_list(value)


def _normalize_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _normalize_max_turns(value: Any) -> int:
    try:
        return max(1, min(int(value or 30), 120))
    except (TypeError, ValueError):
        return 30
