"""工具路径解析 helper，统一把相对路径收窄到项目根。"""

import os
from typing import Any

from src.agents.runtime_types import WORKSPACE_DIR


def resolve_workspace_path(path: str | None, *, base_dir: str | None = None) -> str:
    """把绝对路径原样保留，把相对路径解析到指定工作目录。"""

    value = os.path.expanduser(str(path or ""))
    if os.path.isabs(value):
        return value
    return os.path.abspath(os.path.join(base_dir or WORKSPACE_DIR, value))


def resolved_tool_input(
    tool_input: dict[str, Any],
    key: str,
    *,
    base_dir: str | None = None,
) -> dict[str, Any]:
    """复制工具输入，并解析其中一个路径字段。"""

    if key not in tool_input or not tool_input.get(key):
        return tool_input
    return {
        **tool_input,
        key: resolve_workspace_path(str(tool_input.get(key) or ""), base_dir=base_dir),
    }
