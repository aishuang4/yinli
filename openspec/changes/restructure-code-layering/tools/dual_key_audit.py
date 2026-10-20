"""统计 `.get("snake_case") or .get("camelCase")`（或反向）这类双键兼容读取，按文件输出数量。

用法（在 backend/ 目录）：uv run python ../openspec/changes/restructure-code-layering/tools/dual_key_audit.py [--json]
--json 时输出 {"相对路径": 数量}，可直接作为 test_dual_key_lookup_ratchet.py 的 BASELINE。
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def key_of(node: ast.AST) -> str | None:
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
            and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
        return node.args[0].value
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
        return node.slice.value
    return None


def count(path: Path) -> int:
    total = 0
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            keys = [key_of(value) for value in node.values]
            names = {k for k in keys if k}
            for name in names:
                if "_" in name and camel(name) in names:
                    total += 1
                    break
    return total


result = {}
for path in sorted(Path("src").rglob("*.py")):
    n = count(path)
    if n:
        result[path.as_posix()] = n
if "--json" in sys.argv:
    print(json.dumps(result, ensure_ascii=False, indent=4, sort_keys=True))
else:
    for name, n in sorted(result.items(), key=lambda item: -item[1]):
        print(f"{n:4d} {name}")
    print(f"# 共 {sum(result.values())} 处，{len(result)} 个文件")
