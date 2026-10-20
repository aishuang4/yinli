"""跨卷複製先寫 Session root 內的隱藏 staging，fsync、校驗後再 rename。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"files": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _fsync_file(temporary)
    temporary.replace(path)


def stage_file(
    source: Path,
    session_root: Path,
    relative: str,
    manifest_path: Path,
    *,
    stop_after: str | None = None,
) -> dict[str, Any]:
    """任一步中斷後重跑都從 manifest 繼續。源檔保留，已完成目標不重複寫。"""

    manifest = _load(manifest_path)
    files: dict[str, Any] = manifest.setdefault("files", {})
    staging = session_root / ".staging" / relative
    target = session_root / relative
    entry = files.setdefault(
        relative,
        {
            "source": str(source),
            "target": str(target),
            "state": "pending",
            "sha256": "",
            "size": 0,
            "committed": False,
        },
    )
    if entry.get("committed") and target.is_file() and _digest(target) == entry.get("sha256"):
        return entry

    def persist(state: str, *, committed: bool = False) -> None:
        entry["state"] = state
        entry["committed"] = committed
        entry["source"] = str(source)
        entry["target"] = str(target)
        _save(manifest_path, manifest)

    if entry["state"] == "pending":
        if stop_after == "before_write":
            persist("pending")
            return entry
        staging.parent.mkdir(parents=True, exist_ok=True)
        data = source.read_bytes()
        staging.write_bytes(data)
        entry["sha256"] = hashlib.sha256(data).hexdigest()
        entry["size"] = len(data)
        persist("written")
        if stop_after == "before_fsync":
            return entry
    if entry["state"] == "written":
        _fsync_file(staging)
        persist("staged")
    if entry["state"] == "staged":
        if not staging.is_file() or _digest(staging) != entry["sha256"]:
            persist("corrupt")
            return entry
        persist("hashed")
        if stop_after == "before_rename":
            return entry
    if entry["state"] == "hashed":
        target.parent.mkdir(parents=True, exist_ok=True)
        if staging.is_file():
            staging.replace(target)
        persist("renamed")
        if stop_after == "before_manifest":
            return entry
    if entry["state"] == "renamed":
        if target.is_file() and _digest(target) == entry["sha256"]:
            persist("done", committed=True)
        else:
            persist("corrupt")
    return entry
