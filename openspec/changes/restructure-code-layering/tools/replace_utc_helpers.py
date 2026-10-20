"""把各模块本地的“当前 UTC 时间 ISO 字符串”函数替换为 src.common.time.utc_now_iso（W6）。

用法（在 backend/ 目录）：uv run python ../openspec/changes/restructure-code-layering/tools/replace_utc_helpers.py [--dry-run]
规则：
  - 只处理顶层函数，且去掉 docstring 后函数体恰好是一句
    `return datetime.now(timezone.utc).isoformat()` 或 `return datetime.now(UTC).isoformat()`；
    返回 datetime 对象的函数（例如 auth_records._utc_now、projection_writer._utc_now）不会命中。
  - 删除该函数定义，在最后一条顶层 import 之后插入 `from src.common.time import utc_now_iso as <原名>`。
    保留原名，调用点与 `monkeypatch.setattr(module, "<原名>", ...)` 都不用改。
  - 不删除因此变成未使用的 datetime/timezone/UTC 导入；脚本最后打印需要人工检查的文件，
    人工只删除这三个名字（不要对这些文件整体运行 ruff --fix，以免删掉再导出）。
若 src/common/time.py 不存在会先创建。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BODIES = {
    "return datetime.now(timezone.utc).isoformat()",
    "return datetime.now(UTC).isoformat()",
}
TIME_MODULE = Path("src/common/time.py")
TIME_SOURCE = '''"""时间工具：统一生成 UTC ISO 8601 字符串。"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now_iso() -> str:
    """当前 UTC 时间的 ISO 8601 字符串，带 +00:00 时区后缀。"""

    return datetime.now(timezone.utc).isoformat()
'''

dry_run = "--dry-run" in sys.argv
if not TIME_MODULE.exists():
    print(f"创建 {TIME_MODULE}")
    if not dry_run:
        TIME_MODULE.parent.mkdir(parents=True, exist_ok=True)
        init = TIME_MODULE.parent / "__init__.py"
        if not init.exists():
            init.write_text('"""无业务依赖的公共工具：只可导入标准库与 src.config。"""\n', encoding="utf-8")
        TIME_MODULE.write_text(TIME_SOURCE, encoding="utf-8")

changed: list[Path] = []
for path in sorted(Path("src").rglob("*.py")):
    if path == TIME_MODULE or "__pycache__" in path.parts:
        continue
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    targets = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and not node.decorator_list:
            body = [s for s in node.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
            if len(body) == 1 and ast.unparse(body[0]) in BODIES:
                targets.append(node)
    if not targets:
        continue
    lines = text.splitlines(keepends=True)
    # 插在文件开头连续 import 区的末尾，不放到模块中部的延迟导入之后，避免新增 E402。
    insert_after = 0
    for index, stmt in enumerate(tree.body):
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            insert_after = stmt.end_lineno
        elif index == 0 and isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            insert_after = stmt.end_lineno
        elif isinstance(stmt, ast.If) and ast.unparse(stmt.test) in {"TYPE_CHECKING", "typing.TYPE_CHECKING"}:
            continue
        else:
            break
    for node in sorted(targets, key=lambda n: n.lineno, reverse=True):
        start, end = node.lineno - 1, node.end_lineno
        while end < len(lines) and not lines[end].strip():
            end += 1
        del lines[start:end]
        if start < insert_after:
            insert_after -= end - start
        print(f"{path}:{node.lineno} 删除 {node.name}()")
    new_imports = "".join(f"from src.common.time import utc_now_iso as {n.name}\n" for n in targets)
    lines.insert(insert_after, new_imports)
    changed.append(path)
    if not dry_run:
        path.write_text("".join(lines), encoding="utf-8")
print(f"# 共处理 {len(changed)} 个文件；请检查这些文件中的 datetime/timezone/UTC 导入是否已不再使用：")
for path in changed:
    print(f"  {path}")
