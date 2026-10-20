"""回滾到舊讀路徑，並產生舊版可導入副本。不覆蓋新 Session 檔。"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any


def rollback_to_legacy(
    backup_dir: Path,
    export_dir: Path,
    session_root: Path,
    *,
    set_legacy_read: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """複製備份清單。Session root 裡已有的檔保持原樣。"""

    before = {path.relative_to(session_root).as_posix(): path.read_bytes() for path in session_root.rglob("*") if path.is_file()} if session_root.is_dir() else {}
    if set_legacy_read is not None:
        set_legacy_read()
    export_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    if backup_dir.is_dir():
        for path in sorted(backup_dir.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            target = export_dir / path.relative_to(backup_dir)
            if session_root in target.parents or target == session_root:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copyfile(path, target)
            copied.append(str(target))
    (export_dir / "legacy-import.json").write_text(
        json.dumps({"readPath": "legacy", "files": copied}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    after = {path.relative_to(session_root).as_posix(): path.read_bytes() for path in session_root.rglob("*") if path.is_file()} if session_root.is_dir() else {}
    return {"readPath": "legacy", "copied": copied, "sessionUnchanged": before == after}
