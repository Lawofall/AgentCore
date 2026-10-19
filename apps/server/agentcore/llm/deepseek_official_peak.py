"""DeepSeek official Flash peak hours (CNY list).

Beijing workdays 09:00–12:00 and 14:00–18:00 = UTC [01:00, 04:00) ∪ [06:00, 10:00).
Half-open so 04:00 and 10:00 UTC are off-peak. Weekends are off-peak.
Chinese public holidays are not encoded.
"""

from __future__ import annotations

from datetime import UTC, datetime

_PEAK_HOURS = frozenset({1, 2, 3, 6, 7, 8, 9})


def is_deepseek_official_peak(ts: datetime) -> bool:
    """Whether ``ts`` falls in DeepSeek official Flash peak hours (UTC)."""
    utc = ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)
    if utc.weekday() >= 5:
        return False
    return utc.hour in _PEAK_HOURS
