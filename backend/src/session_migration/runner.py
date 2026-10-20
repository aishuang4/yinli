"""Tenant 遷移執行入口。啟動門與 CLI 都走這裡。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.config.runtime_paths import (
    global_agents_root,
    global_jobs_root,
    global_mcp_root,
    global_tools_root,
    session_root,
    subagent_root,
    tenant_root,
)
from src.session_migration.backup import write_backup
from src.session_migration.catalog_migrate import migrate_catalogs
from src.session_migration.conflicts import precheck
from src.session_migration.cutover import attempt_cutover
from src.session_migration.file_stage import stage_file
from src.session_migration.inventory import collect_inventory
from src.session_migration.owner_backfill import backfill_owners
from src.session_migration.session_backfill import backfill_session_context
from src.session_migration.state import cutover_state, migration_dir, set_migration_flag

logger = logging.getLogger(__name__)

_CATALOG_KINDS = (
    ("agents", "profiles", global_agents_root),
    ("mcp", "mcp", global_mcp_root),
    ("tools", "tools", global_tools_root),
    ("jobs", "jobs", global_jobs_root),
    ("subagents", "agents", subagent_root),
)


def _report(payload: dict[str, Any]) -> None:
    path = migration_dir() / "report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _fresh(inventory: dict[str, Any]) -> bool:
    counts = inventory["counts"]
    if counts["workspaces"] or counts["projects"]:
        return False
    if any(str(item.get("project_id") or "") for item in inventory["sessions"]):
        return False
    if any(str(item.get("project_id") or "") for item in inventory["jobs"]):
        return False
    return not any(item["kind"] in {"project-agents", "project-internal"} for item in inventory["definitions"])


def _source_roots(inventory: dict[str, Any]) -> list[Path]:
    from src.catalog.paths import workspace_catalog_root

    roots: list[Path] = []
    for workspace in inventory["workspaces"]:
        workspace_id = str(workspace.get("id") or "")
        if not workspace_id or workspace_id == "_system":
            continue
        roots.append(workspace_catalog_root(workspace_id))
    for project in inventory["projects"]:
        root = Path(str(project.get("root_path") or ""))
        if root.is_dir():
            roots.append(root / ".agents")
    return roots


def _ready(conn, inventory: dict[str, Any], manifest_path: Path) -> dict[str, bool]:
    from src.runtime.session_context import build_session_context
    from src.sessions.errors import SessionError

    sessions = {
        str(row["id"]): row
        for row in conn.execute("SELECT id, project_id, working_directory FROM chat_sessions").fetchall()
    }
    missing_directory = False
    for project in inventory["projects"]:
        root = Path(str(project.get("root_path") or ""))
        if not root.is_dir():
            continue
        for session in inventory["sessions"]:
            if str(session.get("project_id") or "") != str(project.get("id") or ""):
                continue
            current = sessions.get(str(session.get("id") or ""))
            if current is not None and not str(current["working_directory"] or ""):
                missing_directory = True
    jobs_ready = True
    for job in inventory["jobs"]:
        if str(job.get("status") or "") not in {"active", "pending", "pending_confirmation"}:
            continue
        if not str(job.get("created_from_session_id") or ""):
            jobs_ready = False
    studios_ready = True
    for studio in inventory["studios"]:
        if str(studio.get("status") or "") not in {"open", "paused"}:
            continue
        try:
            build_session_context(str(studio.get("chat_session_id") or ""))
        except SessionError:
            studios_ready = False
    manifest_ready = True
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in (manifest.get("files") or {}).values():
            if str(item.get("state") or "") in {"pending", "corrupt"}:
                manifest_ready = False
    return {
        "directories": not missing_directory,
        "jobs": jobs_ready,
        "studios": studios_ready,
        "manifest": manifest_ready,
        "sessions": len(sessions) == len(inventory["sessions"]),
    }


def run_tenant_migration(*, dry_run: bool = False) -> dict[str, Any]:
    """按固定九步遷移目前 tenant。dry-run 不寫檔。"""

    from src.storage.database import _connect, _lock

    if cutover_state()["cutover"] == "cutover":
        return {"status": "already_cutover"}
    with _lock, _connect() as conn:
        inventory = collect_inventory(conn, tenant_root=tenant_root())
        if _fresh(inventory):
            fresh = {"status": "fresh_cutover"}
            if dry_run:
                return fresh
        else:
            fresh = None
        conflicts = precheck(conn, tenant_root=tenant_root())
    if fresh is not None:
        set_migration_flag(True, schema_version_value=1)
        marker = migration_dir() / "complete.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"ok": True, "fresh": True}, ensure_ascii=False), encoding="utf-8")
        return fresh
    if dry_run:
        return {"status": "dry_run", "inventory": inventory, "conflicts": conflicts}
    set_migration_flag(True)
    with _lock, _connect() as conn:
        write_backup(conn, tenant_root=tenant_root(), destination=migration_dir() / "backup")
    if conflicts["blocking"]:
        result = {"status": "blocked", "conflicts": conflicts["conflicts"]}
        _report(result)
        return result
    blocked = _migrate_catalogs(inventory)
    if blocked is not None:
        _report(blocked)
        return blocked
    manifest_path = migration_dir() / "file-manifest.json"
    with _lock, _connect() as conn:
        backfill_session_context(conn)
        backfill_owners(conn)
        _stage_internal_files(conn, inventory, manifest_path)
    with _lock, _connect() as conn:
        checks = _ready(conn, inventory, manifest_path)
    ready = all(checks.values())
    outcome = attempt_cutover(
        ready=ready,
        pause=lambda: None,
        resume=lambda: None,
        marker=migration_dir() / "complete.json",
        switch_reads=lambda: set_migration_flag(True, schema_version_value=1),
    )
    result = {
        "status": "cutover" if outcome.get("cutover") else "not_ready",
        "checks": checks,
    }
    _report(result)
    return result


def _migrate_catalogs(inventory: dict[str, Any]) -> dict[str, Any] | None:
    roots = _source_roots(inventory)
    for kind, relative, destination in _CATALOG_KINDS:
        sources = [root / relative for root in roots]
        report = migrate_catalogs(sources, destination(), migration_dir() / "catalog" / kind)
        if report.get("conflicts"):
            return {"status": "blocked", "conflicts": report["conflicts"]}
    return None


def _stage_internal_files(conn, inventory: dict[str, Any], manifest_path: Path) -> list[str]:
    unmapped: list[str] = []
    job_owner = {
        str(row["id"]): str(row["created_from_session_id"] or "")
        for row in conn.execute("SELECT id, created_from_session_id FROM scheduled_jobs").fetchall()
    }
    studio_owner = {
        str(row["id"]): str(row["chat_session_id"] or "")
        for row in conn.execute("SELECT id, chat_session_id FROM group_studios").fetchall()
    }
    for item in inventory["definitions"]:
        if item["kind"] != "project-internal":
            continue
        source = Path(item["sourcePath"])
        mapped = _map_internal_file(source, job_owner, studio_owner)
        if mapped is None:
            unmapped.append(str(source))
            continue
        target_root, relative = mapped
        stage_file(source, target_root, relative, manifest_path)
    return unmapped


def _map_internal_file(
    source: Path,
    job_owner: dict[str, str],
    studio_owner: dict[str, str],
) -> tuple[Path, str] | None:
    parts = source.parts
    if ".agent-workstation" not in parts:
        return None
    index = parts.index(".agent-workstation")
    tail = parts[index + 1 :]
    if len(tail) < 2:
        return None
    kind, identifier = tail[0], tail[1]
    relative = "/".join(tail[2:])
    if kind == "sessions":
        return session_root(identifier), relative
    if kind == "jobs":
        owner = job_owner.get(identifier) or ""
        if not owner:
            return None
        return session_root(owner), "/".join(("jobs", identifier, relative))
    if kind == "group-studios":
        owner = studio_owner.get(identifier) or ""
        if not owner:
            return None
        return session_root(owner), "/".join(("group-studio", identifier, relative))
    return None


def ensure_tenant_cutover() -> dict[str, Any]:
    """啟動門。失敗時留下未切換狀態，不把異常拋給啟動流程。"""

    try:
        return run_tenant_migration(dry_run=False)
    except Exception as exc:
        logger.exception("tenant cutover failed")
        return {"status": "failed", "error": exc.__class__.__name__}
