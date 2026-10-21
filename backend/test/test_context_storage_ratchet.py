"""禁止把已删除的存储兼容路径写回源码。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (ROOT / "backend" / "src", ROOT / "frontend" / "src")
BANNED = re.compile(
    r"\b(_ensure_column|assign_owners|legacy_migration|migrate_agents|"
    r"schema_migrations|import_legacy_chat_store|migrate_json_list_file|"
    r"migrate_json_object_file|migrate_list_file|migrate_object_file|"
    r"workspace_id|workspaceId|migrate_group_studio_v2|migrate_legacy_v2|"
    r"_migrate_legacy_skill_packages|_migrate_legacy_subagents)\b"
    r"|[\"']external_skill_overrides[\"']"
)


def test_deleted_storage_compatibility_does_not_return() -> None:
    offenders: list[str] = []
    for root in SCAN_ROOTS:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"} or "__pycache__" in path.parts:
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if BANNED.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert offenders == []
