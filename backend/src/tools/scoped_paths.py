"""工具路径解析 helper，统一把文件访问硬限制在 Agent scope 内。"""

from pathlib import Path
from typing import Any

from src.agents.runtime_types import WORKSPACE_DIR


def resolve_workspace_path(
    path: str | None,
    *,
    base_dir: str | None = None,
    scope_dir: str | None = None,
) -> str:
    """解析工具路径，并拒绝绝对路径、``..`` 或符号链接造成的 scope 越界。"""

    scope = Path(scope_dir or base_dir or WORKSPACE_DIR).expanduser().resolve()
    base = Path(base_dir or scope).expanduser().resolve()
    if not base.is_relative_to(scope):
        raise ValueError(f"Tool base directory is outside filesystem scope: {base}")

    value = Path(str(path or "")).expanduser()
    resolved = value.resolve() if value.is_absolute() else (base / value).resolve()
    if not resolved.is_relative_to(scope):
        raise ValueError(f"Path is outside filesystem scope: {resolved}")
    return str(resolved)


def resolved_tool_input(
    tool_input: dict[str, Any],
    key: str,
    *,
    base_dir: str | None = None,
    scope_dir: str | None = None,
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
        ),
    }
