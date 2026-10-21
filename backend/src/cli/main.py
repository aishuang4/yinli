"""yinli 命令行。数据库维护，以及把旧 profile 复制进 agents 目录。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from src.storage.maintenance import backup, health, recover
from src.tenancy.context import TenantContext, use_tenant


def _legacy_profile_files(source: Path) -> list[Path]:
    """目录本身的 Markdown，以及任意 profiles 子目录里的 Markdown。"""

    found: list[Path] = []
    seen: set[Path] = set()

    def _add(path: Path) -> None:
        if not path.is_file() or path.suffix.lower() != ".md":
            return
        name = path.name
        if name.startswith(".") or name.endswith(".tmp") or name.endswith(".bak") or ".conflict-" in name:
            return
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved in seen:
            return
        seen.add(resolved)
        found.append(path)

    for path in sorted(source.glob("*.md")):
        _add(path)
    for path in sorted(source.rglob("*.md")):
        try:
            parts = path.relative_to(source).parts
        except ValueError:
            continue
        if "profiles" in parts:
            _add(path)
    return found


def _import_legacy_agents(source: Path) -> int:
    """只复制 profile 文件，不解析、不改内容。目标已存在则跳过并打印。"""

    from src.config.runtime_paths import agents_root, ensure_tenant_directories

    root = source.expanduser().resolve()
    if not root.is_dir():
        print(f"目录不存在: {root}", file=sys.stderr)
        return 2
    ensure_tenant_directories()
    copied = 0
    for path in _legacy_profile_files(root):
        try:
            parts = path.resolve().relative_to(root).parts
        except (OSError, ValueError):
            parts = ()
        if "_system" in parts:
            target_dir = agents_root() / "_system" / "profiles"
        else:
            target_dir = agents_root() / "profiles"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        if target.exists():
            print(f"跳过，目标已存在: {target}")
            continue
        shutil.copyfile(path, target)
        copied += 1
        print(f"已复制: {path} -> {target}")
    print(f"完成，复制 {copied} 个 profile")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="yinli")
    sub = parser.add_subparsers(dest="group", required=True)
    db = sub.add_parser("db")
    action = db.add_subparsers(dest="action", required=True)

    backup_cmd = action.add_parser("backup")
    backup_cmd.add_argument("--dest", type=Path)

    recover_cmd = action.add_parser("recover")
    recover_cmd.add_argument("source", type=Path)
    recover_cmd.add_argument("--dest", type=Path)

    action.add_parser("health")

    agents = sub.add_parser("agents")
    agents_action = agents.add_subparsers(dest="action", required=True)
    legacy = agents_action.add_parser("import-legacy")
    legacy.add_argument("directory", type=Path)

    args = parser.parse_args(argv)
    with use_tenant(TenantContext.local()):
        if args.group == "db" and args.action == "backup":
            print(backup(args.dest))
            return 0
        if args.group == "db" and args.action == "recover":
            print(recover(args.source, args.dest))
            return 0
        if args.group == "db" and args.action == "health":
            print(json.dumps(health(), ensure_ascii=False))
            return 0
        if args.group == "agents" and args.action == "import-legacy":
            return _import_legacy_agents(args.directory)
    parser.error("未知命令")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
