"""Alert engine: the cases listed in docs/ALERTS.md."""

from dataclasses import replace

import pytest

from market_monitor.alerts import (
    AssetInput,
    EngineSettings,
    evaluate,
    evaluate_day_move,
    evaluate_fast_move,
    evaluate_levels,
    evaluate_open_gap,
    reference_sample,
)
from market_monitor.config import (
    Asset,
    AssetRules,
    DayMoveRule,
    FastMoveRule,
    LevelsConfig,
    OpenGapRule,
    PriceLevels,
)
from market_monitor.markets import CONTINUOUS, Session
from market_monitor.models import Quote
from market_monitor.storage import RuleState

OPEN = 1_793_174_400  # 2026-10-28 09:00 Europe/Rome
SESSION = Session("2026-10-28", OPEN, OPEN + 30_600)
MIN = 60

STOCK_RULES = AssetRules(
    FastMoveRule(True, 60, 2.0, 1.0),
    DayMoveRule(True, (3.0, 5.0, 8.0), "prev_close", 1.0),
    OpenGapRule(True, 2.0, 45),
)
CRYPTO_RULES = AssetRules(
    FastMoveRule(True, 60, 3.0, 1.5),
    DayMoveRule(True, (5.0, 8.0, 12.0), "rolling_24h", 1.5),
    OpenGapRule(False),
)
SETTINGS = EngineSettings(
    cooldown_minutes=120,
    interval_minutes=15,
    levels=LevelsConfig(True, 0.5),
    urgent_pct={"stock": 8, "etf": 6, "crypto": 10},
)


def stock(levels=PriceLevels(), rules=STOCK_RULES):
    return Asset("ENI.MI", "Eni", "stock", "IT", "yahoo", True, levels, rules)


def crypto(levels=PriceLevels()):
    return Asset("BTCEUR", "Bitcoin", "crypto", "CRYPTO", "binance", True, levels, CRYPTO_RULES)


def make_input(price, *, prev=100.0, at=OPEN + 3 * 3600, history=(), states=None, asset=None,
               session=SESSION, first=False, now=None):
    asset = asset or stock()
    quote = Quote(asset.symbol, at, price, prev_close=prev, currency="EUR")
    return AssetInput(asset, quote, list(history), dict(states or {}), session, now if now is not None else at, first)


def apply(inp, evaluation, delivered=True):
    """Return the states after applying an evaluation (as the cycle would)."""
    states = dict(inp.states)
    states.update(evaluation.silent)
    if delivered:
        for candidate in evaluation.candidates:
            states.update(candidate.states)
    return states


# --- fast_move ---------------------------------------------------------------


def history_flat(price, end, count=8, step=900):
    return [(end - i * step, price) for i in range(count, 0, -1)]


def test_fast_move_threshold_exactly_on_limit():
    at = OPEN + 3 * 3600
    inp = make_input(102.0, at=at, history=[(at - 3600, 100.0)])
    ev = evaluate_fast_move(inp, SETTINGS)
    assert len(ev.candidates) == 1
    alert = ev.candidates[0].alert
    assert alert.rule == "fast_move" and alert.direction == 1
    assert alert.change_pct == pytest.approx(2.0) and alert.window_minutes == 60


def test_fast_move_just_below_threshold():
    at = OPEN + 3 * 3600
    inp = make_input(101.99, at=at, history=[(at - 3600, 100.0)])
    assert evaluate_fast_move(inp, SETTINGS).candidates == []


def test_fast_move_down():
    at = OPEN + 3 * 3600
    inp = make_input(97.5, at=at, history=[(at - 3600, 100.0)])
    assert evaluate_fast_move(inp, SETTINGS).candidates[0].alert.direction == -1


def test_fast_move_cold_start_is_silent():
    at = OPEN + 3 * 3600
    # Only 45 minutes of history: the 60-minute window is not full.
    inp = make_input(110.0, at=at, history=[(at - 2700, 100.0), (at - 1800, 101.0)])
    ev = evaluate_fast_move(inp, SETTINGS)
    assert ev.candidates == [] and ev.silent == {}


def test_fast_move_window_never_crosses_the_night():
    at = OPEN + 30 * MIN  # 09:30: a 60-minute window would reach yesterday
    yesterday = OPEN - 16 * 3600
    inp = make_input(110.0, at=at, history=[(yesterday, 100.0), (OPEN, 109.0)])
    assert evaluate_fast_move(inp, SETTINGS).candidates == []


def test_fast_move_crypto_window_crosses_midnight():
    at = 1_793_142_000 + 1800  # 00:30 local, no sessions for crypto
    inp = make_input(104.0, at=at, history=[(at - 3600, 100.0)], asset=crypto(), session=CONTINUOUS)
    assert len(evaluate_fast_move(inp, SETTINGS).candidates) == 1


def test_reference_sample_tolerates_one_interval():
    q_ts = 10_000
    assert reference_sample([(q_ts - 3600 - 900, 1.0)], q_ts, 60, 15, None) == (q_ts - 4500, 1.0)
    assert reference_sample([(q_ts - 3600 - 901, 1.0)], q_ts, 60, 15, None) is None
    assert reference_sample([(q_ts - 3000, 1.0)], q_ts, 60, 15, None) is None


def test_fast_move_same_direction_needs_step_or_cooldown():
    at = OPEN + 3 * 3600
    first = make_input(102.5, at=at, history=[(at - 3600, 100.0)])
    states = apply(first, evaluate_fast_move(first, SETTINGS))
    assert states["fast_move"].last_level == pytest.approx(2.5)

    # 15 minutes later, +3.0% over the window: less than +1.0 beyond 2.5 -> silent.
    t2 = at + 900
    second = make_input(103.0, at=t2, history=[(t2 - 3600, 100.0)], states=states)
    assert evaluate_fast_move(second, SETTINGS).candidates == []

    # +3.5%: one step beyond the last notified level -> alert even within cooldown.
    third = make_input(103.5, at=t2, history=[(t2 - 3600, 100.0)], states=states)
    assert len(evaluate_fast_move(third, SETTINGS).candidates) == 1

    # Same +3.0% but after the cooldown -> alert.
    t3 = at + 121 * MIN
    fourth = make_input(103.0, at=t3, history=[(t3 - 3600, 100.0)], states=states)
    assert len(evaluate_fast_move(fourth, SETTINGS).candidates) == 1


def test_fast_move_opposite_direction_not_blocked_by_cooldown():
    at = OPEN + 3 * 3600
    states = {"fast_move": RuleState(last_sent_ts=at - 900, last_level=2.5)}
    inp = make_input(97.9, at=at, history=[(at - 3600, 100.0)], states=states)
    assert evaluate_fast_move(inp, SETTINGS).candidates[0].alert.direction == -1


def test_fast_move_disabled():
    rules = replace(STOCK_RULES, fast_move=FastMoveRule(False))
    at = OPEN + 3 * 3600
    inp = make_input(110.0, at=at, history=[(at - 3600, 100.0)], asset=stock(rules=rules))
    assert evaluate_fast_move(inp, SETTINGS).candidates == []


# --- day_move ----------------------------------------------------------------


def day_alerts(inp):
    return [c.alert for c in evaluate_day_move(inp, SETTINGS).candidates]


def test_day_move_steps_going_up():
    t = OPEN + 3600
    states = {}
    sent = []
    for minutes, price in [(0, 102.9), (15, 103.2), (30, 104.0), (45, 105.0), (60, 107.9), (75, 108.1)]:
        inp = make_input(price, at=t + minutes * MIN, states=states)
        ev = evaluate_day_move(inp, SETTINGS)
        sent += [(c.alert.threshold, round(c.alert.change_pct, 1)) for c in ev.candidates]
        states = apply(inp, ev)
    assert sent == [(3.0, 3.2), (5.0, 5.0), (8.0, 8.1)]


def test_day_move_steps_going_down():
    inp = make_input(94.8)
    alerts = day_alerts(inp)
    assert [(a.direction, a.threshold, a.key) for a in alerts] == [(-1, 5.0, "day_move:down:5")]
    states = apply(inp, evaluate_day_move(inp, SETTINGS))
    assert not states["day_move:down:3"].armed and not states["day_move:down:5"].armed
    assert "day_move:up:3" not in states


def test_day_move_jump_over_several_steps_sends_one_alert():
    inp = make_input(108.5)
    alerts = day_alerts(inp)
    assert len(alerts) == 1 and alerts[0].threshold == 8.0
    assert alerts[0].urgent is True


def test_day_move_rearm_with_hysteresis_and_cooldown():
    t = OPEN + 3600
    inp = make_input(103.2, at=t)
    states = apply(inp, evaluate_day_move(inp, SETTINGS))
    # Back to +2.5%: not enough to re-arm (needs < 3 - 1.0 = 2.0).
    inp = make_input(102.5, at=t + 30 * MIN, states=states)
    ev = evaluate_day_move(inp, SETTINGS)
    assert ev.silent == {}
    # +1.9%: re-armed silently.
    inp = make_input(101.9, at=t + 45 * MIN, states=states)
    ev = evaluate_day_move(inp, SETTINGS)
    assert ev.silent["day_move:up:3"].armed is True and ev.candidates == []
    states = apply(inp, ev)
    # +3.1% again within the cooldown: armed but waiting.
    inp = make_input(103.1, at=t + 60 * MIN, states=states)
    assert day_alerts(inp) == []
    # After the cooldown: alert again.
    inp = make_input(103.1, at=t + 121 * MIN, states=states)
    assert [a.threshold for a in day_alerts(inp)] == [3.0]


def test_day_move_higher_step_covers_lower_step_in_cooldown():
    t = OPEN + 3600
    states = {"day_move:up:3": RuleState(last_sent_ts=t - 600, session_key=SESSION.key, armed=True)}
    inp = make_input(105.2, at=t, states=states)
    ev = evaluate_day_move(inp, SETTINGS)
    assert [c.alert.threshold for c in ev.candidates] == [5.0]
    assert set(ev.candidates[0].states) == {"day_move:up:3", "day_move:up:5"}


def test_day_move_new_session_resets_steps():
    yesterday = RuleState(last_sent_ts=OPEN - 20 * 3600, last_level=3.0, session_key="2026-10-27", armed=False)
    inp = make_input(103.5, states={"day_move:up:3": yesterday})
    assert [a.threshold for a in day_alerts(inp)] == [3.0]


def test_day_move_restart_mid_session_initialises_silently():
    inp = make_input(105.5, at=OPEN + 5 * 3600, first=True)
    ev = evaluate_day_move(inp, SETTINGS)
    assert ev.candidates == []
    assert {k for k, v in ev.silent.items() if not v.armed} == {"day_move:up:3", "day_move:up:5"}
    # The next step still alerts normally.
    states = apply(inp, ev)
    inp = make_input(108.2, at=OPEN + 5 * 3600 + 900, states=states)
    assert [a.threshold for a in day_alerts(inp)] == [8.0]


def test_day_move_restart_keeps_current_session_state():
    state = RuleState(last_sent_ts=OPEN + 3600, last_level=3.0, session_key=SESSION.key, armed=False)
    inp = make_input(103.5, at=OPEN + 5 * 3600, first=True, states={"day_move:up:3": state})
    ev = evaluate_day_move(inp, SETTINGS)
    assert ev.candidates == [] and ev.silent == {}


def test_day_move_first_cycle_after_open_is_not_silent():
    inp = make_input(103.5, at=OPEN + 15 * MIN, first=True)
    assert [a.threshold for a in day_alerts(inp)] == [3.0]


def test_day_move_crypto_no_reset_at_midnight():
    asset = crypto()
    midnight = 1_793_142_000
    inp = make_input(94.0, at=midnight - 2 * 3600, asset=asset, session=CONTINUOUS)
    states = apply(inp, evaluate_day_move(inp, SETTINGS))
    # After midnight the rolling 24h change is still -6%: no new alert.
    inp = make_input(94.0, at=midnight + 900, asset=asset, session=CONTINUOUS, states=states)
    assert day_alerts(inp) == []
    # Back to -3.4% (re-arm needs above -(5 - 1.5)) and down again after the cooldown.
    inp = make_input(96.6, at=midnight + 3600, asset=asset, session=CONTINUOUS, states=states)
    states = apply(inp, evaluate_day_move(inp, SETTINGS))
    inp = make_input(94.9, at=midnight + 3 * 3600, asset=asset, session=CONTINUOUS, states=states)
    assert [a.threshold for a in day_alerts(inp)] == [5.0]


def test_day_move_crypto_restart_with_saved_state_and_without():
    asset = crypto()
    inp = make_input(94.0, asset=asset, session=CONTINUOUS, first=True)
    ev = evaluate_day_move(inp, SETTINGS)
    assert ev.candidates == [] and not ev.silent["day_move:down:5"].armed


def test_day_move_urgent_flag():
    assert day_alerts(make_input(107.9))[0].urgent is False
    assert day_alerts(make_input(91.9))[0].urgent is True


def test_day_move_missing_reference():
    assert day_alerts(make_input(110.0, prev=None)) == []


# --- open_gap ----------------------------------------------------------------


def test_open_gap_on_first_sample():
    inp = make_input(102.5, at=OPEN + 60)
    ev = evaluate_open_gap(inp, SETTINGS)
    alert = ev.candidates[0].alert
    assert alert.rule == "open_gap" and alert.change_pct == pytest.approx(2.5)
    states = apply(inp, ev)
    # Evaluated once per session.
    assert evaluate_open_gap(make_input(105.0, at=OPEN + 900, states=states), SETTINGS).candidates == []


def test_open_gap_small_gap_closes_the_session():
    inp = make_input(100.5, at=OPEN + 60)
    ev = evaluate_open_gap(inp, SETTINGS)
    assert ev.candidates == [] and ev.silent["open_gap"].session_key == SESSION.key


def test_open_gap_first_data_too_late_is_skipped():
    inp = make_input(105.0, at=OPEN + 46 * MIN)
    ev = evaluate_open_gap(inp, SETTINGS)
    assert ev.candidates == [] and "open_gap" in ev.silent


def test_open_gap_ignores_previous_session_price():
    inp = make_input(105.0, at=OPEN - 16 * 3600)
    ev = evaluate_open_gap(inp, SETTINGS)
    assert ev.candidates == [] and ev.silent == {}


def test_open_gap_next_session_evaluated_again():
    states = {"open_gap": RuleState(last_sent_ts=OPEN - 86400, session_key="2026-10-27", armed=False)}
    assert len(evaluate_open_gap(make_input(97.0, at=OPEN + 60, states=states), SETTINGS).candidates) == 1


def test_open_gap_not_for_crypto():
    inp = make_input(110.0, asset=crypto(), session=CONTINUOUS)
    assert evaluate_open_gap(inp, SETTINGS).candidates == []


# --- levels ------------------------------------------------------------------


def test_level_above_crossing_and_hysteresis():
    asset = stock(PriceLevels(above=(16.0,)))
    inp = make_input(15.9, asset=asset, prev=15.0)
    states = apply(inp, evaluate_levels(inp, SETTINGS))
    assert states == {}
    states["level_above:16"] = RuleState()  # armed (seen below the level)
    inp = make_input(16.0, asset=asset, prev=15.0, states=states)
    ev = evaluate_levels(inp, SETTINGS)
    assert [c.alert.threshold for c in ev.candidates] == [16.0]
    states = apply(inp, ev)
    # Oscillating around the level: no repeat.
    for price in (15.95, 16.05, 15.93):
        inp = make_input(price, asset=asset, prev=15.0, states=states, at=OPEN + 6 * 3600)
        ev = evaluate_levels(inp, SETTINGS)
        assert ev.candidates == []
        states = apply(inp, ev)
    # Below 16 * (1 - 0.5%) = 15.92: re-armed, and alerts again on the next crossing.
    inp = make_input(15.91, asset=asset, prev=15.0, states=states, at=OPEN + 6 * 3600)
    states = apply(inp, evaluate_levels(inp, SETTINGS))
    assert states["level_above:16"].armed
    inp = make_input(16.1, asset=asset, prev=15.0, states=states, at=OPEN + 8 * 3600)
    assert len(evaluate_levels(inp, SETTINGS).candidates) == 1


def test_level_already_beyond_on_first_sight_is_silent():
    asset = stock(PriceLevels(below=(13.5,)))
    inp = make_input(13.0, asset=asset, prev=14.0)
    ev = evaluate_levels(inp, SETTINGS)
    assert ev.candidates == [] and ev.silent["level_below:13.5"].armed is False


def test_level_below_crossing():
    asset = stock(PriceLevels(below=(13.5,)))
    inp = make_input(13.5, asset=asset, prev=14.0, states={"level_below:13.5": RuleState()})
    alert = evaluate_levels(inp, SETTINGS).candidates[0].alert
    assert alert.rule == "level_below" and alert.direction == -1


def test_levels_disabled():
    settings = replace(SETTINGS, levels=LevelsConfig(False, 0.5))
    asset = stock(PriceLevels(above=(10.0,)))
    inp = make_input(16.0, asset=asset, states={"level_above:10": RuleState()})
    assert evaluate_levels(inp, settings).candidates == []


# --- combined ----------------------------------------------------------------


def test_evaluate_combines_rules_in_one_cycle():
    at = OPEN + 3 * 3600
    asset = stock(PriceLevels(above=(104.0,)))
    inp = make_input(104.5, at=at, history=[(at - 3600, 102.0)], asset=asset,
                     states={"level_above:104": RuleState()})
    rules = sorted(c.alert.rule for c in evaluate(inp, SETTINGS).candidates)
    assert rules == ["day_move", "fast_move", "level_above"]
