"""把已凍結的 Run snapshot 變成 Agent factory 的 cwd、檔案範圍與資源注入。

呼叫方之後改 Session grant 不會影響這份結果。空範圍、`/` 與進程 cwd 都不會補成預設目錄。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
from typing import Any

from src.agents.runtime_types import AgentGuardrails
from src.config.runtime_paths import session_artifacts_root, skill_packages_root
from src.sessions.errors import SessionError
from src.sessions.filesystem import AllowedDirectory, intersect_filesystem


@dataclass(frozen=True)
class FactorySnapshotView:
    """Factory 與 cache key 共用的 snapshot 投影。"""

    snapshot_id: str
    workspace_dir: str
    filesystem_scope: str
    write_scope_dir: str
    extra_filesystem_scopes: tuple[str, ...]
    skill_paths: tuple[str, ...]
    guardrails: AgentGuardrails
    mcp_servers: tuple[Any, ...]
    knowledge_tools: tuple[Any, ...]
    used_skills: tuple[Any, ...]


def apply_run_snapshot_to_factory(
    snapshot: dict[str, Any],
    *,
    guardrails: AgentGuardrails | None,
    skill_paths: list[str] | None,
    mcp_servers: list[Any] | None,
    knowledge_tools: list[Any] | None,
    used_skills: list[Any] | None = None,
    apply_filesystem: bool = True,
) -> FactorySnapshotView:
    """用 snapshot 收斂工作目錄、Skill、MCP 與 HTTP/知識庫，不讀當前 grant。"""

    cwd = _workspace_dir(snapshot)
    roots = _allowed_roots(snapshot) if apply_filesystem else []
    extras: list[str] = []
    if roots:
        grant = intersect_filesystem(
            guardrails.filesystem_scope if guardrails else "",
            [AllowedDirectory(Path(path), mode) for path, mode in roots],
        )
        if not any(cwd == root or cwd.is_relative_to(root) for root in grant.read_roots):
            raise SessionError("filesystem_scope_disjoint", "工作目錄超出凍結的目錄授權")
        extras = [str(root) for root in grant.read_roots if root != cwd]
    skill_ids = _resource_ids(snapshot, "skill")
    resolved_skills = tuple(_skill_paths(skill_ids, skill_paths))
    mcp_ids = set(_resource_ids(snapshot, "mcp"))
    http_ids = set(_resource_ids(snapshot, "http_tool", "knowledge"))
    next_guardrails = replace(
        guardrails or AgentGuardrails(),
        filesystem_scope=str(cwd),
    )
    return FactorySnapshotView(
        snapshot_id=str(snapshot.get("id") or ""),
        workspace_dir=str(cwd),
        filesystem_scope=str(cwd),
        write_scope_dir=str(cwd),
        extra_filesystem_scopes=tuple(extras),
        skill_paths=resolved_skills,
        guardrails=next_guardrails,
        mcp_servers=tuple(
            server for server in (mcp_servers or []) if _mcp_selected(server, mcp_ids)
        ),
        knowledge_tools=tuple(
            tool for tool in (knowledge_tools or []) if getattr(tool, "id", "") in http_ids
        ),
        used_skills=tuple(
            skill for skill in (used_skills or []) if getattr(skill, "id", "") in set(skill_ids)
        ),
    )


def _workspace_dir(snapshot: dict[str, Any]) -> Path:
    working = str(snapshot.get("workingDirectory") or "").strip()
    if not working or working == "/":
        root = session_artifacts_root(str(snapshot.get("sessionId") or ""))
        root.mkdir(parents=True, exist_ok=True)
        return root.resolve()
    cwd = Path(working).expanduser()
    try:
        cwd = cwd.resolve()
    except OSError as exc:
        raise SessionError("directory_unavailable", "工作目錄無法解析") from exc
    if cwd == Path("/").resolve() or not cwd.is_dir():
        raise SessionError("directory_unavailable", "凍結的工作目錄不可用")
    return cwd


def _allowed_roots(snapshot: dict[str, Any]) -> list[tuple[str, str]]:
    roots: list[tuple[str, str]] = []
    for item in snapshot.get("allowedRoots") or []:
        raw = str(item.get("normalizedPath") or "").strip()
        if not raw or raw == "/" or ".." in Path(raw).parts:
            continue
        mode = str(item.get("accessMode") or "read_write")
        if mode not in {"read", "read_write"}:
            continue
        roots.append((raw, mode))
    return roots


def _mcp_selected(server: Any, mcp_ids: set[str]) -> bool:
    """快照存的是目錄 ID。執行規格的 name 可能是 slug。"""

    server_id = str(getattr(server, "id", "") or "")
    name = str(getattr(server, "name", "") or "")
    if server_id in mcp_ids or name in mcp_ids:
        return True
    return any(name == item.replace("-", "_") for item in mcp_ids)


def _resource_ids(snapshot: dict[str, Any], *types: str) -> list[str]:
    items = (snapshot.get("resources") or {}).get("items") or []
    found: list[str] = []
    for item in items:
        if item.get("resourceType") not in types:
            continue
        identifier = str(item.get("resourceId") or "").strip()
        if identifier and identifier not in found:
            found.append(identifier)
    return found


def _skill_paths(skill_ids: list[str], supplied: list[str] | None) -> list[str]:
    chosen: list[str] = []
    seen: set[str] = set()
    for path in supplied or []:
        name = os.path.basename(os.path.normpath(path))
        if name in skill_ids and name not in seen:
            chosen.append(path)
            seen.add(name)
    packages = skill_packages_root()
    from src.skills.service import catalog_for

    by_id = {item.id: item for item in catalog_for(refresh=True).candidates}
    for skill_id in skill_ids:
        if skill_id in seen:
            continue
        item = by_id.get(skill_id)
        source = Path(item.source_path).parent if item is not None and item.source_path else packages / skill_id
        if (source / "SKILL.md").is_file():
            chosen.append(str(source))
            seen.add(skill_id)
    return chosen
