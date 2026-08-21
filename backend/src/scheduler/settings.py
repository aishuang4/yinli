"""定时任务运行时开关。默认关闭，直到 ticker 与管理页就绪。"""

from __future__ import annotations

from src.config.runtime_settings import SYSTEM_SETTINGS_NAMESPACE
from src.storage.database import get_json_record, upsert_json_record

SCHEDULED_JOBS_SETTINGS_KEY = "scheduled_jobs"


def is_scheduled_jobs_enabled() -> bool:
    """读取 scheduled_jobs.enabled；缺省或未写入时为 false。"""

    raw = get_json_record(SYSTEM_SETTINGS_NAMESPACE, SCHEDULED_JOBS_SETTINGS_KEY) or {}
    return bool(raw.get("enabled", False))


def set_scheduled_jobs_enabled(enabled: bool) -> None:
    """测试与后续设置页写入开关。"""

    upsert_json_record(SYSTEM_SETTINGS_NAMESPACE, SCHEDULED_JOBS_SETTINGS_KEY, {"enabled": bool(enabled)})
