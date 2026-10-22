"""启动时封存旧库和旧 workspaces 目录。新代码不读取封存内容。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_checked: set[Path] = set()


def archive_legacy_layout(data_dir: Path) -> Path | None:
    """每个数据目录只检查一次。没有旧痕迹时不创建封存目录。"""

    key = data_dir.resolve()
    if key in _checked:
        return None
    _checked.add(key)
    tenant = data_dir.parent
    old_db = data_dir / "agent_workstation.db"
    workspaces = tenant / "workspaces"
    state_db = data_dir / "state.db"
    move_state = state_db.is_file() and _has_workspaces_table(state_db)
    if not old_db.is_file() and not workspaces.exists() and not move_state:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = tenant / "backups" / f"legacy-{stamp}"
    dest.mkdir(parents=True, exist_ok=True)
    if old_db.is_file():
        _checkpoint_and_move(old_db, dest)
    if move_state:
        _checkpoint_and_move(state_db, dest)
    if workspaces.exists():
        workspaces.rename(dest / "workspaces")
    _write_pending_notice(dest)
    return dest


def forget_archive_checks() -> None:
    """测试 teardown 用。"""

    _checked.clear()


def pending_legacy_notice(data_dir: Path) -> dict[str, object]:
    """前端只展示这里的结果。没有未确认封存时 pending 为 false。"""

    payload = _read_notice(data_dir)
    if payload is None or payload.get("acknowledged") is True:
        return {"pending": False}
    path = str(payload.get("path") or "")
    if not path:
        return {"pending": False}
    return {
        "pending": True,
        "path": path,
        "title": "历史对话已封存",
        "message": (
            "本版本不保留旧对话。旧数据库和旧工作区目录已封存到下面的位置，"
            "可用任意 SQLite 客户端打开其中的数据库文件。封存目录不会自动删除。"
        ),
    }


def acknowledge_legacy_notice(data_dir: Path) -> dict[str, object]:
    """用户确认后不再弹出。封存目录本身保留。"""

    notice = _notice_path(data_dir)
    payload = _read_notice(data_dir)
    if payload is None:
        return {"pending": False}
    payload["acknowledged"] = True
    notice.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {"pending": False}


def _notice_path(data_dir: Path) -> Path:
    return data_dir.parent / "backups" / "legacy-notice.json"


def _write_pending_notice(dest: Path) -> None:
    notice = dest.parent / "legacy-notice.json"
    payload = {
        "path": str(dest),
        "archivedAt": datetime.now(timezone.utc).isoformat(),
        "acknowledged": False,
    }
    notice.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_notice(data_dir: Path) -> dict[str, object] | None:
    notice = _notice_path(data_dir)
    if not notice.is_file():
        return None
    try:
        payload = json.loads(notice.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _has_workspaces_table(path: Path) -> bool:
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workspaces'"
        ).fetchone()
    except sqlite3.Error:
        return False
    finally:
        conn.close()
    return row is not None


def _checkpoint_and_move(path: Path, dest: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    path.rename(dest / path.name)
    for suffix in ("-wal", "-shm"):
        side = Path(f"{path}{suffix}")
        if side.exists():
            side.rename(dest / side.name)
