from datetime import datetime

import pytest
import yaml

from market_monitor.config import load_config
from market_monitor.web.store import ConfigStore, ConflictError, UnknownFileError


@pytest.fixture
def store(config_dir):
    return ConfigStore(config_dir.path)


def test_read(store):
    data, version, error = store.read("watchlist")
    assert error is None and version
    assert data["assets"][0]["symbol"] == "ENI.MI"


def test_unknown_file_rejected(store):
    with pytest.raises(UnknownFileError):
        store.read("../.env")
    with pytest.raises(UnknownFileError):
        store.path("secrets")


def test_save_valid_change_with_backup(store, config_dir):
    data, version, _ = store.read("watchlist")
    original = (config_dir.path / "watchlist.yaml").read_text(encoding="utf-8")
    data["assets"].append({"symbol": "AAPL", "name": "Apple", "class": "stock", "market": "US", "provider": "yahoo"})
    result = store.save("watchlist", data, version, now=datetime(2026, 10, 28, 10, 0))
    assert result.ok and result.errors == [] and result.version != version
    saved = (config_dir.path / "watchlist.yaml").read_text(encoding="utf-8")
    assert saved.startswith("# Salvato dall'interfaccia web il 28/10/2026 10:00.")
    assert "config/watchlist.example.yaml" in saved
    assert (config_dir.path / "watchlist.yaml.bak").read_text(encoding="utf-8") == original
    assert [a.symbol for a in load_config(config_dir.path).assets][-1] == "AAPL"


def test_invalid_change_is_not_saved(store, config_dir):
    data, version, _ = store.read("alerts")
    before = (config_dir.path / "alerts.yaml").read_text(encoding="utf-8")
    data["delivery"]["quiet_hours"]["start"] = "25:00"
    result = store.save("alerts", data, version)
    assert not result.ok
    assert result.errors == ["alerts.yaml: delivery.quiet_hours.start: orario non valido '25:00'"]
    assert (config_dir.path / "alerts.yaml").read_text(encoding="utf-8") == before
    assert not (config_dir.path / "alerts.yaml.bak").exists()


def test_cross_file_validation(store):
    data, version, _ = store.read("config")
    del data["markets"]["US"]
    data["markets"]["IT"]["open"] = "09:00"
    result = store.save("config", data, version)
    # The watchlist does not use US, so removing it is fine...
    assert result.ok
    wl, wl_version, _ = store.read("watchlist")
    wl["assets"][0]["market"] = "US"
    result = store.save("watchlist", wl, wl_version)
    # ...but now an asset on US is rejected.
    assert not result.ok and "mercato 'US' non definito" in result.errors[0]


def test_conflict_when_file_changed_on_disk(store, config_dir):
    data, version, _ = store.read("watchlist")
    path = config_dir.path / "watchlist.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# edited by hand\n", encoding="utf-8")
    with pytest.raises(ConflictError):
        store.save("watchlist", data, version)


def test_times_round_trip_as_strings(store, config_dir):
    data, version, _ = store.read("config")
    store.save("config", data, version)
    raw = yaml.safe_load((config_dir.path / "config.yaml").read_text(encoding="utf-8"))
    assert raw["markets"]["IT"]["close"] == "17:30"
    assert raw["markets"]["IT"]["open"] == "09:00"
    assert load_config(config_dir.path).app.markets["IT"].close.hour == 17


def test_warnings_returned(store):
    data, version, _ = store.read("config")
    data["align_to_clock"] = False
    data["interval_minutes"] = 7
    result = store.save("config", data, version)
    assert result.ok and any("non è un multiplo" in w for w in result.warnings)
