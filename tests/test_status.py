import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import Clock, FakeProvider
from market_monitor import status as status_module
from market_monitor.config import ConfigManager
from market_monitor.models import Quote
from market_monitor.notifier import DryRunNotifier
from market_monitor.providers import ProviderError
from market_monitor.service import Service
from market_monitor.status import SCHEMA_VERSION, write_atomic
from market_monitor.storage import SentAlert, Storage

ROME = ZoneInfo("Europe/Rome")
WED_10 = datetime(2026, 10, 28, 10, 0, tzinfo=ROME).timestamp()
FAKE_TOKEN = "123456789:AAH_fake-token-for-tests-0123456789"


@pytest.fixture
def status_config(config_dir):
    app = config_dir.load("config")
    app["status_file"] = {"enabled": True, "path": "data/status.json", "recent_alerts": 2}
    config_dir.save("config", app)
    data = config_dir.load("watchlist")
    eni = dict(data["assets"][0], levels={"above": [16.0]})
    data["assets"] = [eni, data["assets"][1], data["assets"][-1]]  # ENI, ENEL, BTCEUR
    config_dir.save("watchlist", data)
    return config_dir


def make_service(config_dir, quotes, dry_run=False):
    manager = ConfigManager(config_dir.path)
    manager.load()
    providers = {"yahoo": FakeProvider("yahoo", quotes), "binance": FakeProvider("binance", quotes)}
    return Service(
        manager, DryRunNotifier(), dry_run=dry_run, storage=Storage(":memory:"),
        providers_factory=lambda config: providers, clock=Clock(WED_10), configure_logging=False,
    )


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_status_schema(status_config):
    quotes = {
        "ENI.MI": Quote("ENI.MI", int(WED_10 - 300), 14.52, prev_close=14.08, currency="EUR"),
        "ENEL.MI": Quote("ENEL.MI", int(WED_10 - 300), 7.0, prev_close=7.1, currency="EUR"),
        "BTCEUR": Quote("BTCEUR", int(WED_10), 62310.0, prev_close=65728.0, currency="EUR"),
    }
    service = make_service(status_config, quotes)
    for ts, rule in ((WED_10 - 7200, "day_move"), (WED_10 - 3600, "level_above"), (WED_10 - 60, "fast_move")):
        service.storage.log_sent_alert(SentAlert(int(ts), "ENI.MI", rule, f"detail {rule}"))
    service.run_cycle()
    path = status_config.path.parent / "data" / "status.json"
    status = read(path)
    assert status["schema_version"] == SCHEMA_VERSION == 1
    assert status["generated_at"] == "2026-10-28T10:00:00+01:00"
    assert status["interval_minutes"] == 15
    eni = status["assets"][0]
    assert eni == {
        "symbol": "ENI.MI", "name": "Eni", "class": "stock", "market": "IT", "market_open": True,
        "price": 14.52, "currency": "EUR", "quote_time": "2026-10-28T09:55:00+01:00",
        "change_day_pct": 3.12, "change_reference": "prev_close", "levels": {"above": [16.0], "below": []},
    }
    btc = status["assets"][2]
    assert btc["change_reference"] == "rolling_24h" and btc["market_open"] is True
    assert [a["rule"] for a in status["recent_alerts"]] == ["fast_move", "level_above"]  # newest first, max 2
    assert [a["rule"] for a in status["levels_reached_today"]] == ["level_above"]
    assert status["providers"] == {
        "binance": {"ok": True, "consecutive_failures": 0},
        "yahoo": {"ok": True, "consecutive_failures": 0},
    }
    assert status["last_digest"] is None


def test_partial_provider_failure(status_config):
    quotes = {
        "ENI.MI": ProviderError("HTTP 429"),
        "ENEL.MI": ProviderError("HTTP 429"),
        "BTCEUR": Quote("BTCEUR", int(WED_10), 62310.0, prev_close=65728.0, currency="EUR"),
    }
    service = make_service(status_config, quotes)
    service.storage.save_quote(Quote("ENI.MI", int(WED_10 - 86400), 14.0, prev_close=13.9, currency="EUR"))
    service.run_cycle()
    status = read(status_config.path.parent / "data" / "status.json")
    by_symbol = {a["symbol"]: a for a in status["assets"]}
    assert by_symbol["ENI.MI"]["price"] == 14.0  # last stored value, with its (old) time
    assert by_symbol["ENI.MI"]["quote_time"] == "2026-10-27T10:00:00+01:00"
    assert by_symbol["ENEL.MI"]["price"] is None
    assert by_symbol["BTCEUR"]["price"] == 62310.0
    assert status["providers"]["yahoo"] == {"ok": False, "consecutive_failures": 1}


def test_no_secrets_in_status(status_config, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
    quotes = {s: Quote(s, int(WED_10), 1.0, prev_close=1.0, currency="EUR") for s in ("ENI.MI", "ENEL.MI", "BTCEUR")}
    service = make_service(status_config, quotes)
    service.run_cycle()
    text = (status_config.path.parent / "data" / "status.json").read_text(encoding="utf-8")
    assert FAKE_TOKEN not in text and "987654321" not in text
    assert "token" not in text.lower() and "chat" not in text.lower()


def test_disabled_or_dry_run_writes_nothing(config_dir, status_config):
    quotes = {s: Quote(s, int(WED_10), 1.0, prev_close=1.0, currency="EUR") for s in ("ENI.MI", "ENEL.MI", "BTCEUR")}
    make_service(status_config, quotes, dry_run=True).run_cycle()
    assert not (status_config.path.parent / "data" / "status.json").exists()
    app = status_config.load("config")
    app["status_file"]["enabled"] = False
    status_config.save("config", app)
    make_service(status_config, quotes).run_cycle()
    assert not (status_config.path.parent / "data" / "status.json").exists()


def test_atomic_write_keeps_previous_file_on_error(tmp_path, monkeypatch):
    path = tmp_path / "shm" / "status.json"
    write_atomic(path, {"schema_version": 1, "n": 1})

    def broken_dump(obj, fp, **kwargs):
        fp.write('{"schema_version": 1, "n": ')  # half written
        raise OSError("disk full")

    monkeypatch.setattr(status_module.json, "dump", broken_dump)
    with pytest.raises(OSError):
        write_atomic(path, {"schema_version": 1, "n": 2})
    assert read(path) == {"schema_version": 1, "n": 1}
    assert [p.name for p in path.parent.iterdir()] == ["status.json"]  # no temporary file left


def test_status_write_error_does_not_break_cycle(status_config, monkeypatch, caplog):
    quotes = {s: Quote(s, int(WED_10), 1.0, prev_close=1.0, currency="EUR") for s in ("ENI.MI", "ENEL.MI", "BTCEUR")}
    service = make_service(status_config, quotes)

    def fail(path, content):
        raise PermissionError("read-only")

    monkeypatch.setattr("market_monitor.service.write_atomic", fail)
    cycle = service.run_cycle()
    assert len(cycle.ok) == 3
    assert "could not write the status file" in caplog.text
    assert service.storage.last_quote("ENI.MI") is not None  # committed anyway


def test_status_print(status_config, capsys):
    from market_monitor.cli import main

    args = ["--config-dir", str(status_config.path), "status", "--print"]
    assert main(args) == 1
    assert "non esiste ancora" in capsys.readouterr().out
    write_atomic(status_config.path.parent / "data" / "status.json", {"schema_version": 1})
    assert main(args) == 0
    assert '"schema_version": 1' in capsys.readouterr().out


def test_status_print_disabled(config_dir, capsys):
    from market_monitor.cli import main

    assert main(["--config-dir", str(config_dir.path), "status", "--print"]) == 1
    assert "File di stato disattivato" in capsys.readouterr().out
