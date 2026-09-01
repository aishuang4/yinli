"""群聊工作室运行时开关。json_store `system_settings/group_studio`，默认开启。"""

from __future__ import annotations

from typing import Any

from src.config.runtime_settings import SYSTEM_SETTINGS_NAMESPACE
from src.storage.database import get_json_record, upsert_json_record

GROUP_STUDIO_SETTINGS_KEY = "group_studio"
DEFAULT_MAX_MEMBERS = 6
DEFAULT_MAX_PARALLEL_WORKERS = 1
DEFAULT_MAX_TURNS = 40


def _read_group_studio_settings() -> dict[str, Any]:
    raw = get_json_record(SYSTEM_SETTINGS_NAMESPACE, GROUP_STUDIO_SETTINGS_KEY) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def is_group_studio_enabled() -> bool:
    """读取 group_studio.enabled；缺省或未写入时为 true。"""

    return bool(_read_group_studio_settings().get("enabled", True))


def set_group_studio_settings(
    *,
    enabled: bool | None = None,
    max_members: int | None = None,
    max_parallel_workers: int | None = None,
    max_turns: int | None = None,
) -> dict[str, Any]:
    """读-改-写合并 system_settings/group_studio。P0 强制 maxParallelWorkers=1。"""

    current = _read_group_studio_settings()
    if enabled is not None:
        current["enabled"] = bool(enabled)
    if max_members is not None:
        current["max_members"] = max(2, min(int(max_members), DEFAULT_MAX_MEMBERS))
    if max_parallel_workers is not None:
        current["max_parallel_workers"] = DEFAULT_MAX_PARALLEL_WORKERS
    if max_turns is not None:
        current["max_turns"] = max(1, min(int(max_turns), 200))
    current.setdefault("enabled", True)
    current.setdefault("max_members", DEFAULT_MAX_MEMBERS)
    current.setdefault("max_parallel_workers", DEFAULT_MAX_PARALLEL_WORKERS)
    current.setdefault("max_turns", DEFAULT_MAX_TURNS)
    current["max_parallel_workers"] = DEFAULT_MAX_PARALLEL_WORKERS
    upsert_json_record(SYSTEM_SETTINGS_NAMESPACE, GROUP_STUDIO_SETTINGS_KEY, current)
    return current


def get_group_studio_settings_public() -> dict[str, bool | int]:
    """设置页读取存储值；缺省开启，人数/轮次为 P0 上限。"""

    raw = _read_group_studio_settings()
    return {
        "enabled": bool(raw.get("enabled", True)),
        "maxMembers": int(raw.get("max_members", DEFAULT_MAX_MEMBERS) or DEFAULT_MAX_MEMBERS),
        "maxParallelWorkers": DEFAULT_MAX_PARALLEL_WORKERS,
        "maxTurns": int(raw.get("max_turns", DEFAULT_MAX_TURNS) or DEFAULT_MAX_TURNS),
    }
