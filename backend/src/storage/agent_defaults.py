"""持久 Agent 默认值与历史文件范围归一化。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def normalize_guardrails(
    data: dict[str, Any],
    *,
    defaults: dict[str, Any],
    project_root: Path,
) -> dict[str, Any]:
    """归一化历史 guardrails 默认值，避免旧根目录或打包临时目录继续暴露。"""

    normalized = {**defaults, **data}
    filesystem_scope = str(normalized.get("filesystem_scope") or "").strip()
    if filesystem_scope == "/" or is_pyinstaller_temp_path(filesystem_scope):
        normalized["filesystem_scope"] = str(project_root)
    return normalized


def is_pyinstaller_temp_path(value: str) -> bool:
    """识别历史打包版本写入的 PyInstaller 临时目录。"""

    return any(part.startswith("_MEI") for part in Path(value).parts)


def is_macos_ephemeral_path(value: str) -> bool:
    """识别不应作为安装版默认 Project 的 macOS 临时目录。"""

    path = Path(value).expanduser()
    return path.is_relative_to(Path("/private/var/folders")) or path.is_relative_to(Path("/var/folders"))


def is_stale_desktop_default_project_path(value: str) -> bool:
    """判断历史安装版默认 Project 是否仍指向不可复用的临时位置。"""

    # 安装包首次运行或更换 userData 位置后，旧数据库可能保留已不存在的根目录。
    # 默认 Project 应迁移到当前桌面用户数据目录，由 runtime_paths 负责创建。
    path = Path(value).expanduser()
    return (
        not value.strip()
        or is_pyinstaller_temp_path(value)
        or is_macos_ephemeral_path(value)
        or (not path.is_dir() and "agent-workstation" in str(path).lower())
    )
