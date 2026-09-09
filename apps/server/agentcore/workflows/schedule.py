"""Cron helpers for workflow triggers (5-field, no external cron library).

Field order: minute hour day-of-month month day-of-week.
Dow: 0/7 = Sunday … 6 = Saturday (cron convention).

Day matching follows Vixie cron: when **both** day-of-month and day-of-week are
restricted (neither starts with ``*``), a day matches if **either** field matches
(OR). When at least one of them is a star field, both must match (AND, which for
a star field is trivially true). ``0 9 1 * 1`` therefore means「每月 1 号 **或**
每周一 09:00」, not「1 号且恰好是周一」.
"""

from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_FIELD_PART = re.compile(r"^(\*|\d+(?:-\d+)?)(?:/(\d+))?$")


class CronError(ValueError):
    """Malformed cron expression."""


@dataclass(frozen=True, slots=True)
class _Field:
    values: frozenset[int]


def _parse_field(raw: str, *, minimum: int, maximum: int, name: str) -> _Field:
    raw = raw.strip()
    if not raw:
        raise CronError(f"cron {name} 为空")
    values: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        m = _FIELD_PART.match(part)
        if not m:
            raise CronError(f"cron {name} 非法: {part!r}")
        base, step_s = m.group(1), m.group(2)
        step = int(step_s) if step_s else 1
        if step < 1:
            raise CronError(f"cron {name} 步长必须 ≥1")
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(base)
        if start > end or start < minimum or end > maximum:
            raise CronError(f"cron {name} 超出范围 [{minimum},{maximum}]: {part!r}")
        values.update(range(start, end + 1, step))
    if not values:
        raise CronError(f"cron {name} 无匹配值")
    return _Field(frozenset(values))


@dataclass(frozen=True, slots=True)
class CronExpr:
    minute: _Field
    hour: _Field
    dom: _Field
    month: _Field
    dow: _Field
    # Vixie's DOM_STAR / DOW_STAR: set when the field starts with ``*`` (``*`` or
    # ``*/n``). They pick AND vs OR for the two day fields — see ``_day_matches``.
    dom_star: bool = True
    dow_star: bool = True


def _is_star_field(raw: str) -> bool:
    return raw.strip().startswith("*")


def parse_cron(expr: str) -> CronExpr:
    """Parse and validate a 5-field cron string."""
    parts = expr.strip().split()
    if len(parts) != 5:
        raise CronError("cron 须为 5 段（分 时 日 月 周）")
    minute = _parse_field(parts[0], minimum=0, maximum=59, name="minute")
    hour = _parse_field(parts[1], minimum=0, maximum=23, name="hour")
    dom = _parse_field(parts[2], minimum=1, maximum=31, name="dom")
    month = _parse_field(parts[3], minimum=1, maximum=12, name="month")
    dow_raw = _parse_field(parts[4], minimum=0, maximum=7, name="dow")
    # Normalize 7 → 0 (Sunday).
    dow_vals = {(0 if v == 7 else v) for v in dow_raw.values}
    return CronExpr(
        minute=minute,
        hour=hour,
        dom=dom,
        month=month,
        dow=_Field(frozenset(dow_vals)),
        dom_star=_is_star_field(parts[2]),
        dow_star=_is_star_field(parts[4]),
    )


def validate_cron(expr: str) -> str:
    """Validate and return the stripped cron expression."""
    cleaned = " ".join(expr.strip().split())
    parse_cron(cleaned)
    return cleaned


def _day_matches(expr: CronExpr, dt: datetime) -> bool:
    """Vixie day rule: OR the two day fields when both are restricted, else AND."""
    # Python weekday: Mon=0 … Sun=6 → cron: Sun=0, Mon=1 … Sat=6
    cron_dow = (dt.weekday() + 1) % 7
    dom_hit = dt.day in expr.dom.values
    dow_hit = cron_dow in expr.dow.values
    if expr.dom_star or expr.dow_star:
        return dom_hit and dow_hit
    return dom_hit or dow_hit


def _matches(expr: CronExpr, dt: datetime) -> bool:
    return (
        dt.minute in expr.minute.values
        and dt.hour in expr.hour.values
        and dt.month in expr.month.values
        and _day_matches(expr, dt)
    )


def next_run_after(cron: str, after: datetime, *, max_steps: int = 366 * 24 * 60) -> datetime:
    """Next fire strictly after ``after`` (minute resolution, timezone-aware)."""
    expr = parse_cron(cron)
    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)
    # Start at the next whole minute.
    cursor = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(max_steps):
        # Fast-skip months / days that cannot match.
        if cursor.month not in expr.month.values:
            # Jump to 1st of next candidate month at 00:00.
            year, month = cursor.year, cursor.month
            while True:
                month += 1
                if month > 12:
                    month = 1
                    year += 1
                if month in expr.month.values:
                    break
            cursor = datetime(year, month, 1, tzinfo=cursor.tzinfo)
            continue
        if not _day_matches(expr, cursor):
            # Next day 00:00.
            nxt = (cursor + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            # Clamp day overflow when month changes mid-skip.
            dim = monthrange(nxt.year, nxt.month)[1]
            if nxt.day > dim:
                nxt = nxt.replace(day=dim)
            cursor = nxt
            continue
        if cursor.hour not in expr.hour.values:
            for h in range(cursor.hour + 1, 24):
                if h in expr.hour.values:
                    cursor = cursor.replace(hour=h, minute=0)
                    break
            else:
                cursor = (cursor + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
            continue
        if cursor.minute not in expr.minute.values:
            for m in range(cursor.minute + 1, 60):
                if m in expr.minute.values:
                    cursor = cursor.replace(minute=m)
                    break
            else:
                cursor = cursor.replace(minute=0) + timedelta(hours=1)
            continue
        if _matches(expr, cursor):
            return cursor
        cursor += timedelta(minutes=1)
    raise CronError("无法在合理范围内计算下次执行时间")


# Named presets → cron (product shortcuts; stored as cron on the row).
# Desktop wire uses schedule_preset names only (no short aliases).
CRON_PRESETS: dict[str, str] = {
    "hourly": "0 * * * *",
    "daily": "0 9 * * *",
    "weekdays": "0 9 * * 1-5",
    "weekly_mon": "0 9 * * 1",
    "weekly_fri": "0 9 * * 5",
    "monthly_1": "0 9 1 * *",
}

# Prefer these when reversing cron → schedule_preset for API responses.
_PRESET_INFER_ORDER: tuple[str, ...] = (
    "daily",
    "weekdays",
    "weekly_mon",
    "weekly_fri",
    "monthly_1",
    "hourly",
)


def infer_schedule_preset(cron: str) -> str:
    """Map stored cron back to a desktop ``schedule_preset`` (else ``custom``)."""
    cleaned = " ".join(cron.strip().split())
    for name in _PRESET_INFER_ORDER:
        if CRON_PRESETS[name] == cleaned:
            return name
    return "custom"


def resolve_cron(
    *, cron: str | None = None, schedule_preset: str | None = None
) -> str:
    """Resolve a cron expression from either an explicit cron or a named preset.

    ``schedule_preset="custom"`` is not a cron map key — callers must pass ``cron`` alone.
    """
    if schedule_preset:
        key = schedule_preset.strip().lower()
        if key == "custom":
            if not cron:
                raise CronError("schedule_preset=custom 时须提供 cron")
            return validate_cron(cron)
        if cron:
            raise CronError("命名 schedule_preset 时不要同时传 cron")
        if key not in CRON_PRESETS:
            raise CronError(f"未知 schedule_preset: {schedule_preset!r}")
        return CRON_PRESETS[key]
    if not cron:
        raise CronError("须提供 schedule_preset 或 cron")
    return validate_cron(cron)
