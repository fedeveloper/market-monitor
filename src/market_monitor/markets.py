"""Market hours: open/closed state and trading sessions from config.yaml (no holiday calendar).

Holidays are handled by the freshness check: on a closed day the provider keeps
returning the last price, which soon becomes older than ``stale_data_minutes``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from market_monitor.config import MarketHours

# Free data lags about 15 minutes: keep fetching a little after the close to
# get the final prices of the session.
CLOSE_GRACE_MINUTES = 20


@dataclass(frozen=True)
class Session:
    """A trading session. Crypto markets have no sessions (key and times are None)."""

    key: str | None
    open_ts: int | None = None
    close_ts: int | None = None


CONTINUOUS = Session(key=None)


def session_on(market: MarketHours, now_ts: float) -> Session | None:
    """The session of the market's local day containing ``now_ts``, or None if it is not a trading day."""
    if market.always_open:
        return CONTINUOUS
    local = datetime.fromtimestamp(now_ts, market.timezone)
    if local.weekday() not in market.days:
        return None
    day = local.date()
    opens = datetime.combine(day, market.open, market.timezone)
    closes = datetime.combine(day, market.close, market.timezone)
    return Session(key=day.isoformat(), open_ts=int(opens.timestamp()), close_ts=int(closes.timestamp()))


def is_open(market: MarketHours, now_ts: float, grace_minutes: int = 0) -> bool:
    session = session_on(market, now_ts)
    if session is None:
        return False
    if session is CONTINUOUS:
        return True
    return session.open_ts <= now_ts < session.close_ts + grace_minutes * 60


def should_fetch(market: MarketHours, now_ts: float) -> bool:
    """Fetch from the open until a short grace period after the close."""
    return is_open(market, now_ts, CLOSE_GRACE_MINUTES)

