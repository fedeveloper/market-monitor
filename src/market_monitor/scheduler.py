"""Cycle scheduling aligned to round clock times (:00, :15, :30, :45 with a 15 minute interval)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def next_run(now_ts: float, interval_minutes: int, align: bool, tz: ZoneInfo) -> float:
    """Epoch of the next cycle strictly after ``now_ts``.

    Aligned slots are counted from local midnight in ``tz`` (the interval divides
    24 hours, enforced by config validation).
    """
    interval = interval_minutes * 60
    if not align:
        return now_ts + interval
    local = datetime.fromtimestamp(now_ts, tz)
    midnight = datetime.combine(local.date(), datetime.min.time(), tz)
    elapsed = now_ts - midnight.timestamp()
    slot = math.floor(elapsed / interval) + 1
    candidate = midnight.timestamp() + slot * interval
    # Past the last slot of the day (or on a DST change): next local midnight.
    next_midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tz).timestamp()
    return min(candidate, next_midnight)
