"""定时任务运行时开关。默认关闭，直到 ticker 与管理页就绪。"""

from __future__ import annotations

from typing import Any

from src.config.runtime_settings import SYSTEM_SETTINGS_NAMESPACE
from src.storage.database import get_json_record, upsert_json_record

SCHEDULED_JOBS_SETTINGS_KEY = "scheduled_jobs"


def _read_scheduled_jobs_settings() -> dict[str, Any]:
    raw = get_json_record(SYSTEM_SETTINGS_NAMESPACE, SCHEDULED_JOBS_SETTINGS_KEY) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def is_scheduled_jobs_enabled() -> bool:
    """读取 scheduled_jobs.enabled；缺省或未写入时为 false。"""

    return bool(_read_scheduled_jobs_settings().get("enabled", False))


def is_capability_gate_enabled() -> bool:
    """能力门禁子开关；JSON 缺省 false，且主开关关闭时视为 false。"""

    if not is_scheduled_jobs_enabled():
        return False
    return bool(_read_scheduled_jobs_settings().get("capability_gate", False))


def set_scheduled_jobs_settings(
    *,
    enabled: bool | None = None,
    capability_gate: bool | None = None,
) -> dict[str, Any]:
    """读-改-写合并 system_settings/scheduled_jobs，避免抹掉另一键。"""

    current = _read_scheduled_jobs_settings()
    if enabled is not None:
        current["enabled"] = bool(enabled)
    if capability_gate is not None:
        current["capability_gate"] = bool(capability_gate)
    upsert_json_record(SYSTEM_SETTINGS_NAMESPACE, SCHEDULED_JOBS_SETTINGS_KEY, current)
    return current


def set_scheduled_jobs_enabled(enabled: bool) -> None:
    """测试与后续设置页写入主开关；必须保留 capability_gate。"""

    set_scheduled_jobs_settings(enabled=enabled)
