from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from market_monitor.config import MarketHours
from market_monitor.markets import CONTINUOUS, is_open, last_close_before, session_on, should_fetch
from market_monitor.scheduler import next_run

ROME = ZoneInfo("Europe/Rome")
NY = ZoneInfo("America/New_York")
IT = MarketHours("IT", timezone=ROME, open=time(9, 0), close=time(17, 30), days=frozenset(range(5)))
US = MarketHours("US", timezone=NY, open=time(9, 30), close=time(16, 0), days=frozenset(range(5)))
CRYPTO = MarketHours("CRYPTO", always_open=True)


def ts(y, mo, d, h, mi, tz=ROME):
    return datetime(y, mo, d, h, mi, tzinfo=tz).timestamp()


@pytest.mark.parametrize(
    "when,expected",
    [
        (ts(2026, 10, 28, 8, 59), False),
        (ts(2026, 10, 28, 9, 0), True),
        (ts(2026, 10, 28, 17, 29), True),
        (ts(2026, 10, 28, 17, 30), False),
        (ts(2026, 10, 31, 12, 0), False),  # Saturday
    ],
)
def test_it_open(when, expected):
    assert is_open(IT, when) is expected


def test_fetch_grace_after_close():
    assert should_fetch(IT, ts(2026, 10, 28, 17, 45))
    assert not should_fetch(IT, ts(2026, 10, 28, 17, 50))


def test_us_hours_in_rome_time():
    # 2026-10-28: Rome is UTC+1 (DST ended 25/10), New York still UTC-4 until 1/11.
    assert not is_open(US, ts(2026, 10, 28, 14, 29))
    assert is_open(US, ts(2026, 10, 28, 14, 30))
    assert is_open(US, ts(2026, 10, 28, 20, 59))
    assert not is_open(US, ts(2026, 10, 28, 21, 0))


def test_crypto_always_open():
    assert is_open(CRYPTO, ts(2026, 10, 31, 3, 0))
    assert session_on(CRYPTO, 0) is CONTINUOUS


def test_session():
    session = session_on(IT, ts(2026, 10, 28, 12, 0))
    assert session.key == "2026-10-28"
    assert session.open_ts == ts(2026, 10, 28, 9, 0)
    assert session.close_ts == ts(2026, 10, 28, 17, 30)
    assert session_on(IT, ts(2026, 11, 1, 12, 0)) is None  # Sunday


def test_last_close_before():
    assert last_close_before(IT, ts(2026, 10, 28, 12, 0)) == ts(2026, 10, 27, 17, 30)
    assert last_close_before(IT, ts(2026, 10, 28, 18, 0)) == ts(2026, 10, 28, 17, 30)
    assert last_close_before(IT, ts(2026, 11, 2, 10, 0)) == ts(2026, 10, 30, 17, 30)  # Monday -> Friday
    assert last_close_before(CRYPTO, 0) is None


@pytest.mark.parametrize(
    "now,interval,expected",
    [
        (ts(2026, 10, 28, 10, 7), 15, ts(2026, 10, 28, 10, 15)),
        (ts(2026, 10, 28, 10, 15), 15, ts(2026, 10, 28, 10, 30)),
        (ts(2026, 10, 28, 23, 50), 15, ts(2026, 10, 29, 0, 0)),
        (ts(2026, 10, 28, 10, 7), 60, ts(2026, 10, 28, 11, 0)),
        (ts(2026, 10, 28, 10, 7), 5, ts(2026, 10, 28, 10, 10)),
    ],
)
def test_next_run_aligned(now, interval, expected):
    assert next_run(now, interval, True, ROME) == expected


def test_next_run_not_aligned():
    assert next_run(1000.5, 15, False, ROME) == 1900.5


def test_next_run_across_dst_change():
    # 25/10/2026 03:00 CEST -> 02:00 CET: the 02:xx hour happens twice.
    before = ts(2026, 10, 25, 1, 50)
    nxt = next_run(before, 15, True, ROME)
    assert nxt - before == 600
