"""Workspace / Project catalog 冪等遷入全局目錄。相同正文合併，不同正文阻斷。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.session_migration.mapping import map_same_id_files, materialize_merged_files


def _definition_files(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if not root.is_dir() or root.is_symlink():
            continue
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            if path.suffix not in {".md", ".yaml", ".yml"}:
                continue
            found.append(path)
    return found


def migrate_catalogs(roots: list[Path], destination: Path, report_dir: Path) -> dict[str, Any]:
    """重跑不覆蓋已合併檔，也不把衝突正文寫進全局目錄。"""

    report_dir.mkdir(parents=True, exist_ok=True)
    mapping = map_same_id_files(_definition_files(roots))
    written = materialize_merged_files(mapping, destination) if not mapping["conflicts"] else []
    if mapping["conflicts"]:
        written = []
    payload = {
        "merged": mapping["merged"],
        "conflicts": mapping["conflicts"],
        "written": written,
        "overwritten": mapping.get("overwritten", []),
    }
    (report_dir / "mapping.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (report_dir / "conflicts.json").write_text(
        json.dumps(mapping["conflicts"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload
