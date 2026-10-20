"""阶段 B 审计：找出没有锁、但在函数里被修改的模块级可变容器。

用法（在 backend/ 目录）：uv run python ../openspec/changes/fix-runtime-correctness-and-quality-gates/tools/mutable_state_audit.py
输出：文件、被修改的容器名。逐个判断它是否会被线程池里的路由调用链修改。
"""

from __future__ import annotations

import ast
from pathlib import Path

MUTATORS = {"append", "extend", "update", "pop", "clear", "setdefault", "add", "discard", "remove", "insert", "popitem"}
FACTORIES = {"dict", "list", "set", "OrderedDict", "defaultdict", "deque", "WeakValueDictionary"}

for path in sorted(Path("src").rglob("*.py")):
    text = path.read_text(encoding="utf-8")
    if "Lock(" in text:
        continue
    tree = ast.parse(text)
    containers: set[str] = set()
    for node in tree.body:
        target = value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            target, value = node.target.id, node.value
        if target and (
            isinstance(value, (ast.Dict, ast.List, ast.Set))
            or (isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in FACTORIES)
        ):
            containers.add(target)
    mutated: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in MUTATORS
                    and isinstance(node.func.value, ast.Name) and node.func.value.id in containers):
                mutated.add(node.func.value.id)
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.Delete)):
                targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id in containers:
                        mutated.add(target.value.id)
            if isinstance(node, ast.Global):
                mutated.update(name for name in node.names if name in containers)
    if mutated:
        print(f"{path.as_posix()} {', '.join(sorted(mutated))}")
