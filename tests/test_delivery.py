from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from market_monitor.alerts import Alert, Candidate
from market_monitor.config import DeliveryConfig, QuietHours
from market_monitor.delivery import in_quiet_hours, plan_delivery

ROME = ZoneInfo("Europe/Rome")
QUIET = QuietHours(True, time(23, 0), time(7, 0), ROME, urgent_bypass=True)
CONFIG = DeliveryConfig(120, 10, 6, QUIET, {"stock": 8, "etf": 6, "crypto": 10})


def at(h, m, day=28):
    return datetime(2026, 10, day, h, m, tzinfo=ROME).timestamp()


def cand(symbol="ENI.MI", rule="day_move", change=3.2, urgent=False):
    return Candidate(Alert(symbol, rule, f"{rule}:{symbol}", 1, 1.0, "EUR", 0, change_pct=change, urgent=urgent), {})


@pytest.mark.parametrize(
    "when,expected",
    [(at(22, 59), False), (at(23, 0), True), (at(3, 0), True), (at(6, 59), True), (at(7, 0), False), (at(14, 0), False)],
)
def test_quiet_hours_across_midnight(when, expected):
    assert in_quiet_hours(when, QUIET) is expected


def test_quiet_hours_same_day_window():
    quiet = QuietHours(True, time(12, 0), time(14, 0), ROME, True)
    assert in_quiet_hours(at(13, 0), quiet) and not in_quiet_hours(at(14, 0), quiet)


def test_quiet_hours_disabled():
    quiet = QuietHours(False, time(23, 0), time(7, 0), ROME, True)
    assert not in_quiet_hours(at(3, 0), quiet)


def test_quiet_hours_hold_non_urgent_and_pass_urgent():
    normal, urgent = cand(change=5.1), cand("BTCEUR", change=-10.5, urgent=True)
    plan = plan_delivery([normal, urgent], at(2, 0), CONFIG, [])
    assert plan.send == [urgent] and plan.quiet == [normal]


def test_quiet_hours_without_urgent_bypass():
    config = DeliveryConfig(120, 10, 6, QuietHours(True, time(23, 0), time(7, 0), ROME, False), CONFIG.urgent_pct)
    urgent = cand(urgent=True)
    plan = plan_delivery([urgent], at(2, 0), config, [])
    assert plan.send == [] and plan.quiet == [urgent]


def test_outside_quiet_hours_everything_passes():
    alerts = [cand(change=3.1), cand("BTCEUR", "fast_move", 3.4)]
    assert len(plan_delivery(alerts, at(14, 30), CONFIG, []).send) == 2


def test_hourly_limit():
    now = at(14, 30)
    sent = [int(now) - 3500 + i * 60 for i in range(6)]  # six messages in the last hour
    plan = plan_delivery([cand()], now, CONFIG, sent)
    assert plan.send == [] and len(plan.rate_limited) == 1
    # One of them is now older than an hour.
    plan = plan_delivery([cand()], now + 200, CONFIG, sent)
    assert len(plan.send) == 1


def test_batch_limit_keeps_most_important():
    alerts = [cand(f"S{i}", "fast_move", 2.0 + i / 10) for i in range(11)]
    alerts.append(cand("URG", "day_move", -8.5, urgent=True))
    alerts.append(cand("DAY", "day_move", 3.0))
    plan = plan_delivery(alerts, at(14, 30), CONFIG, [])
    assert len(plan.send) == 10 and len(plan.overflow) == 3
    assert [c.alert.symbol for c in plan.send[:2]] == ["URG", "DAY"]
    assert plan.send[2].alert.symbol == "S10"  # biggest fast_move first
    assert {c.alert.symbol for c in plan.overflow} == {"S0", "S1", "S2"}


def test_no_candidates():
    plan = plan_delivery([], at(14, 30), CONFIG, [])
    assert plan.send == [] and plan.overflow == []
