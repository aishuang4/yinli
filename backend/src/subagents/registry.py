"""Subagent registry，负责扫描和合并不同 scope 的定义。"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

from src.config.runtime_paths import default_project_root, subagent_root, user_home_root
from src.subagents.loader import scan_subagent_directory
from src.subagents.models import (
    SUBAGENT_COMPAT_PROJECT_DIRS,
    SUBAGENT_COMPAT_USER_DIRS,
    SubagentCatalog,
    SubagentDefinition,
    SubagentLoadError,
)


def load_subagent_catalog(
    *,
    workspace_dir: str | None = None,
    user_dir: str | None = None,
    user_home_dir: Path | None = None,
) -> SubagentCatalog:
    """加载当前工作区可用的 Subagent 定义。

    M1 只实现项目级和用户级 scope。项目级覆盖用户级。
    hosted 下 user_home_root() 为 None，跳过 HOME scope（§12.7）。
    """

    workspace = Path(workspace_dir) if workspace_dir else default_project_root()
    user = Path(user_dir).expanduser() if user_dir else subagent_root()
    home = user_home_dir if user_home_dir is not None else user_home_root()

    user_definitions: list[SubagentDefinition] = []
    user_errors: list[SubagentLoadError] = []
    for relative_dir in ("", *SUBAGENT_COMPAT_USER_DIRS):
        definitions, errors = scan_subagent_directory(user / relative_dir, scope="user")
        user_definitions.extend(
            replace(definition, enabled=False) if relative_dir else definition
            for definition in definitions
        )
        user_errors.extend(errors)
    home_definitions: list[SubagentDefinition] = []
    home_errors: list[SubagentLoadError] = []
    if home is not None:
        for relative_dir in SUBAGENT_COMPAT_USER_DIRS:
            definitions, errors = scan_subagent_directory(home / relative_dir, scope="user")
            home_definitions.extend(replace(definition, enabled=False) for definition in definitions)
            home_errors.extend(errors)
    project_definitions: list[SubagentDefinition] = []
    project_errors: list[SubagentLoadError] = []
    for relative_dir in SUBAGENT_COMPAT_PROJECT_DIRS:
        definitions, errors = scan_subagent_directory(workspace / relative_dir, scope="project")
        project_definitions.extend(definitions)
        project_errors.extend(errors)

    definitions: dict[str, SubagentDefinition] = {}
    warnings: list[str] = []
    errors: list[SubagentLoadError] = [*user_errors, *home_errors, *project_errors]

    _merge_scope(definitions, warnings, user_definitions, scope_label="user", override_existing=False)
    _merge_scope(definitions, warnings, home_definitions, scope_label="user", override_existing=False)
    _merge_scope(definitions, warnings, project_definitions, scope_label="project", override_existing=True)

    fingerprint = _fingerprint(definitions)
    return SubagentCatalog(
        definitions=definitions,
        errors=errors,
        warnings=warnings,
        fingerprint=fingerprint,
    )


def _merge_scope(
    definitions: dict[str, SubagentDefinition],
    warnings: list[str],
    incoming: list[SubagentDefinition],
    *,
    scope_label: str,
    override_existing: bool,
) -> None:
    seen_in_scope: set[str] = set()
    for definition in incoming:
        if definition.name in seen_in_scope:
            warnings.append(f"{scope_label} scope 内重复 Subagent name: {definition.name}，已忽略 {definition.path}")
            continue
        seen_in_scope.add(definition.name)
        if definition.name in definitions and not override_existing:
            warnings.append(f"Subagent {definition.name} 已存在，已忽略 {definition.path}")
            continue
        if definition.name in definitions and override_existing:
            warnings.append(f"Subagent {definition.name} 被更高优先级定义覆盖: {definition.path}")
        definitions[definition.name] = definition


def _fingerprint(definitions: dict[str, SubagentDefinition]) -> str:
    if not definitions:
        return "empty"
    raw = "|".join(
        f"{name}:{definition.scope}:{definition.path}:{definition.content_hash}"
        for name, definition in sorted(definitions.items())
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
