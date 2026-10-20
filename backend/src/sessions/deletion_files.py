"""Session 刪除時的受管檔案暫存與恢復。外部 working directory 不進入暫存。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from src.config.runtime_paths import session_root, tenant_root


def recovery_dir(session_id: str) -> Path:
    return tenant_root() / "session-recovery" / session_id


def stage_session_files(session_id: str, *, working_directory: str, external_paths: list[str]) -> Path:
    """複製 Session root，並寫下不會刪除的外部引用。"""

    destination = recovery_dir(session_id)
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    source = session_root(session_id)
    if source.exists() and not source.is_symlink():
        shutil.copytree(source, destination / "root")
    manifest = {
        "sessionId": session_id,
        "workingDirectory": working_directory,
        "external": external_paths,
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return destination


def restore_session_files(session_id: str) -> None:
    """把暫存的 Session root 拷回原位。"""

    staged = recovery_dir(session_id) / "root"
    target = session_root(session_id)
    if target.exists() and not target.is_symlink():
        shutil.rmtree(target)
    elif target.is_symlink():
        target.unlink()
    if staged.is_dir():
        shutil.copytree(staged, target)


def read_manifest(session_id: str) -> dict[str, Any]:
    path = recovery_dir(session_id) / "manifest.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
