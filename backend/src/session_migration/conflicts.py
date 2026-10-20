"""遷移衝突預檢。有阻斷衝突時不寫新 schema 事實。"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.session_migration.inventory import collect_inventory

_BLOCKING = {
    "same_id_different_body",
    "path_out_of_bounds",
    "missing_project",
    "missing_session",
    "active_job_without_source",
}
_NAME_RE = re.compile(r"(?m)^name:\s*[\"']?([^\"'\n]+)")
_ACTIVE_JOB = {"active", "pending_confirmation"}


@dataclass
class Conflict:
    """一條預檢衝突。"""

    code: str
    message: str
    blocking: bool
    subjects: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "blocking": self.blocking,
            "subjects": self.subjects,
        }


def _owner_session_id(conn: sqlite3.Connection, job_id: str) -> str:
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(scheduled_jobs)").fetchall()}
    if "owner_session_id" not in columns:
        return ""
    row = conn.execute(
        "SELECT owner_session_id FROM scheduled_jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        return ""
    return str(row[0] or "")


def _bounded(path: Path, *, tenant_root: Path) -> bool:
    if not str(path) or str(path) == "/":
        return False
    if ".." in path.parts:
        return False
    if path.is_symlink():
        resolved = path.resolve()
        if not resolved.is_relative_to(tenant_root.resolve()):
            return False
    return True


def _profile_groups(definitions: list[dict[str, str]]) -> tuple[dict[str, list[tuple[str, str]]], dict[str, set[str]]]:
    by_id: dict[str, list[tuple[str, str]]] = {}
    names: dict[str, set[str]] = {}
    for item in definitions:
        source = Path(item["sourcePath"])
        if source.suffix != ".md" or source.parent.name != "profiles":
            continue
        body = source.read_text(encoding="utf-8", errors="ignore")
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        by_id.setdefault(source.stem, []).append((str(source), digest))
        match = _NAME_RE.search(body)
        if match:
            names.setdefault(match.group(1).strip(), set()).add(source.stem)
    return by_id, names


def precheck(conn: sqlite3.Connection, *, tenant_root: Path) -> dict[str, Any]:
    """只讀預檢。回傳衝突，不建立表、不回填 Session。"""

    before = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    inventory = collect_inventory(conn, tenant_root=tenant_root)
    conflicts: list[Conflict] = []
    project_ids = {str(item["id"]) for item in inventory["projects"]}
    session_ids = {str(item["id"]) for item in inventory["sessions"]}
    for project in inventory["projects"]:
        root = Path(str(project.get("root_path") or ""))
        if not _bounded(root, tenant_root=tenant_root):
            conflicts.append(Conflict(
                code="path_out_of_bounds",
                message="Project root 越界或不可用",
                blocking=True,
                subjects=[str(project["id"])],
            ))
    for session in inventory["sessions"]:
        project_id = str(session.get("project_id") or "")
        if project_id and project_id not in project_ids:
            conflicts.append(Conflict(
                code="missing_project",
                message="Session 指向不存在的 Project",
                blocking=True,
                subjects=[str(session["id"]), project_id],
            ))
    for job in inventory["jobs"]:
        source = str(job.get("created_from_session_id") or "")
        if source and source not in session_ids:
            conflicts.append(Conflict(
                code="missing_session",
                message="Job 指向不存在的 Session",
                blocking=True,
                subjects=[str(job["id"]), source],
            ))
        owner = _owner_session_id(conn, str(job["id"]))
        if str(job.get("status") or "") in _ACTIVE_JOB and not source and not owner:
            conflicts.append(Conflict(
                code="active_job_without_source",
                message="active Job 沒有來源 Session",
                blocking=True,
                subjects=[str(job["id"])],
            ))
    by_id, names = _profile_groups(inventory["definitions"])
    for agent_id, versions in by_id.items():
        digests = {digest for _path, digest in versions}
        if len(versions) > 1 and len(digests) > 1:
            conflicts.append(Conflict(
                code="same_id_different_body",
                message="相同 Agent ID 的正文不同",
                blocking=True,
                subjects=[agent_id, *[path for path, _digest in versions]],
            ))
    for name, ids in names.items():
        if len(ids) > 1:
            conflicts.append(Conflict(
                code="same_name_multiple_ids",
                message="同名 Agent 對應多個 ID，保留兩者、不按名稱合併",
                blocking=False,
                subjects=[name, *sorted(ids)],
            ))
    after = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    if after != before:
        raise RuntimeError("預檢不允許改變 schema")
    blocking = [item for item in conflicts if item.blocking]
    return {
        "conflicts": [item.to_dict() for item in conflicts],
        "blocking": bool(blocking),
        "schemaChanged": False,
        "writesSchemaFacts": False,
    }
