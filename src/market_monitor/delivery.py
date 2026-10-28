"""Delivery filters (pure): quiet hours with urgent bypass, hourly message limit, batching.

Suppressed alerts are simply not delivered: their rule state is not updated,
so if the condition still holds when the suppression ends the alert goes out then.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, time

from market_monitor.alerts import Candidate
from market_monitor.config import DeliveryConfig, QuietHours

_RULE_PRIORITY = {"day_move": 0, "fast_move": 1, "open_gap": 2, "level_above": 3, "level_below": 3}


@dataclass
class DeliveryPlan:
    send: list[Candidate] = field(default_factory=list)
    quiet: list[Candidate] = field(default_factory=list)  # held by quiet hours
    rate_limited: list[Candidate] = field(default_factory=list)
    overflow: list[Candidate] = field(default_factory=list)  # beyond max_alerts_per_message


def in_quiet_hours(now_ts: float, quiet: QuietHours) -> bool:
    if not quiet.enabled:
        return False
    now = datetime.fromtimestamp(now_ts, quiet.timezone).time().replace(second=0, microsecond=0)
    return _in_window(now, quiet.start, quiet.end)


def _in_window(now: time, start: time, end: time) -> bool:
    if start < end:
        return start <= now < end
    return now >= start or now < end  # across midnight, e.g. 23:00-07:00


def priority(candidate: Candidate) -> tuple:
    alert = candidate.alert
    return (not alert.urgent, _RULE_PRIORITY.get(alert.rule, 9), -abs(alert.change_pct or 0))


def plan_delivery(
    candidates: Sequence[Candidate], now_ts: float, config: DeliveryConfig, sent_message_times: Sequence[int]
) -> DeliveryPlan:
    """Decide which alerts go in this cycle's message.

    ``sent_message_times``: epochs of alert messages already sent (only the last hour matters).
    """
    plan = DeliveryPlan()
    pending = list(candidates)
    if in_quiet_hours(now_ts, config.quiet_hours):
        bypass = config.quiet_hours.urgent_bypass
        plan.quiet = [c for c in pending if not (bypass and c.alert.urgent)]
        pending = [c for c in pending if bypass and c.alert.urgent]
    if pending:
        recent = sum(1 for ts in sent_message_times if now_ts - ts < 3600)
        if recent >= config.max_messages_per_hour:
            plan.rate_limited, pending = pending, []
    pending.sort(key=priority)
    plan.send = pending[: config.max_alerts_per_message]
    plan.overflow = pending[config.max_alerts_per_message :]
    return plan


def quiet_now(now_ts: float, config: DeliveryConfig) -> bool:
    """For non-alert messages (service health): held during quiet hours."""
    return in_quiet_hours(now_ts, config.quiet_hours)
