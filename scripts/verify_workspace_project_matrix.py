"""驗證 Workspace / Project 依賴矩陣與歷史引用清單。"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from src.session_migration.matrix import verify_allowlist, verify_matrix


def main() -> int:
    errors = verify_matrix(REPO_ROOT) + verify_allowlist(REPO_ROOT)
    if errors:
        print("\n".join(errors))
        return 1
    print("workspace/project matrix covers 112 backend and 47 frontend modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
