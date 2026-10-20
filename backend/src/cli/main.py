"""yinli 命令行。当前只提供数据库维护。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.storage.maintenance import backup, health, recover
from src.tenancy.context import TenantContext, use_tenant


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
    parser.error("未知命令")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
