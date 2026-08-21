"""weekly / once 钟面调度计算。Phase 1 只用 zoneinfo + tzdata，不解析 cron。"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

WEEKDAY_INDEX = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


class ScheduleError(ValueError):
    """调度表达式或时区不合法。"""


def load_timezone(name: str) -> ZoneInfo:
    """加载 IANA 时区；Windows 依赖显式 tzdata 包。"""

    if not name or not str(name).strip():
        raise ScheduleError("timezone 不能为空")
    try:
        return ZoneInfo(str(name).strip())
    except ZoneInfoNotFoundError as exc:
        raise ScheduleError(f"未知时区: {name}") from exc


def parse_clock(value: str) -> tuple[int, int]:
    """解析 HH:MM 钟面。"""

    raw = str(value or "").strip()
    parts = raw.split(":")
    if len(parts) != 2:
        raise ScheduleError(f"非法时钟: {value}")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ScheduleError(f"非法时钟: {value}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleError(f"非法时钟: {value}")
    return hour, minute


def to_utc_iso(value: datetime) -> str:
    """统一存 UTC ISO，避免不同偏移字符串破坏 UNIQUE 比较。"""

    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_stored_datetime(value: str) -> datetime:
    """读取库内 ISO 时间，缺时区时按 UTC。"""

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_schedule_expr(kind: str, expr: dict[str, Any] | None) -> dict[str, Any]:
    """校验 Phase 1 调度表达式并规范化。"""

    payload = expr if isinstance(expr, dict) else {}
    if kind == "weekly":
        weekdays = payload.get("weekdays")
        at = payload.get("at")
        if not isinstance(weekdays, list) or not weekdays:
            raise ScheduleError("weekly 需要非空 weekdays")
        normalized: list[str] = []
        for item in weekdays:
            key = str(item).strip().lower()
            if key not in WEEKDAY_INDEX:
                raise ScheduleError(f"非法 weekday: {item}")
            if key not in normalized:
                normalized.append(key)
        parse_clock(str(at or ""))
        return {"weekdays": normalized, "at": str(at).strip()}
    if kind == "once":
        at = payload.get("at")
        if not isinstance(at, str) or not at.strip():
            raise ScheduleError("once 需要 at")
        try:
            parsed = datetime.fromisoformat(at.strip())
        except ValueError as exc:
            raise ScheduleError(f"非法 once.at: {at}") from exc
        if parsed.tzinfo is not None:
            raise ScheduleError("once.at 不得带时区偏移")
        return {"at": parsed.replace(microsecond=0).isoformat()}
    raise ScheduleError(f"Phase 1 不支持 schedule_kind={kind}")


def _wall_clock(day: date, hour: int, minute: int, tz: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


def next_weekly_anchor_after(
    after: datetime,
    *,
    weekdays: list[str],
    at: str,
    timezone_name: str,
) -> datetime:
    """从 after 之后的下一钟面锚点（weekday + HH:MM），不是 after+7 天。"""

    tz = load_timezone(timezone_name)
    after_utc = after if after.tzinfo is not None else after.replace(tzinfo=timezone.utc)
    after_utc = after_utc.astimezone(timezone.utc)
    hour, minute = parse_clock(at)
    wanted = {WEEKDAY_INDEX[name] for name in weekdays}
    if not wanted:
        raise ScheduleError("weekly 需要非空 weekdays")
    local = after_utc.astimezone(tz)
    start_date = local.date()
    for offset in range(0, 8):
        day = start_date + timedelta(days=offset)
        if day.weekday() not in wanted:
            continue
        candidate = _wall_clock(day, hour, minute, tz).astimezone(timezone.utc)
        if candidate > after_utc:
            return candidate.replace(microsecond=0)
    raise ScheduleError("无法计算下一 weekly 锚点")


def once_run_at(at: str, timezone_name: str) -> datetime:
    """once.at 为无偏移本地时间，时区用 timezone 列。"""

    tz = load_timezone(timezone_name)
    try:
        parsed = datetime.fromisoformat(at)
    except ValueError as exc:
        raise ScheduleError(f"非法 once.at: {at}") from exc
    if parsed.tzinfo is not None:
        raise ScheduleError("once.at 不得带时区偏移")
    return parsed.replace(tzinfo=tz).astimezone(timezone.utc).replace(microsecond=0)


def compute_next_run_at(
    *,
    schedule_kind: str,
    schedule_expr: dict[str, Any],
    timezone_name: str,
    after: datetime,
) -> datetime:
    """确认启用时计算 next_run_at；weekly 取 after 之后下一锚点，once 取定点。"""

    expr = validate_schedule_expr(schedule_kind, schedule_expr)
    if schedule_kind == "weekly":
        return next_weekly_anchor_after(
            after,
            weekdays=expr["weekdays"],
            at=expr["at"],
            timezone_name=timezone_name,
        )
    return once_run_at(expr["at"], timezone_name)
