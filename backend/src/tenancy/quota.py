"""托管租户配额。desktop 不检查。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from fastapi import HTTPException

from src.config.runtime_paths import tenant_root
from src.config.settings import settings
from src.tenancy.context import current_tenant, is_hosted_mode

_disk_cache: dict[str, tuple[float, int]] = {}


class QuotaExceeded(Exception):
    def __init__(self, quota: str, limit: int, *, status: int = 429) -> None:
        super().__init__(quota)
        self.quota = quota
        self.limit = limit
        self.status = status

    def as_http(self) -> HTTPException:
        return HTTPException(
            status_code=self.status,
            detail={"code": "quota_exceeded", "quota": self.quota, "limit": self.limit},
        )


def _hosted() -> bool:
    if not is_hosted_mode():
        return False
    try:
        return current_tenant().is_hosted
    except Exception:
        return False


def assert_active_runs(active: int) -> None:
    if not _hosted():
        return
    limit = int(settings.tenant_max_active_runs)
    if active >= limit:
        raise QuotaExceeded("max_active_runs", limit, status=429)


def assert_job_count(count: int) -> None:
    if not _hosted():
        return
    limit = int(settings.tenant_max_jobs)
    if count >= limit:
        raise QuotaExceeded("max_jobs", limit, status=429)


def assert_upload_size(size: int) -> None:
    if not _hosted():
        return
    limit = int(settings.tenant_upload_mb) * 1024 * 1024
    if size > limit:
        raise QuotaExceeded("upload_mb", int(settings.tenant_upload_mb), status=413)


def assert_disk_budget(extra_bytes: int = 0) -> None:
    if not _hosted():
        return
    limit = int(settings.tenant_disk_mb) * 1024 * 1024
    if disk_usage_bytes() + extra_bytes > limit:
        raise QuotaExceeded("disk_mb", int(settings.tenant_disk_mb), status=413)


def firings_allowed(count_today: int) -> bool:
    if not _hosted():
        return True
    return count_today < int(settings.tenant_max_firings_per_day)


def disk_usage_bytes() -> int:
    tenant_id = current_tenant().tenant_id
    cached = _disk_cache.get(tenant_id)
    now = time.monotonic()
    if cached and now - cached[0] < 60:
        return cached[1]
    total = _directory_size(tenant_root())
    _disk_cache[tenant_id] = (now, total)
    return total


def _directory_size(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return total
