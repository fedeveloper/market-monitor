"""Alert engine: pure functions, no I/O.

Input: the asset, its current quote, the recent samples, the persisted rule
states, the market session. Output: alert candidates, each with the state to
store if it is delivered, plus "silent" state updates that apply anyway
(re-arming, silent initialisation, open_gap done for the session).

State keys (one row each in ``alert_state``): ``fast_move``,
``day_move:up:3`` / ``day_move:down:3`` (one per step and direction),
``open_gap``, ``level_above:<L>`` / ``level_below:<L>``. The cooldown applies
per key: a re-armed step or level, or a new fast_move that is not
``step_pct`` beyond the last one, waits for the cooldown; a higher step is a
different key and is not delayed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from market_monitor.config import Asset, LevelsConfig
from market_monitor.markets import Session
from market_monitor.models import Quote
from market_monitor.storage import RuleState

# Tolerance for float comparisons on thresholds: a move of exactly 2.0% must
# trigger a 2.0% threshold even if the division gives 1.9999999999999.
EPS = 1e-9


@dataclass(frozen=True)
class Alert:
    symbol: str
    rule: str  # fast_move | day_move | open_gap | level_above | level_below
    key: str
    direction: int  # +1 up, -1 down
    price: float
    currency: str | None
    ts: int  # time of the quote that triggered it
    change_pct: float | None = None  # window change, day change or gap
    threshold: float | None = None  # threshold, step or price level
    window_minutes: int | None = None
    urgent: bool = False


@dataclass(frozen=True)
class Candidate:
    alert: Alert
    # States to store (by key) only if the alert is delivered.
    states: dict[str, RuleState]


@dataclass
class Evaluation:
    candidates: list[Candidate] = field(default_factory=list)
    silent: dict[str, RuleState] = field(default_factory=dict)


@dataclass(frozen=True)
class EngineSettings:
    cooldown_minutes: int
    interval_minutes: int
    levels: LevelsConfig
    urgent_pct: dict[str, float]


@dataclass(frozen=True)
class AssetInput:
    asset: Asset
    quote: Quote
    history: Sequence[tuple[int, float]]  # (ts, price) samples before the quote, oldest first
    states: dict[str, RuleState]
    session: Session
    now_ts: int
    # First evaluation of this asset since the process started: steps already
    # passed are recorded silently (no retroactive alert after a restart).
    first_evaluation: bool = False


def _cooled(state: RuleState | None, now_ts: int, cooldown_minutes: int) -> bool:
    return state is None or state.last_sent_ts is None or now_ts - state.last_sent_ts >= cooldown_minutes * 60


def _pct(price: float, reference: float) -> float:
    return (price - reference) / reference * 100


def _num(value: float) -> str:
    return f"{value:g}"


# --- fast_move ---------------------------------------------------------------


def reference_sample(
    history: Sequence[tuple[int, float]], quote_ts: int, window_minutes: int, interval_minutes: int,
    session_open_ts: int | None,
) -> tuple[int, float] | None:
    """The latest sample at least ``window_minutes`` older than the quote, but not
    more than one interval beyond the window, and within the same session."""
    target = quote_ts - window_minutes * 60
    oldest = target - interval_minutes * 60
    if session_open_ts is not None:
        oldest = max(oldest, session_open_ts)
    best = None
    for ts, price in history:
        if oldest <= ts <= target and price > 0:
            best = (ts, price)
    return best


def evaluate_fast_move(inp: AssetInput, settings: EngineSettings) -> Evaluation:
    result = Evaluation()
    rule = inp.asset.rules.fast_move
    if not rule.enabled:
        return result
    ref = reference_sample(
        inp.history, inp.quote.ts, rule.window_minutes, settings.interval_minutes, inp.session.open_ts
    )
    if ref is None:
        return result  # cold start or window would cross the night
    change = _pct(inp.quote.price, ref[1])
    if abs(change) + EPS < rule.threshold_pct:
        return result
    direction = 1 if change > 0 else -1
    state = inp.states.get("fast_move")
    last = state.last_level if state else None
    send = (
        last is None
        or (last > 0) != (direction > 0)
        or abs(change) + EPS >= abs(last) + rule.step_pct
        or _cooled(state, inp.now_ts, settings.cooldown_minutes)
    )
    if send:
        alert = Alert(
            inp.asset.symbol, "fast_move", "fast_move", direction, inp.quote.price, inp.quote.currency,
            inp.quote.ts, change_pct=change, threshold=rule.threshold_pct, window_minutes=rule.window_minutes,
        )
        new_state = RuleState(last_sent_ts=inp.now_ts, last_level=change, session_key=inp.session.key, armed=True)
        result.candidates.append(Candidate(alert, {"fast_move": new_state}))
    return result


# --- day_move ----------------------------------------------------------------


def day_move_key(direction: int, step: float) -> str:
    return f"day_move:{'up' if direction > 0 else 'down'}:{_num(step)}"


def evaluate_day_move(inp: AssetInput, settings: EngineSettings) -> Evaluation:
    result = Evaluation()
    asset, quote, session = inp.asset, inp.quote, inp.session
    rule = asset.rules.day_move
    if not rule.enabled or not quote.prev_close:
        return result
    change = _pct(quote.price, quote.prev_close)
    per_session = session.key is not None  # stocks/ETF reset each session; crypto never
    # After a restart, steps already passed are recorded without alerting. For
    # stocks only if the session has been open a while: the first cycle after the
    # open is a normal evaluation.
    late_start = inp.first_evaluation and (
        not per_session or inp.now_ts - session.open_ts > 2 * settings.interval_minutes * 60
    )
    urgent = abs(change) + EPS >= settings.urgent_pct.get(asset.asset_class, float("inf"))

    for direction in (1, -1):
        level = change * direction  # positive when moving in this direction
        hits: list[tuple[float, str, RuleState]] = []  # crossed, armed, cooled
        waiting: list[tuple[float, str, RuleState]] = []  # crossed, armed, still in cooldown
        for step in rule.steps_pct:
            key = day_move_key(direction, step)
            stored = inp.states.get(key)
            current = stored is not None and (not per_session or stored.session_key == session.key)
            state = stored if current else RuleState(
                last_sent_ts=stored.last_sent_ts if stored else None, session_key=session.key
            )
            crossed = level + EPS >= step
            if not state.armed:
                if level < step - rule.rearm_pct:
                    result.silent[key] = replace(state, armed=True)
                continue
            if not crossed:
                continue
            if late_start and not current:
                result.silent[key] = replace(state, last_level=step, armed=False)
                continue
            if _cooled(state, inp.now_ts, settings.cooldown_minutes):
                hits.append((step, key, state))
            else:
                waiting.append((step, key, state))  # stays armed: retried after the cooldown
        if not hits:
            continue
        top_step, top_key, _ = hits[-1]
        alert = Alert(
            asset.symbol, "day_move", top_key, direction, quote.price, quote.currency, quote.ts,
            change_pct=change, threshold=top_step, urgent=urgent,
        )
        # Lower steps still in cooldown are covered by this alert too.
        states = {
            key: RuleState(last_sent_ts=inp.now_ts, last_level=step, session_key=session.key, armed=False)
            for step, key, _ in hits + waiting
            if step <= top_step
        }
        result.candidates.append(Candidate(alert, states))
    return result


# --- open_gap ----------------------------------------------------------------


def evaluate_open_gap(inp: AssetInput, settings: EngineSettings) -> Evaluation:
    result = Evaluation()
    rule = inp.asset.rules.open_gap
    quote, session = inp.quote, inp.session
    if not rule.enabled or session.open_ts is None or not quote.prev_close:
        return result
    state = inp.states.get("open_gap")
    if state is not None and state.session_key == session.key:
        return result  # already evaluated this session
    if quote.ts < session.open_ts:
        return result  # still the previous session's price
    done = RuleState(
        last_sent_ts=state.last_sent_ts if state else None, session_key=session.key, armed=False
    )
    if quote.ts - session.open_ts > rule.max_delay_minutes * 60:
        result.silent["open_gap"] = done  # first data too late: skipped for this session
        return result
    gap = _pct(quote.price, quote.prev_close)
    if abs(gap) + EPS < rule.threshold_pct:
        result.silent["open_gap"] = done
        return result
    alert = Alert(
        inp.asset.symbol, "open_gap", "open_gap", 1 if gap > 0 else -1, quote.price, quote.currency, quote.ts,
        change_pct=gap, threshold=rule.threshold_pct,
    )
    result.candidates.append(Candidate(alert, {"open_gap": replace(done, last_sent_ts=inp.now_ts, last_level=gap)}))
    return result


# --- price levels ------------------------------------------------------------


def evaluate_levels(inp: AssetInput, settings: EngineSettings) -> Evaluation:
    result = Evaluation()
    if not settings.levels.enabled:
        return result
    price = inp.quote.price
    hysteresis = settings.levels.hysteresis_pct / 100
    checks = [("level_above", 1, level) for level in inp.asset.levels.above]
    checks += [("level_below", -1, level) for level in inp.asset.levels.below]
    for rule, direction, level in checks:
        key = f"{rule}:{_num(level)}"
        state = inp.states.get(key)
        reached = price >= level if direction > 0 else price <= level
        if state is None:
            if reached:
                # Already beyond the level when first seen: no crossing observed.
                result.silent[key] = RuleState(last_level=level, armed=False)
            continue
        if not state.armed:
            away = price < level * (1 - hysteresis) if direction > 0 else price > level * (1 + hysteresis)
            if away:
                result.silent[key] = replace(state, armed=True)
            continue
        if reached and _cooled(state, inp.now_ts, settings.cooldown_minutes):
            alert = Alert(
                inp.asset.symbol, rule, key, direction, price, inp.quote.currency, inp.quote.ts, threshold=level
            )
            result.candidates.append(
                Candidate(alert, {key: RuleState(last_sent_ts=inp.now_ts, last_level=level, armed=False)})
            )
    return result


# --- all rules ---------------------------------------------------------------

EVALUATORS = (evaluate_fast_move, evaluate_day_move, evaluate_open_gap, evaluate_levels)


def evaluate(inp: AssetInput, settings: EngineSettings) -> Evaluation:
    """Evaluate every rule for one asset with a fresh quote."""
    total = Evaluation()
    for evaluator in EVALUATORS:
        part = evaluator(inp, settings)
        total.candidates.extend(part.candidates)
        total.silent.update(part.silent)
    return total
