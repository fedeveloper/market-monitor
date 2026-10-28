import pytest

from market_monitor.models import Quote
from market_monitor.storage import ProviderHealth, RuleState, SentAlert, Storage


@pytest.fixture(params=["memory", "file"])
def storage(request, tmp_path):
    path = ":memory:" if request.param == "memory" else tmp_path / "data" / "market.db"
    db = Storage(path)
    yield db
    db.close()


def q(ts, price, symbol="ENI.MI"):
    return Quote(symbol, ts, price, prev_close=14.0, day_open=14.1, volume=100.0, currency="EUR")


def test_save_and_query_samples(storage):
    for i, price in enumerate([14.0, 14.1, 14.2, 14.3]):
        assert storage.save_quote(q(1000 + i * 900, price))
    storage.save_quote(q(1000, 99.0, symbol="ENEL.MI"))
    assert storage.samples("ENI.MI", 1900) == [(1900, 14.1), (2800, 14.2), (3700, 14.3)]
    assert storage.samples("ENI.MI", 1000, 1900) == [(1000, 14.0), (1900, 14.1)]
    assert storage.last_quote("ENI.MI") == q(3700, 14.3)
    assert storage.last_quote("NONE") is None


def test_duplicate_sample_ignored(storage):
    assert storage.save_quote(q(1000, 14.0)) is True
    assert storage.save_quote(q(1000, 15.0)) is False
    assert storage.samples("ENI.MI", 0) == [(1000, 14.0)]


def test_prune(storage):
    for ts in (100, 200, 300):
        storage.save_quote(q(ts, 1.0))
    storage.log_sent_alert(SentAlert(100, "ENI.MI", "day_move", "x"))
    storage.log_sent_message(100)
    assert storage.prune(250) == 2
    assert storage.samples("ENI.MI", 0) == [(300, 1.0)]
    assert storage.sent_alerts(0) == []
    assert storage.sent_message_times(0) == []


def test_rule_state_roundtrip(storage):
    assert storage.rule_states("ENI.MI") == {}
    state = RuleState(last_sent_ts=123, last_level=3.2, session_key="2026-10-28", armed=False)
    storage.set_rule_state("ENI.MI", "day_move:up:3", state)
    storage.set_rule_state("ENI.MI", "fast_move", RuleState())
    assert storage.rule_states("ENI.MI") == {"day_move:up:3": state, "fast_move": RuleState()}
    storage.set_rule_state("ENI.MI", "day_move:up:3", RuleState(armed=True))
    assert storage.rule_states("ENI.MI")["day_move:up:3"] == RuleState()


def test_provider_health_roundtrip(storage):
    assert storage.provider_health("yahoo") == ProviderHealth()
    storage.set_provider_health("yahoo", ProviderHealth(3, "HTTP 429", True))
    assert storage.provider_health("yahoo") == ProviderHealth(3, "HTTP 429", True)
    assert storage.all_provider_health() == {"yahoo": ProviderHealth(3, "HTTP 429", True)}


def test_kv_and_logs(storage):
    assert storage.get("last_digest") is None
    storage.set("last_digest", "2026-10-28")
    assert storage.get("last_digest") == "2026-10-28"
    storage.log_sent_message(10)
    storage.log_sent_message(20)
    assert storage.sent_message_times(15) == [20]
    storage.log_sent_alert(SentAlert(30, "BTCEUR", "level_above", "70.000 €"))
    assert storage.sent_alerts(0) == [SentAlert(30, "BTCEUR", "level_above", "70.000 €")]


def test_nothing_persisted_without_commit(tmp_path):
    path = tmp_path / "market.db"
    db = Storage(path)
    db.save_quote(q(1000, 14.0))
    db.close()  # no commit: the cycle failed
    db = Storage(path)
    assert db.samples("ENI.MI", 0) == []
    db.save_quote(q(1000, 14.0))
    db.commit()
    db.close()
    assert Storage(path).samples("ENI.MI", 0) == [(1000, 14.0)]


def test_file_database_uses_wal(tmp_path):
    db = Storage(tmp_path / "market.db")
    assert db.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    db.close()


def test_schema_is_idempotent(tmp_path):
    path = tmp_path / "market.db"
    Storage(path).close()
    db = Storage(path)
    tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"quotes", "alert_state", "provider_health", "sent_alerts", "sent_messages", "kv"} <= tables
    db.close()


def test_maybe_prune_once_per_day(storage):
    day = 86400
    storage.save_quote(q(0, 1.0))
    storage.save_quote(q(40 * day, 1.0))
    assert storage.maybe_prune("2026-10-28", 40 * day, 30) == 1
    storage.save_quote(q(1, 1.0))
    assert storage.maybe_prune("2026-10-28", 40 * day, 30) is None
    assert storage.maybe_prune("2026-10-29", 41 * day, 30) == 1


def test_recent_sent_alerts_newest_first(storage):
    for ts in (10, 30, 20):
        storage.log_sent_alert(SentAlert(ts, "ENI.MI", "day_move", str(ts)))
    assert [a.ts for a in storage.recent_sent_alerts(2)] == [30, 20]
    assert storage.recent_sent_alerts(0) == []
