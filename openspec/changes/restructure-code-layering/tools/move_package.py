"""把一个包下的模块整体搬到新位置，并改写 src/ 与 test/ 中所有引用。

用法（在 backend/ 目录）：
  uv run python ../openspec/changes/restructure-code-layering/tools/move_package.py \
      --old src.server.routes.chat --new src.runtime.chat \
      --keep __init__.py --keep router.py --keep handlers [--dry-run]

规则：
  1. OLD 目录下除 --keep 列出的文件/子目录外，全部 git mv 到 NEW 目录，保持相对结构；NEW 下每级目录确保有 __init__.py。
  2. 在 src/、test/ 及 backend-server.spec 中，把 "OLD.<被搬走的子模块>" 改为 "NEW.<子模块>"。覆盖 from/import 语句、
     monkeypatch/mock.patch 字符串、importlib 字符串。指向 --keep 模块的引用不改。
  3. `from OLD import a, b` 若 a、b 都是被搬走的子模块，改为 `from NEW import a, b`；若混有保留的名字，打印到"需人工处理"清单。
  4. 包内相对导入保持不变（结构不变即仍然有效）。
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path


def module_path(module: str) -> Path:
    return Path(*module.split("."))


def moved_modules(old_dir: Path, keep: set[str]) -> list[Path]:
    files = []
    for path in sorted(old_dir.rglob("*.py")):
        rel = path.relative_to(old_dir)
        if rel.parts[0] in keep or rel.as_posix() in keep or "__pycache__" in rel.parts:
            continue
        files.append(rel)
    return files


def dotted(rel: Path) -> str:
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", required=True)
    parser.add_argument("--new", required=True)
    parser.add_argument("--keep", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    old_dir, new_dir = module_path(args.old), module_path(args.new)
    keep = set(args.keep)
    rels = moved_modules(old_dir, keep)
    names = sorted({dotted(rel) for rel in rels if dotted(rel)}, key=len, reverse=True)
    top_level = {name.split(".")[0] for name in names}
    print(f"搬移 {len(rels)} 个文件，{len(names)} 个模块名")

    if not args.dry_run:
        in_git = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], capture_output=True).returncode == 0
        for rel in rels:
            target = new_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if in_git:
                subprocess.run(["git", "mv", str(old_dir / rel), str(target)], check=True)
            else:
                shutil.move(str(old_dir / rel), str(target))
        for directory in {new_dir, *[(new_dir / rel).parent for rel in rels]}:
            current = directory
            while current != new_dir.parent and current.parts:
                init = current / "__init__.py"
                if not init.exists():
                    init.write_text("", encoding="utf-8")
                current = current.parent
        for directory in sorted({(old_dir / rel).parent for rel in rels}, key=lambda p: len(p.parts), reverse=True):
            if directory.exists() and not any(p for p in directory.rglob("*.py")):
                shutil.rmtree(directory)

    old_re = re.escape(args.old)
    module_alt = "|".join(re.escape(name) for name in names)
    dotted_ref = re.compile(rf"\b{old_re}\.({module_alt})(?=\b|[.\"'\s])")
    from_pkg = re.compile(rf"^(\s*)from {old_re} import (\([^)]*\)|[^\n]+)", re.M)
    manual: list[str] = []
    changed_files = 0
    targets = [*Path("src").rglob("*.py"), *Path("test").rglob("*.py"), *Path(".").glob("*.spec")]
    for path in targets:
        text = path.read_text(encoding="utf-8")
        new = dotted_ref.sub(lambda m: f"{args.new}.{m.group(1)}", text)

        def rewrite_from(match: re.Match[str]) -> str:
            imported = [part.strip().split(" as ")[0] for part in match.group(2).strip("()").replace("\n", " ").split(",") if part.strip()]
            if imported and all(name in top_level for name in imported):
                return f"{match.group(1)}from {args.new} import {match.group(2)}"
            if any(name in top_level for name in imported):
                manual.append(f"{path}: from {args.old} import {', '.join(imported)}")
            return match.group(0)

        new = from_pkg.sub(rewrite_from, new)
        if new != text:
            changed_files += 1
            if not args.dry_run:
                path.write_text(new, encoding="utf-8")
    print(f"改写引用的文件 {changed_files} 个")
    for item in manual:
        print(f"需人工处理：{item}")


if __name__ == "__main__":
    main()
