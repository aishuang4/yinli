"""把一组顶层名字从一个模块改为从另一个模块导入，并改写 src/ 与 test/ 的引用。不搬代码本身。

用法（在 backend/ 目录，先用 extract_symbols.py 把代码搬到 --to 模块）：
  uv run python ../openspec/changes/restructure-code-layering/tools/move_symbols.py \
      --from src.server.routes.agents --to src.agents.registry --names get_registered_agent,list_registered_agents [--dry-run]

改写规则：
  1. `from FROM import a, b, c`（含多行括号形式）中属于 --names 的名字拆到 `from TO import ...`，其余保留。
  2. 字符串和代码里的 "FROM.name"（monkeypatch 路径等）改为 "TO.name"。
  3. 通过模块别名访问（如 `from src.server.routes import agents as agents_routes` 后的 `agents_routes.name`，
     以及 `setattr(agents_routes, "name", ...)`）：给了 --new-alias 时自动改为 `<new-alias>.name`，并在该文件
     别名 import 的下一行插入 `from <TO 的包> import <TO 的模块名> as <new-alias>`；没给时只打印位置。
  4. --from 模块自身不改写。
  5. 测试里 `setattr(<旧别名>, "x", ...)` 的 x 若也被 --to 模块导入使用（例如 invalidate_agent），打印"需人工处理"，
     由实施者判断是否改为 patch --to 模块。
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="source", required=True)
    parser.add_argument("--to", dest="target", required=True)
    parser.add_argument("--names", required=True)
    parser.add_argument("--new-alias", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    names = [name.strip() for name in args.names.split(",") if name.strip()]
    source_file = Path(*args.source.split(".")).with_suffix(".py")
    parent, leaf = args.source.rsplit(".", 1)
    from_stmt = re.compile(rf"^(?P<indent>[ \t]*)from {re.escape(args.source)} import (?P<body>\([^)]*\)|[^\n]+)", re.M)
    dotted = re.compile(rf"\b{re.escape(args.source)}\.({'|'.join(map(re.escape, names))})\b")
    alias_import = re.compile(rf"from {re.escape(parent)} import [^\n]*\b{re.escape(leaf)}\b(?: as (\w+))?|import {re.escape(args.source)} as (\w+)")
    manual: list[str] = []
    changed = 0
    target_file = Path(*args.target.split(".")).with_suffix(".py")
    target_imports: set[str] = set()
    if target_file.exists():
        for node in ast.parse(target_file.read_text(encoding="utf-8")).body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                target_imports |= {(a.asname or a.name).split(".")[0] for a in node.names}
    for path in [*Path("src").rglob("*.py"), *Path("test").rglob("*.py")]:
        if path == source_file:
            continue
        text = path.read_text(encoding="utf-8")
        needs_import: set[str] = set()

        def split(match: re.Match[str]) -> str:
            body = match.group("body")
            comment = ""
            if not body.startswith("(") and "#" in body:
                body, comment = body.split("#", 1)
                comment = "  #" + comment
            items = [part.strip() for part in body.strip().strip("()").replace("\n", " ").split(",") if part.strip()]
            moved = [item for item in items if item.split(" as ")[0].strip() in names]
            if not moved:
                return match.group(0)
            kept = [item for item in items if item not in moved]
            indent = match.group("indent")
            lines = []
            if kept:
                lines.append(f"{indent}from {args.source} import {', '.join(kept)}{comment}")
            lines.append(f"{indent}from {args.target} import {', '.join(moved)}{comment if not kept else ''}")
            return "\n".join(lines)

        new = from_stmt.sub(split, text)
        new = dotted.sub(lambda m: f"{args.target}.{m.group(1)}", new)
        target_parent, target_leaf = args.target.rsplit(".", 1)
        for match in list(alias_import.finditer(new)):
            alias = match.group(1) or match.group(2) or leaf
            attr_use = re.compile(rf"(?<![\w.]){re.escape(alias)}\.({'|'.join(map(re.escape, names))})\b")
            setattr_use = re.compile(rf"setattr\(\s*{re.escape(alias)}\s*,\s*([\"'])({'|'.join(map(re.escape, names))})\1")
            if args.new_alias:
                replaced = attr_use.sub(lambda m: f"{args.new_alias}.{m.group(1)}", new)
                replaced = setattr_use.sub(lambda m: f"setattr({args.new_alias}, {m.group(1)}{m.group(2)}{m.group(1)}", replaced)
                if replaced != new:
                    new = replaced
                    needs_import.add(alias)
            else:
                for use in [*attr_use.finditer(new), *setattr_use.finditer(new)]:
                    line = new.count("\n", 0, use.start()) + 1
                    manual.append(f"{path}:{line} {use.group(0)}")
        for match in alias_import.finditer(new):
            alias = match.group(1) or match.group(2) or leaf
            for patched in re.finditer(rf"setattr\(\s*{re.escape(alias)}\s*,\s*[\"'](\w+)[\"']", new):
                if patched.group(1) in target_imports and patched.group(1) not in names:
                    line = new.count("\n", 0, patched.start()) + 1
                    manual.append(f"{path}:{line} 补丁目标 {alias}.{patched.group(1)} 也被 {args.target} 使用，确认是否改为 patch {args.target}")
        if needs_import:
            import_line = f"from {target_parent} import {target_leaf} as {args.new_alias}"
            out_lines = []
            for line in new.splitlines(keepends=True):
                out_lines.append(line)
                stripped = line.strip()
                match = alias_import.fullmatch(stripped.split("  #")[0].strip())
                if match and (match.group(1) or match.group(2) or leaf) in needs_import:
                    indent = line[: len(line) - len(line.lstrip())]
                    out_lines.append(f"{indent}{import_line}\n")
            new = "".join(out_lines)
            for alias in needs_import:
                body_uses = re.findall(rf"(?<![\w.]){re.escape(alias)}\b", new)
                import_count = len(re.findall(rf"^[ \t]*(?:from {re.escape(parent)} import {re.escape(leaf)} as {re.escape(alias)}|from {re.escape(parent)} import {re.escape(leaf)}|import {re.escape(args.source)} as {re.escape(alias)})[ \t]*(?:#.*)?$", new, re.M))
                if import_count and len(body_uses) == import_count:
                    new = re.sub(rf"^[ \t]*(?:from {re.escape(parent)} import {re.escape(leaf)} as {re.escape(alias)}|from {re.escape(parent)} import {re.escape(leaf)}|import {re.escape(args.source)} as {re.escape(alias)})[ \t]*(?:#.*)?\n", "", new, flags=re.M)
        if new != text:
            changed += 1
            if not args.dry_run:
                path.write_text(new, encoding="utf-8")
    print(f"改写的文件 {changed} 个")
    for item in sorted(set(manual)):
        print(f"需人工处理：{item}")


if __name__ == "__main__":
    main()
