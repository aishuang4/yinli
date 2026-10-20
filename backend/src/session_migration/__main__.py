"""全局 Session 遷移命令列。run 與 rollback 只能在後端停止時使用。"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from src.session_migration.rollback import rollback_to_legacy
from src.session_migration.runner import run_tenant_migration
from src.session_migration.state import cutover_state, migration_dir, set_migration_flag
from src.tenancy.context import TenantContext, is_hosted_mode, use_tenant

_SUCCESS = {"already_cutover", "fresh_cutover", "cutover", "dry_run"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.session_migration",
        description="全局 Session 遷移。run 和 rollback 只能在後端停止時使用。",
    )
    parser.add_argument("command", choices=("status", "dry-run", "run", "rollback"))
    parser.add_argument("--tenant", default="")
    args = parser.parse_args(argv)
    try:
        if is_hosted_mode() and not args.tenant:
            parser.error("hosted 必須提供 --tenant")
        context = TenantContext.hosted(args.tenant) if args.tenant else TenantContext.local()
        with use_tenant(context):
            payload = _execute(args.command)
        json.dump(payload, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        status = str(payload.get("status") or "")
        if args.command == "status" or status in _SUCCESS or payload.get("readPath") == "legacy":
            return 0
        if status in {"blocked", "not_ready"}:
            return 2
        return 0
    except SystemExit:
        raise
    except Exception:
        return 1


def _execute(command: str) -> dict[str, Any]:
    from src.config.runtime_paths import sessions_root

    if command == "status":
        return cutover_state()
    if command == "dry-run":
        return run_tenant_migration(dry_run=True)
    if command == "run":
        return run_tenant_migration(dry_run=False)
    return rollback_to_legacy(
        migration_dir() / "backup",
        migration_dir() / "rollback-export",
        sessions_root(),
        set_legacy_read=lambda: set_migration_flag(False),
    )
