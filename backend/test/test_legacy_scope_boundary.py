"""舊歸屬 import 只能減少，不能新增。"""

from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SOURCE = BACKEND / "src"
FORBIDDEN_MODULES = {
    "src.runtime_context",
    "src.project_resources",
    "src.project_files",
}
DATABASE_NAMES = {
    "get_project",
    "get_workspace",
    "list_projects",
    "list_workspaces",
    "list_project_resource_bindings",
    "upsert_project_resource_binding",
    "DEFAULT_PROJECT_ID",
    "default_project_root",
}
EXEMPT_PREFIXES = (
    "src/session_migration/",
    "src/storage/",
)
EXEMPT_FILES = {
    "src/server/routes/workspaces.py",
    "src/runtime_context.py",
    "src/project_resources.py",
    "src/project_files.py",
}
CONCAT_RE = re.compile(r"""["'](workspace|project)["']\s*\+\s*["'](_id|Id)["']""")
CONCAT_PENDING_REMOVAL = frozenset({
    "test/conftest.py",
    "test/hosted_support.py",
})
LEGACY_IMPORT_ALLOWLIST: frozenset[str] = frozenset({
    "src/scheduler/capability_catalog.py",
    "src/security/authorization/migration.py",
    "src/skills/migration.py",
})


def _module_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _module_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _file_violates(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    database_aliases: set[str] = set()
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_MODULES or alias.name == "src.storage.database":
                    if alias.name in FORBIDDEN_MODULES:
                        return True
                    database_aliases.add(alias.asname or alias.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in FORBIDDEN_MODULES or module.startswith(tuple(item + "." for item in FORBIDDEN_MODULES)):
                return True
            if module == "src.storage" and any(alias.name == "database" for alias in node.names):
                for alias in node.names:
                    if alias.name == "database":
                        database_aliases.add(alias.asname or alias.name)
            if module == "src.storage.database":
                for alias in node.names:
                    if alias.name in DATABASE_NAMES:
                        return True
                    imported_names.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id in database_aliases and node.attr in DATABASE_NAMES:
                return True
        if isinstance(node, ast.Name) and node.id in imported_names and node.id in DATABASE_NAMES:
            return True
    return False


def scan_legacy_imports() -> set[str]:
    found: set[str] = set()
    for path in SOURCE.rglob("*.py"):
        relative = path.relative_to(BACKEND).as_posix()
        if relative.startswith(EXEMPT_PREFIXES) or relative in EXEMPT_FILES or "__pycache__" in relative:
            continue
        try:
            violates = _file_violates(path)
        except SyntaxError:
            continue
        if violates:
            found.add(relative)
    return found


def test_legacy_import_allowlist_matches_scan() -> None:
    assert scan_legacy_imports() == LEGACY_IMPORT_ALLOWLIST


def test_scope_field_concatenation_is_pending_only() -> None:
    offenders: list[str] = []
    roots = [SOURCE, BACKEND / "test"]
    for root in roots:
        for path in root.rglob("*.py"):
            relative = path.relative_to(BACKEND).as_posix()
            if relative.startswith("src/session_migration/") or relative in CONCAT_PENDING_REMOVAL:
                continue
            if "__pycache__" in relative:
                continue
            if CONCAT_RE.search(path.read_text(encoding="utf-8")):
                offenders.append(relative)
    assert offenders == []
