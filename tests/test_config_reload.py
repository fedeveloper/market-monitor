import logging
import os

import pytest

from market_monitor.config import ConfigError, ConfigManager


def touch_later(path, seconds=10):
    """Bump mtime explicitly: filesystem timestamp granularity can hide fast rewrites."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + seconds * 1_000_000_000))


def test_config_before_load_raises(config_dir):
    with pytest.raises(RuntimeError):
        ConfigManager(config_dir.path).config


def test_initial_load_invalid_raises(config_dir):
    (config_dir.path / "config.yaml").unlink()
    with pytest.raises(ConfigError):
        ConfigManager(config_dir.path).load()


def test_no_change_no_reload(config_dir):
    manager = ConfigManager(config_dir.path)
    first = manager.load()
    assert manager.reload_if_changed() is False
    assert manager.config is first


def test_watchlist_change_is_picked_up(config_dir):
    manager = ConfigManager(config_dir.path)
    manager.load()
    data = config_dir.load("watchlist")
    data["assets"] = data["assets"][:2]
    config_dir.save("watchlist", data)
    touch_later(config_dir.path / "watchlist.yaml")
    assert manager.reload_if_changed() is True
    assert [a.symbol for a in manager.config.assets] == ["ENI.MI", "ENEL.MI"]
    assert manager.reload_if_changed() is False


def test_alerts_change_is_picked_up(config_dir):
    manager = ConfigManager(config_dir.path)
    manager.load()
    data = config_dir.load("alerts")
    data["defaults"]["stock"]["fast_move"]["threshold_pct"] = 2.5
    config_dir.save("alerts", data)
    touch_later(config_dir.path / "alerts.yaml")
    assert manager.reload_if_changed() is True
    eni = next(a for a in manager.config.assets if a.symbol == "ENI.MI")
    assert eni.rules.fast_move.threshold_pct == 2.5


def test_invalid_change_keeps_previous_config_and_logs_once(config_dir, caplog):
    manager = ConfigManager(config_dir.path)
    previous = manager.load()
    path = config_dir.path / "watchlist.yaml"
    config_dir.write_text("watchlist", "assets: [")
    touch_later(path)
    with caplog.at_level(logging.ERROR):
        assert manager.reload_if_changed() is False
        assert manager.reload_if_changed() is False
    assert manager.config is previous
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1 and "watchlist.yaml: YAML non valido" in errors[0].getMessage()

    # Fixing the file applies the new config.
    config_dir.write_text("watchlist", "assets:\n  - {symbol: BTCEUR, class: crypto, market: CRYPTO, provider: binance}\n")
    touch_later(path, seconds=20)
    assert manager.reload_if_changed() is True
    assert [a.symbol for a in manager.config.assets] == ["BTCEUR"]
    assert len(previous.assets) == 13


def test_deleted_file_keeps_previous_config(config_dir):
    manager = ConfigManager(config_dir.path)
    previous = manager.load()
    (config_dir.path / "alerts.yaml").unlink()
    assert manager.reload_if_changed() is False
    assert manager.config is previous
