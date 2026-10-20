"""列出 src/ 中从未被引用的顶层函数与类（不含带装饰器的、以双下划线开头的）。

用法（在 backend/ 目录）：uv run python ../openspec/changes/restructure-code-layering/tools/dead_code_audit.py
判断标准：名字在本文件只出现在定义处，且在 src/、test/、backend-server.spec 的其他文件中都没有以完整单词出现。
输出：路径:行号 名字 (行数)。字符串反射调用（getattr/importlib）无法静态识别，删除前需 grep 名字复核。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

files = sorted(Path("src").rglob("*.py"))
texts = {path: path.read_text(encoding="utf-8") for path in [*files, *Path("test").rglob("*.py"), *Path(".").glob("*.spec")]}
found = []
for path in files:
    tree = ast.parse(texts[path])
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if node.decorator_list or node.name.startswith("__"):
            continue
        word = re.compile(rf"\b{re.escape(node.name)}\b")
        if len(word.findall(texts[path])) > 1:
            continue
        if any(word.search(text) for other, text in texts.items() if other != path):
            continue
        found.append((path.as_posix(), node.lineno, node.name, node.end_lineno - node.lineno + 1))
for rel, line, name, size in found:
    print(f"{rel}:{line} {name} ({size})")
print(f"# 共 {len(found)} 个，{sum(item[3] for item in found)} 行")
