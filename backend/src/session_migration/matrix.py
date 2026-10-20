"""Workspace / Project 依賴矩陣與新增引用基線。"""

from __future__ import annotations

import json
import re
from pathlib import Path

IDENTIFIER_RE = re.compile(r"workspaceId|projectId|workspace_id|project_id")
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs"}
SKIP_PARTS = {"node_modules", "dist", "__pycache__", ".venv", ".git"}
# 本變更新增的歸屬層，不計入提案時的現有模組計數。
NEW_PACKAGE_PREFIXES = (
    "backend/src/session_migration/",
    "backend/src/sessions/",
)
MATRIX_PATH = Path(__file__).with_name("usage_matrix.json")
ALLOWLIST_PATH = Path(__file__).with_name("reference_allowlist.json")
REPO_ROOT = Path(__file__).resolve().parents[3]


def load_matrix(path: Path | None = None) -> list[dict[str, str]]:
    """讀取已提交的模組歸屬矩陣。"""

    payload = json.loads((path or MATRIX_PATH).read_text(encoding="utf-8"))
    modules = payload.get("modules")
    if not isinstance(modules, list):
        raise ValueError("usage matrix 缺少 modules")
    return [dict(item) for item in modules]


def load_allowlist(path: Path | None = None) -> set[str]:
    """歷史引用允許清單。未登記路徑視為新增依賴。"""

    payload = json.loads((path or ALLOWLIST_PATH).read_text(encoding="utf-8"))
    paths = payload.get("paths")
    if not isinstance(paths, list):
        raise ValueError("reference allowlist 缺少 paths")
    return {str(item) for item in paths}


def scan_references(root: Path, *, relative_roots: tuple[str, ...] = ("backend", "frontend/src")) -> set[str]:
    """掃描相對路徑中出現 Workspace / Project 識別字的源碼檔。"""

    found: set[str] = set()
    for relative_root in relative_roots:
        base = root / relative_root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
                continue
            if any(part in SKIP_PARTS for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if IDENTIFIER_RE.search(text):
                found.add(path.relative_to(root).as_posix())
    return found


def product_modules(paths: set[str]) -> set[str]:
    """提案基線中的產品模組，不含本變更新增套件。"""

    return {
        path
        for path in paths
        if (path.startswith("backend/src/") or path.startswith("frontend/src/"))
        and not path.startswith(NEW_PACKAGE_PREFIXES)
    }


def verify_matrix(root: Path | None = None) -> list[str]:
    """矩陣必須覆蓋 backend/src 與 frontend/src 的全部現有命中。基線在停用預設 Project 綁定後是後端 111、前端 24。"""

    repo = root or REPO_ROOT
    modules = load_matrix()
    recorded = {item["path"]: item for item in modules}
    hits = product_modules(scan_references(repo))
    errors: list[str] = []
    missing = sorted(hits - set(recorded))
    extra = sorted(set(recorded) - hits)
    if missing:
        errors.append("矩陣未覆蓋: " + ", ".join(missing))
    if extra:
        errors.append("矩陣含已消失模組: " + ", ".join(extra))
    backend = [item for item in modules if item.get("area") == "backend"]
    frontend = [item for item in modules if item.get("area") == "frontend"]
    live_backend = [path for path in hits if path.startswith("backend/")]
    live_frontend = [path for path in hits if path.startswith("frontend/")]
    if len(live_backend) > len(backend) or len(live_frontend) > len(frontend):
        errors.append(
            f"命中數不得超過矩陣記錄，實際 {len(live_backend)} / {len(live_frontend)}，"
            f"記錄 {len(backend)} / {len(frontend)}"
        )
    allowed_targets = {"global-catalog", "session-owner", "external-directory", "deletable"}
    for item in modules:
        if item.get("target") not in allowed_targets:
            errors.append(f"非法歸屬 {item.get('path')}: {item.get('target')}")
    return errors


def unregistered_references(paths: set[str], allowlist: set[str] | None = None) -> list[str]:
    """回傳不在歷史清單中的引用路徑。"""

    allowed = load_allowlist() if allowlist is None else allowlist
    return sorted(paths - allowed)


def verify_allowlist(root: Path | None = None) -> list[str]:
    """倉庫現況必須與歷史清單一致。多出來的檔案就是未登記引用。"""

    repo = root or REPO_ROOT
    found = scan_references(repo)
    allowed = load_allowlist()
    extra = sorted(found - allowed)
    stale = sorted(allowed - found)
    errors: list[str] = []
    if extra:
        errors.append("未登記的 Workspace/Project 引用: " + ", ".join(extra))
    if stale:
        errors.append("清單中已不存在的引用: " + ", ".join(stale))
    return errors
