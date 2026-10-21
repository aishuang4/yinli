"""持久定义文件 catalog。

禁止本包 import `src.server.routes`。
本模块不在 import 时加载 loader，避免 factory → scoped_paths → catalog 循环。
"""

from src.catalog.paths import (
    extra_filesystem_scopes_for_workspace,
    system_overlay_root,
)

__all__ = [
    "catalog_fingerprint",
    "delete_agent",
    "ensure_agent_file_catalog",
    "extra_filesystem_scopes_for_workspace",
    "get_profile_invalid_error",
    "get_registered_agent",
    "invalidate_catalog",
    "is_agent_catalog_migrated",
    "list_registered_agents",
    "refresh_project_index",
    "save_agent",
    "system_overlay_root",
]


def __getattr__(name: str):
    if name in {
        "catalog_fingerprint",
        "delete_agent",
        "get_profile_invalid_error",
        "get_registered_agent",
        "invalidate_catalog",
        "list_registered_agents",
        "save_agent",
    }:
        from src.catalog import loader

        return getattr(loader, name)
    if name in {"ensure_agent_file_catalog", "is_agent_catalog_migrated"}:
        from src.catalog import migrate_agents

        return getattr(migrate_agents, name)
    if name == "refresh_project_index":
        from src.catalog import index as catalog_index

        return catalog_index.refresh_project_index
    raise AttributeError(name)
