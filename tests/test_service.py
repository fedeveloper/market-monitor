from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import Clock, FakeProvider
from market_monitor.config import ConfigManager
from market_monitor.models import Quote
from market_monitor.notifier import DryRunNotifier
from market_monitor.providers import ProviderError
from market_monitor.service import Service, fetch_all

ROME = ZoneInfo("Europe/Rome")


def at(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=ROME).timestamp()


WED_10 = at(2026, 10, 28, 10, 0)
SAT_10 = at(2026, 10, 31, 10, 0)


@pytest.fixture
def small_watchlist(config_dir):
    data = config_dir.load("watchlist")
    data["assets"] = [data["assets"][0], data["assets"][1], data["assets"][-1]]  # ENI, ENEL, BTCEUR
    config_dir.save("watchlist", data)
    return config_dir


def make_service(config_dir, clock, quotes, notifier=None, dry_run=False):
    manager = ConfigManager(config_dir.path)
    manager.load()
    providers = {"yahoo": FakeProvider("yahoo", quotes), "binance": FakeProvider("binance", quotes)}
    from market_monitor.storage import Storage

    service = Service(
        manager,
        notifier or DryRunNotifier(),
        dry_run=dry_run,
        storage=Storage(":memory:"),
        providers_factory=lambda config: providers,
        clock=clock,
        configure_logging=False,
    )
    return service, providers


def quote(symbol, ts, price, prev=None, currency="EUR"):
    return Quote(symbol, int(ts), price, prev_close=prev, currency=currency)


def test_cycle_fetches_saves_and_notifies(small_watchlist):
    clock = Clock(WED_10)
    quotes = {
        "ENI.MI": quote("ENI.MI", WED_10 - 900, 14.52, 14.08),
        "ENEL.MI": ProviderError("yahoo ENEL.MI: HTTP 429"),
        "BTCEUR": quote("BTCEUR", WED_10 - 10, 62310.0, 65728.0),
    }
    notifier = DryRunNotifier()
    service, _ = make_service(small_watchlist, clock, quotes, notifier)
    cycle = service.run_cycle()
    assert [r.asset.symbol for r in cycle.ok] == ["ENI.MI", "BTCEUR"]
    assert [r.asset.symbol for r in cycle.failed] == ["ENEL.MI"]
    assert service.storage.last_quote("ENI.MI").price == 14.52
    text = notifier.sent[0]
    assert "<b>Market Monitor</b> · 10:00" in text
    assert "▲ Eni (ENI.MI)  14,52 €  +3,1% oggi" in text
    assert "▼ Bitcoin (BTCEUR)  62.310 €  −5,2% in 24 ore" in text
    assert "Non disponibili: ENEL.MI" in text


def test_closed_market_not_fetched(small_watchlist):
    clock = Clock(SAT_10)
    quotes = {"BTCEUR": quote("BTCEUR", SAT_10, 60000.0, 61000.0)}
    service, providers = make_service(small_watchlist, clock, quotes)
    cycle = service.run_cycle()
    assert providers["yahoo"].calls == []
    assert providers["binance"].calls == ["BTCEUR"]
    assert sum(r.skipped for r in cycle.results) == 2


def test_stale_quote_flagged(small_watchlist):
    service, providers = make_service(small_watchlist, Clock(WED_10), {})
    quotes = {
        "ENI.MI": quote("ENI.MI", WED_10 - 46 * 60, 14.5, 14.0),
        "ENEL.MI": quote("ENEL.MI", WED_10 - 45 * 60, 7.0, 7.0),
        "BTCEUR": quote("BTCEUR", WED_10, 1.0, 1.0),
    }
    providers["yahoo"].quotes.update(quotes)
    providers["binance"].quotes.update(quotes)
    results = {r.asset.symbol: r for r in fetch_all(service.config, providers, WED_10)}
    assert results["ENI.MI"].fresh is False
    assert results["ENEL.MI"].fresh is True


def test_unexpected_provider_exception_does_not_stop_cycle(small_watchlist):
    quotes = {
        "ENI.MI": RuntimeError("bug"),
        "ENEL.MI": quote("ENEL.MI", WED_10, 7.0, 7.0),
        "BTCEUR": quote("BTCEUR", WED_10, 1.0, 1.0),
    }
    service, _ = make_service(small_watchlist, Clock(WED_10), quotes)
    cycle = service.run_cycle()
    assert len(cycle.ok) == 2 and cycle.failed[0].error == "RuntimeError: bug"


def test_dry_run_does_not_write_database(small_watchlist):
    quotes = {s: quote(s, WED_10, 1.0, 1.0) for s in ("ENI.MI", "ENEL.MI", "BTCEUR")}
    service, _ = make_service(small_watchlist, Clock(WED_10), quotes, dry_run=True)
    service.run_cycle()
    assert service.storage.last_quote("ENI.MI") is None


def test_cycle_exception_is_contained(small_watchlist):
    quotes = {s: quote(s, WED_10, 1.0, 1.0) for s in ("ENI.MI", "ENEL.MI", "BTCEUR")}
    service, _ = make_service(small_watchlist, Clock(WED_10), quotes)

    def boom(cycle):
        raise ValueError("bug in process")

    service.process = boom
    assert service.run_cycle_safely() is None
    assert service.storage.last_quote("ENI.MI") is None  # rolled back


def test_config_reloaded_between_cycles(small_watchlist):
    import os

    quotes = {s: quote(s, WED_10, 1.0, 1.0) for s in ("ENI.MI", "ENEL.MI", "BTCEUR")}
    service, providers = make_service(small_watchlist, Clock(WED_10), quotes)
    service.run_cycle()
    data = small_watchlist.load("watchlist")
    data["assets"] = data["assets"][:1]
    small_watchlist.save("watchlist", data)
    path = small_watchlist.path / "watchlist.yaml"
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**10))
    cycle = service.run_cycle()
    assert [r.asset.symbol for r in cycle.results] == ["ENI.MI"]


def test_run_forever_waits_for_slot_and_stops(small_watchlist):
    quotes = {s: quote(s, WED_10, 1.0, 1.0) for s in ("ENI.MI", "ENEL.MI", "BTCEUR")}
    clock = Clock(WED_10 - 1)  # one second before the 10:00 slot
    service, _ = make_service(small_watchlist, clock, quotes)
    waits = []

    def fake_wait(timeout):
        waits.append(timeout)
        clock.advance(timeout)
        return len(waits) > 1  # stop before the second cycle

    service.stop_event.wait = fake_wait
    service.run_forever()
    assert waits[0] == pytest.approx(1)
    assert waits[1] == pytest.approx(900)
    assert service.storage.last_quote("ENI.MI") is not None


def test_cli_run_once_dry_run(small_watchlist, monkeypatch, capsys):
    from market_monitor import service as service_module
    from market_monitor.cli import main

    quotes = {s: quote(s, 4102444800, 1.0, 1.0) for s in ("ENI.MI", "ENEL.MI", "BTCEUR")}
    monkeypatch.setattr(
        service_module, "build_providers",
        lambda app: {"yahoo": FakeProvider("yahoo", quotes), "binance": FakeProvider("binance", quotes)},
    )
    code = main(["--config-dir", str(small_watchlist.path), "--env-file", "/nonexistent", "run", "--once", "--dry-run"])
    assert code == 0
    out = capsys.readouterr().out
    assert "[dry-run] messaggio non inviato" in out and "Bitcoin (BTCEUR)" in out
    # The log file of the example config lives next to the config dir.
    assert (small_watchlist.path.parent / "logs" / "market-monitor.log").exists()


def test_cli_run_without_secrets_fails(small_watchlist, capsys, monkeypatch):
    from market_monitor.cli import main

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    code = main(["--config-dir", str(small_watchlist.path), "--env-file", "/nonexistent", "run", "--once"])
    assert code == 1
    assert "TELEGRAM_BOT_TOKEN mancante" in capsys.readouterr().out
