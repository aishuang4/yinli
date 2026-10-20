"""把外部对存储层私有锁与连接的用法改写为公开事务入口（W6）。

用法（在 backend/ 目录）：uv run python ../openspec/changes/restructure-code-layering/tools/rewrite_storage_access.py [--dry-run]
改写的写法（`conn` 可以是任意变量名）：
  with database._lock, database._connect() as conn:                 -> with database.transaction() as conn:
  with database._lock, closing(database._connect()) as conn:        -> with database.transaction() as conn:
  with database._lock, closing(database._connect()) as conn, conn:  -> with database.transaction() as conn:
  with control_db._control_lock, control_db.control_connect() as conn:  -> with control_db.control_transaction() as conn:
  with _control_lock, control_connect() as conn:（auth_records.py / control_records.py 内的直接导入写法）
                                                                    -> with control_transaction() as conn:
  with database._connect() as conn（测试里不加锁的直接读写，后面可以跟其他上下文管理器）
                                                                    -> with database.transaction() as conn
  with _lock, _connect() as conn:（`from src.storage.database import _connect, _lock` 的直接导入写法）
                                                                    -> with transaction() as conn:
    同时把该文件单行 `from src.storage.database import ...` 中的 `_connect`、`_lock` 换成 `transaction`。
其余写法（例如同一 with 里还有别的上下文管理器、或把 _connect 当参数传递）只打印位置，按 design.md Decision 10 手工处理。
不改写 src/storage/database.py 与 src/storage/control_db.py 自身。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RULES = [
    (re.compile(r"with database\._lock, (?:closing\()?database\._connect\(\)\)? as (\w+)(?:, \1)?:"), r"with database.transaction() as \1:"),
    (re.compile(r"with control_db\._control_lock, control_db\.control_connect\(\) as (\w+):"), r"with control_db.control_transaction() as \1:"),
    (re.compile(r"with _control_lock, control_connect\(\) as (\w+):"), r"with control_transaction() as \1:"),
    (re.compile(r"with database\._connect\(\) as (\w+)"), r"with database.transaction() as \1"),
]
DIRECT_USE = re.compile(r"with _lock, _connect\(\) as (\w+):")
DIRECT_IMPORT = re.compile(r"^(\s*from src\.storage\.database import )([^\n(]+)$", re.MULTILINE)


def _rewrite_direct(text: str) -> tuple[str, int]:
    """改写直接导入 _connect/_lock 的文件；用法没有全部改掉时保留导入，交给人工处理。"""

    new, count = DIRECT_USE.subn(r"with transaction() as \1:", text)
    if not count or re.search(r"\b_connect\b|\b_lock\b", DIRECT_IMPORT.sub("", new)):
        return new, count

    def fix(match: re.Match[str]) -> str:
        names = [name.strip() for name in match.group(2).split(",")]
        if "_connect" not in names and "_lock" not in names:
            return match.group(0)
        kept = [name for name in names if name not in {"_connect", "_lock"}]
        if "transaction" not in kept:
            kept.append("transaction")
        return match.group(1) + ", ".join(sorted(kept, key=lambda name: (name.lstrip("_").lower(), name)))

    return DIRECT_IMPORT.sub(fix, new), count
LEFTOVER = re.compile(r"database\._lock|database\._connect|_control_lock|control_connect|import [^\n]*\b_connect\b")
SKIP = {Path("src/storage/database.py"), Path("src/storage/control_db.py")}

dry_run = "--dry-run" in sys.argv
total = 0
for path in sorted([*Path("src").rglob("*.py"), *Path("test").rglob("*.py")]):
    if path in SKIP:
        continue
    text = path.read_text(encoding="utf-8")
    new = text
    count = 0
    for pattern, replacement in RULES:
        new, n = pattern.subn(replacement, new)
        count += n
    new, n = _rewrite_direct(new)
    count += n
    if count:
        total += count
        print(f"{path}: 改写 {count} 处")
        if not dry_run:
            path.write_text(new, encoding="utf-8")
    reported: set[int] = set()
    for match in LEFTOVER.finditer(new):
        line = new.count("\n", 0, match.start()) + 1
        if line in reported:
            continue
        reported.add(line)
        print(f"需人工处理：{path}:{line} {new.splitlines()[line - 1].strip()}")
print(f"# 共改写 {total} 处")
