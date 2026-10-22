"""Agent 工作目录的范围计算，以及持久沙箱里的 profile 只读副本。"""
from __future__ import annotations


import os
import shutil
from pathlib import Path

from src.config.runtime_paths import project_default_root


def normalize_filesystem_scope(scope: str | None) -> str:
    """空范围和文件系统根都退回默认 Project 目录。"""

    default_scope = str(project_default_root())
    raw = scope or default_scope
    expanded = os.path.abspath(os.path.expanduser(raw))
    if expanded == os.path.abspath(os.sep):
        return default_scope
    return expanded


def _safe_workspace_dir(guardrails: object | None) -> str:
    """读取护栏上的文件范围。护栏为空时使用默认 Project 目录。"""

    scope = getattr(guardrails, "filesystem_scope", None) if guardrails is not None else None
    return normalize_filesystem_scope(scope)


def _safe_explicit_dir(path: str | None) -> str | None:
    """规范化显式目录；空值或根目录视为未设置。"""

    if not str(path or "").strip():
        return None
    expanded = os.path.abspath(os.path.expanduser(path))
    if expanded == os.path.abspath(os.sep):
        return None
    return expanded


def _resolve_write_tool_base(workspace_dir: str | None, write_scope: str) -> str:
    """Write/Edit 的 base 必须位于 scope 内。

    Team Member 的 write_scope 是 session 下的私有子目录。若仍用更宽的 session
    当 base，权限检查会判定 base 越界并抛 ValueError，直接打断整个 Member session。
    """

    scope = Path(write_scope).expanduser().resolve()
    if not str(workspace_dir or "").strip():
        return str(scope)
    workspace = Path(workspace_dir).expanduser().resolve()
    if workspace == scope or workspace.is_relative_to(scope):
        return str(workspace)
    return str(scope)


def _seed_catalog_profiles_readonly(host_workdir: Path, extra_filesystem_scopes: list[str]) -> None:
    """把 host catalog profiles 只读复制进持久 Docker sandbox，不回写。"""

    dest = host_workdir / ".agents" / "profiles"
    dest.mkdir(parents=True, exist_ok=True)
    for scope in extra_filesystem_scopes:
        source = Path(scope) / "profiles"
        if not source.is_dir():
            continue
        for item in source.glob("*.md"):
            target = dest / item.name
            shutil.copy2(item, target)
