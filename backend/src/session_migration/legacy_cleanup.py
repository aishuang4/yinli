"""舊外部隱藏目錄的清理報告。預設 dry-run，不刪使用者業務檔。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

_HIDDEN = (".agent-workstation", ".agents")


def plan_legacy_cleanup(base: Path, *, apply: bool = False) -> dict[str, Any]:
    """拒絕根目錄、家目錄和逃逸符號連結。未知業務檔只報告、不刪除。"""

    resolved = base.resolve() if base.exists() else base
    if resolved == Path("/").resolve() or resolved == Path.home().resolve():
        return {"refused": "unsafe_root", "planned": [], "deleted": [], "unknown": []}
    planned: list[str] = []
    refused: list[dict[str, str]] = []
    unknown: list[str] = []
    if base.is_dir() and not base.is_symlink():
        for path in sorted(base.iterdir()):
            if path.name not in _HIDDEN:
                if path.is_file():
                    unknown.append(str(path))
                continue
            if path.is_symlink():
                refused.append({"path": str(path), "reason": "symlink"})
                continue
            if not path.is_dir():
                continue
            for child in sorted(path.rglob("*")):
                if child.is_symlink():
                    refused.append({"path": str(child), "reason": "symlink"})
                    continue
                if child.is_file():
                    planned.append(str(child))
    deleted: list[str] = []
    if apply:
        for item in planned:
            Path(item).unlink()
            deleted.append(item)
    return {"refused": refused, "planned": planned, "deleted": deleted, "unknown": unknown, "dryRun": not apply}
