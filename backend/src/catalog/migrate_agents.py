"""Agent 文件 catalog 的启动钩子。

旧库封存，不在启动时把表记录写成 profile。显式复制入口是 `yinli agents import-legacy`。
"""

from __future__ import annotations

from src.catalog.loader import invalidate_catalog


def is_agent_catalog_migrated() -> bool:
    """文件目录已是权威，调用方不必再回填旧表。"""

    return True


def ensure_agent_file_catalog() -> None:
    """启动时只让扫描缓存失效，不读取、不导出旧表。"""

    invalidate_catalog()
