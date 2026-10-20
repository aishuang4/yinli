"""把一个模块中的一组顶层定义原样剪切到新模块（W1 用），并让原模块改为通过模块别名访问它们。

用法（在 backend/ 目录）：
  uv run python ../openspec/changes/restructure-code-layering/tools/extract_symbols.py \
      --from src.server.routes.agents --to src.agents.registry --alias agent_registry \
      --names AGENTS_NAMESPACE,_agents,... --by-name AgentCreate,AgentUpdate \
      --docstring "Agent 注册表：租户内 Agent 缓存与查询。" [--dry-run]

规则：
  1. --names 与 --by-name 中的顶层定义（函数/类/赋值，连同紧邻其上的注释行）按原顺序剪切到 --to 模块；
     顶层表达式语句若只调用被搬走的函数（如 `_install_agent_registry()`）也一起搬走。
  2. --to 模块的 import：从原模块的顶层 import 中，按被搬代码实际用到的名字逐个筛选生成，保持原写法。
  3. 原模块追加 `from <to 的包> import <to 的模块名> as <alias>` 与 `from <to> import <by-name 列表>`；
     剩余代码中对 --names 的裸引用改为 `<alias>.<name>`（保证 monkeypatch 新模块属性时原模块同步可见）；
     `global <name>` 语句若引用了被搬名字会报错退出，需人工处理。
  4. 原模块中不再使用的 import 交给 `ruff check --select F401 --fix <from 文件> <to 文件>` 清理；只对这两个文件运行，
     且运行前 fix-runtime-correctness-and-quality-gates 的 tools/reexport_audit.py 必须输出 0（否则会删掉别人依赖的再导出）。
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="source", required=True)
    parser.add_argument("--to", dest="target", required=True)
    parser.add_argument("--alias", required=True)
    parser.add_argument("--names", default="")
    parser.add_argument("--by-name", default="")
    parser.add_argument("--docstring", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    attr_names = [n for n in args.names.split(",") if n]
    by_names = [n for n in args.by_name.split(",") if n]
    moved = set(attr_names) | set(by_names)

    source_path = Path(*args.source.split(".")).with_suffix(".py")
    target_path = Path(*args.target.split(".")).with_suffix(".py")
    text = source_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)

    def defined(node: ast.stmt) -> set[str]:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return {node.name}
        if isinstance(node, ast.Assign):
            return {t.id for t in node.targets if isinstance(t, ast.Name)}
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            return {node.target.id}
        return set()

    cut: list[tuple[int, int]] = []
    imports: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
            continue
        names = defined(node)
        is_call = (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                   and isinstance(node.value.func, ast.Name) and node.value.func.id in moved)
        if (names and names <= moved) or is_call:
            start = min([node.lineno, *[d.lineno for d in getattr(node, "decorator_list", [])]]) - 1
            while start > 0 and lines[start - 1].lstrip().startswith("#"):
                start -= 1
            cut.append((start, node.end_lineno))
        elif names & moved:
            sys.exit(f"顶层语句同时定义了搬走与保留的名字，需人工拆分：第 {node.lineno} 行")
    missing = moved - {n for node in tree.body for n in defined(node)}
    if missing:
        sys.exit(f"原模块中找不到：{sorted(missing)}")

    moved_code = "".join("".join(lines[a:b]) + "\n\n" for a, b in cut).rstrip() + "\n"
    used = {n.id for n in ast.walk(ast.parse(moved_code)) if isinstance(n, ast.Name)} | {
        n.value.id for n in ast.walk(ast.parse(moved_code)) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
    used |= set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", " ".join(
        c.value for c in ast.walk(ast.parse(moved_code)) if isinstance(c, ast.Constant) and isinstance(c.value, str))))
    import_lines: list[str] = []
    for node in imports:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        keep = [a for a in node.names if (a.asname or a.name).split(".")[0] in used]
        if not keep:
            continue
        rendered = ", ".join(a.name + (f" as {a.asname}" if a.asname else "") for a in keep)
        if isinstance(node, ast.ImportFrom):
            import_lines.append(f"from {'.' * node.level}{node.module or ''} import {rendered}")
        else:
            import_lines.append(f"import {rendered}")
    header = f'"""{args.docstring}"""\n\n' if args.docstring else ""
    target_text = header + "from __future__ import annotations\n\n" + "\n".join(import_lines) + "\n\n\n" + moved_code

    keep_mask = [True] * len(lines)
    for a, b in cut:
        for i in range(a, b):
            keep_mask[i] = False
    remaining = "".join(line for line, keep in zip(lines, keep_mask) if keep)
    rem_tree = ast.parse(remaining)
    for node in ast.walk(rem_tree):
        if isinstance(node, ast.Global) and set(node.names) & moved:
            sys.exit(f"原模块剩余代码第 {node.lineno} 行 global 引用了搬走的名字，需人工处理")
    rem_lines = remaining.splitlines(keepends=True)
    edits: list[tuple[int, int, str]] = []
    for node in ast.walk(rem_tree):
        if isinstance(node, ast.Name) and node.id in attr_names and isinstance(node.ctx, ast.Load):
            edits.append((node.lineno, node.col_offset, node.id))
    for lineno, col, name in sorted(edits, reverse=True):
        line = rem_lines[lineno - 1]
        rem_lines[lineno - 1] = line[:col] + f"{args.alias}.{name}" + line[col + len(name):]
    remaining = "".join(rem_lines)
    package, leaf = args.target.rsplit(".", 1)
    new_imports = f"from {package} import {leaf} as {args.alias}\n"
    if by_names:
        new_imports += f"from {args.target} import {', '.join(by_names)}\n"
    last_import = max(n.end_lineno for n in ast.parse(remaining).body if isinstance(n, (ast.Import, ast.ImportFrom)))
    rem_lines = remaining.splitlines(keepends=True)
    remaining = "".join(rem_lines[:last_import]) + new_imports + "".join(rem_lines[last_import:])
    remaining = re.sub(r"\n{4,}", "\n\n\n", remaining)

    print(f"搬走 {len(cut)} 段共 {sum(b - a for a, b in cut)} 行；原模块改写 {len(edits)} 处裸引用")
    if not args.dry_run:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists():
            sys.exit(f"{target_path} 已存在，拒绝覆盖")
        target_path.write_text(target_text, encoding="utf-8")
        source_path.write_text(remaining, encoding="utf-8")


if __name__ == "__main__":
    main()
