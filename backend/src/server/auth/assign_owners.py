"""一次性把尚未归属的业务数据划给某个用户。

用法：`uv run python -m src.server.auth.assign_owners <user_id>`
"""

from __future__ import annotations

import sys

from src.server.auth.ownership import assign_unowned


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or not args[0].strip():
        print("用法: python -m src.server.auth.assign_owners <user_id>", file=sys.stderr)
        return 2
    try:
        updated = assign_unowned(args[0].strip())
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"已归属 {updated} 行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
