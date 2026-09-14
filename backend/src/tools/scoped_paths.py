"""工具路径解析 helper，统一把文件访问硬限制在 Agent scope 内。"""

from pathlib import Path
from typing import Any

from src.agents.runtime_types import WORKSPACE_DIR
from src.catalog.paths import is_forbidden_extra_scope


def _normalized_extra_scopes(extra_scopes: list[str] | None) -> list[Path]:
    extras: list[Path] = []
    for item in extra_scopes or []:
        extra = Path(str(item)).expanduser()
        if is_forbidden_extra_scope(extra):
            raise ValueError(f"Forbidden extra filesystem scope: {extra}")
        if not extra.exists() or not extra.is_dir():
            continue
        extras.append(extra.resolve())
    return extras


def _is_allowed(resolved: Path, scope: Path, extras: list[Path]) -> bool:
    if resolved.is_relative_to(scope):
        return True
    return any(resolved.is_relative_to(extra) for extra in extras)


def resolve_workspace_path(
    path: str | None,
    *,
    base_dir: str | None = None,
    scope_dir: str | None = None,
    extra_scopes: list[str] | None = None,
) -> str:
    """解析工具路径，并拒绝绝对路径、``..`` 或符号链接造成的 scope 越界。"""

    scope = Path(scope_dir or base_dir or WORKSPACE_DIR).expanduser().resolve()
    base = Path(base_dir or scope).expanduser().resolve()
    if not base.is_relative_to(scope):
        raise ValueError(f"Tool base directory is outside filesystem scope: {base}")

    extras = _normalized_extra_scopes(extra_scopes)
    value = Path(str(path or "")).expanduser()
    resolved = value.resolve() if value.is_absolute() else (base / value).resolve()
    if not _is_allowed(resolved, scope, extras):
        raise ValueError(f"Path is outside filesystem scope: {resolved}")
    return str(resolved)


def resolve_existing_workspace_path(
    path: str | None,
    *,
    base_dir: str | None = None,
    scope_dir: str | None = None,
    extra_scopes: list[str] | None = None,
) -> str:
    """解析只读路径；Session cwd 中不存在时兼容 Project scope 相对路径。"""

    resolved = resolve_workspace_path(
        path, base_dir=base_dir, scope_dir=scope_dir, extra_scopes=extra_scopes,
    )
    value = Path(str(path or "")).expanduser()
    if value.is_absolute() or Path(resolved).exists() or not base_dir or not scope_dir:
        return resolved

    extras = _normalized_extra_scopes(extra_scopes)
    scope = Path(scope_dir).expanduser().resolve()
    scope_relative = (scope / value).resolve()
    if _is_allowed(scope_relative, scope, extras) and scope_relative.exists():
        return str(scope_relative)
    return resolved


def resolved_tool_input(
    tool_input: dict[str, Any],
    key: str,
    *,
    base_dir: str | None = None,
    scope_dir: str | None = None,
    extra_scopes: list[str] | None = None,
) -> dict[str, Any]:
    """复制工具输入，并解析其中一个路径字段。"""

    if key not in tool_input or not tool_input.get(key):
        return tool_input
    return {
        **tool_input,
        key: resolve_workspace_path(
            str(tool_input.get(key) or ""),
            base_dir=base_dir,
            scope_dir=scope_dir,
            extra_scopes=extra_scopes,
        ),
    }
