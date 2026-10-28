from datetime import time

import pytest

from market_monitor.config import (
    ConfigError,
    Secrets,
    check_secrets,
    load_config,
    load_secrets,
    parse_env_file,
)


def errors_of(config_dir) -> list[str]:
    with pytest.raises(ConfigError) as exc:
        load_config(config_dir.path)
    return exc.value.errors


def asset(config, symbol):
    return next(a for a in config.assets if a.symbol == symbol)


# --- valid configuration -------------------------------------------------


def test_example_files_are_valid(config_dir):
    config = load_config(config_dir.path)
    assert config.warnings == ()
    assert config.app.interval_minutes == 15
    assert config.app.align_to_clock is True
    assert str(config.app.timezone) == "Europe/Rome"
    assert config.app.markets["IT"].open == time(9, 0)
    assert config.app.markets["IT"].days == frozenset(range(5))
    assert config.app.markets["CRYPTO"].always_open
    assert set(config.app.providers) == {"yahoo", "binance"}
    assert config.app.telegram.max_message_chars == 4000
    assert config.alerts.delivery.quiet_hours.start == time(23, 0)
    assert config.alerts.delivery.urgent_pct == {"stock": 8, "etf": 6, "crypto": 10}
    assert config.alerts.health.stale_data_minutes == 45
    assert len(config.assets) == 13
    assert {a.asset_class for a in config.assets} == {"stock", "etf", "crypto"}


def test_relative_paths_resolved_against_project_root(config_dir):
    config = load_config(config_dir.path)
    root = config_dir.path.parent
    assert config.app.storage.path == root / "data" / "market.db"
    assert config.app.logging.file == root / "logs" / "market-monitor.log"


def test_class_defaults_applied(config_dir):
    config = load_config(config_dir.path)
    eni, swda, btc = asset(config, "ENI.MI"), asset(config, "SWDA.MI"), asset(config, "BTCEUR")
    assert eni.rules.fast_move.threshold_pct == 2.0
    assert eni.rules.day_move.steps_pct == (3, 5, 8)
    assert eni.rules.day_move.reference == "prev_close"
    assert swda.rules.fast_move.threshold_pct == 1.2
    assert swda.rules.open_gap.threshold_pct == 1.0
    assert btc.rules.day_move.reference == "rolling_24h"
    assert btc.rules.open_gap.enabled is False
    assert eni.overridden_rules == ()


def test_asset_defaults(config_dir):
    data = config_dir.load("watchlist")
    data["assets"].append({"symbol": "AAPL", "class": "stock", "market": "US", "provider": "yahoo"})
    config_dir.save("watchlist", data)
    aapl = asset(load_config(config_dir.path), "AAPL")
    assert aapl.name == "AAPL"
    assert aapl.enabled is True
    assert aapl.levels.above == () and aapl.levels.below == ()


def test_disabled_asset_kept_but_not_enabled(config_dir):
    data = config_dir.load("watchlist")
    data["assets"][0]["enabled"] = False
    config_dir.save("watchlist", data)
    config = load_config(config_dir.path)
    assert len(config.assets) == 13
    assert len(config.enabled_assets) == 12
    assert "ENI.MI" not in {a.symbol for a in config.enabled_assets}


# --- per-asset overrides -------------------------------------------------


def test_override_single_field_keeps_other_defaults(config_dir):
    data = config_dir.load("watchlist")
    data["assets"][0].update(
        {"levels": {"above": [16.0], "below": [13.5]}, "overrides": {"fast_move": {"threshold_pct": 1.5}}}
    )
    config_dir.save("watchlist", data)
    config = load_config(config_dir.path)
    eni = asset(config, "ENI.MI")
    assert eni.rules.fast_move.threshold_pct == 1.5
    assert eni.rules.fast_move.window_minutes == 60
    assert eni.rules.fast_move.step_pct == 1.0
    assert eni.rules.day_move.steps_pct == (3, 5, 8)
    assert eni.overridden_rules == ("fast_move",)
    assert eni.levels.above == (16.0,) and eni.levels.below == (13.5,)
    # Other stocks keep the class default.
    assert asset(config, "ENEL.MI").rules.fast_move.threshold_pct == 2.0


def test_override_multiple_rules(config_dir):
    data = config_dir.load("watchlist")
    data["assets"][-1]["overrides"] = {
        "day_move": {"steps_pct": [4, 7]},
        "fast_move": {"enabled": False},
    }
    config_dir.save("watchlist", data)
    btc = asset(load_config(config_dir.path), "BTCEUR")
    assert btc.rules.day_move.steps_pct == (4, 7)
    assert btc.rules.day_move.reference == "rolling_24h"
    assert btc.rules.fast_move.enabled is False
    assert btc.rules.fast_move.threshold_pct == 3.0


def test_override_does_not_leak_into_class_defaults(config_dir):
    data = config_dir.load("watchlist")
    data["assets"][0]["overrides"] = {"open_gap": {"threshold_pct": 5}}
    config_dir.save("watchlist", data)
    config = load_config(config_dir.path)
    assert config.alerts.defaults["stock"].open_gap.threshold_pct == 2.0
    assert asset(config, "ENI.MI").rules.open_gap.threshold_pct == 5


def test_override_with_invalid_value(config_dir):
    data = config_dir.load("watchlist")
    data["assets"][0]["overrides"] = {"fast_move": {"threshold_pct": -1}}
    config_dir.save("watchlist", data)
    errors = errors_of(config_dir)
    assert errors == [
        "watchlist.yaml: assets[0] (ENI.MI).overrides.fast_move.threshold_pct: "
        "deve essere maggiore di 0, trovato -1"
    ]


def test_override_unknown_rule_and_field(config_dir):
    data = config_dir.load("watchlist")
    data["assets"][0]["overrides"] = {"fast_mov": {}, "day_move": {"step_pct": [1]}}
    config_dir.save("watchlist", data)
    errors = errors_of(config_dir)
    assert any("overrides.fast_mov: chiave sconosciuta" in e for e in errors)
    assert any("overrides.day_move.step_pct: chiave sconosciuta" in e for e in errors)


def test_enabling_open_gap_on_crypto_is_rejected(config_dir):
    data = config_dir.load("watchlist")
    data["assets"][-1]["overrides"] = {"open_gap": {"enabled": True, "threshold_pct": 2, "max_delay_minutes": 30}}
    config_dir.save("watchlist", data)
    errors = errors_of(config_dir)
    assert len(errors) == 1 and "open_gap non si applica alle crypto" in errors[0]


# --- invalid configuration: clear messages --------------------------------


def test_missing_file(config_dir):
    (config_dir.path / "alerts.yaml").unlink()
    errors = errors_of(config_dir)
    assert len(errors) == 1
    assert errors[0].startswith("alerts.yaml: file mancante")
    assert "alerts.example.yaml" in errors[0]


def test_yaml_syntax_error_reports_line(config_dir):
    config_dir.write_text("watchlist", "assets:\n  - {symbol: ENI.MI, name: Eni\n")
    errors = errors_of(config_dir)
    assert len(errors) == 1
    assert errors[0].startswith("watchlist.yaml: YAML non valido (riga")


def test_empty_file(config_dir):
    config_dir.write_text("config", "# nothing\n")
    assert errors_of(config_dir) == ["config.yaml: file vuoto"]


def test_errors_from_all_files_are_collected(config_dir):
    app = config_dir.load("config")
    app["interval_minutes"] = 0
    app["timezone"] = "Europe/Roma"
    config_dir.save("config", app)
    alerts = config_dir.load("alerts")
    alerts["defaults"]["etf"]["fast_move"]["threshold_pct"] = "1,2"
    alerts["delivery"]["quiet_hours"]["start"] = "25:00"
    config_dir.save("alerts", alerts)
    watchlist = config_dir.load("watchlist")
    watchlist["assets"][0]["class"] = "bond"
    config_dir.save("watchlist", watchlist)

    errors = errors_of(config_dir)
    assert "config.yaml: interval_minutes: deve essere tra 1 e 1440, trovato 0" in errors
    assert "config.yaml: timezone: fuso orario sconosciuto 'Europe/Roma' (es. Europe/Rome)" in errors
    assert "alerts.yaml: defaults.etf.fast_move.threshold_pct: deve essere un numero, trovato '1,2'" in errors
    assert "alerts.yaml: delivery.quiet_hours.start: orario non valido '25:00'" in errors
    assert (
        "watchlist.yaml: assets[0] (ENI.MI).class: valore 'bond' non ammesso, "
        "valori possibili: stock, etf, crypto"
    ) in errors


def test_unquoted_time_hint(config_dir):
    text = (config_dir.path / "config.yaml").read_text().replace('close: "17:30"', "close: 17:30")
    config_dir.write_text("config", text)
    errors = errors_of(config_dir)
    assert errors == ['config.yaml: markets.IT.close: orario da scrivere tra virgolette, es. "09:30"']


def test_typo_in_key_is_reported(config_dir):
    alerts = config_dir.load("alerts")
    alerts["defaults"]["stock"]["fast_move"]["treshold_pct"] = 1.5
    config_dir.save("alerts", alerts)
    errors = errors_of(config_dir)
    assert errors[0].startswith("alerts.yaml: defaults.stock.fast_move.treshold_pct: chiave sconosciuta")


def test_missing_required_field(config_dir):
    alerts = config_dir.load("alerts")
    del alerts["defaults"]["stock"]["fast_move"]["window_minutes"]
    del alerts["health"]
    config_dir.save("alerts", alerts)
    errors = errors_of(config_dir)
    assert "alerts.yaml: defaults.stock.fast_move: regola attiva ma mancano i campi: window_minutes" in errors
    assert "alerts.yaml: health: sezione obbligatoria mancante" in errors


def test_steps_must_increase(config_dir):
    alerts = config_dir.load("alerts")
    alerts["defaults"]["stock"]["day_move"]["steps_pct"] = [3, 8, 5]
    config_dir.save("alerts", alerts)
    assert errors_of(config_dir) == [
        "alerts.yaml: defaults.stock.day_move.steps_pct: i gradini devono essere crescenti, trovato [3, 8, 5]"
    ]


def test_unknown_market_and_provider(config_dir):
    watchlist = config_dir.load("watchlist")
    watchlist["assets"][0]["market"] = "DE"
    watchlist["assets"][1]["provider"] = "stooq"
    config_dir.save("watchlist", watchlist)
    errors = errors_of(config_dir)
    assert (
        "watchlist.yaml: assets[0] (ENI.MI).market: mercato 'DE' non definito in config.yaml "
        "(disponibili: IT, US, CRYPTO)"
    ) in errors
    assert any(e.startswith("watchlist.yaml: assets[1] (ENEL.MI).provider: provider 'stooq'") for e in errors)


def test_duplicate_symbol(config_dir):
    watchlist = config_dir.load("watchlist")
    watchlist["assets"].append(dict(watchlist["assets"][0]))
    config_dir.save("watchlist", watchlist)
    errors = errors_of(config_dir)
    assert errors == ["watchlist.yaml: assets[13] (ENI.MI): simbolo duplicato, già definito in assets[0]"]


def test_missing_symbol(config_dir):
    watchlist = config_dir.load("watchlist")
    del watchlist["assets"][2]["symbol"]
    config_dir.save("watchlist", watchlist)
    assert "watchlist.yaml: assets[2].symbol: campo obbligatorio mancante" in errors_of(config_dir)


def test_market_open_after_close(config_dir):
    app = config_dir.load("config")
    app["markets"]["US"]["open"] = "17:00"
    config_dir.save("config", app)
    assert errors_of(config_dir) == [
        "config.yaml: markets.US: l'orario di apertura deve precedere quello di chiusura"
    ]


def test_interval_must_divide_day_when_aligned(config_dir):
    app = config_dir.load("config")
    app["interval_minutes"] = 7
    config_dir.save("config", app)
    errors = errors_of(config_dir)
    assert len(errors) == 1 and errors[0].startswith("config.yaml: interval_minutes: con align_to_clock")
    app["align_to_clock"] = False
    config_dir.save("config", app)
    config = load_config(config_dir.path)
    # 60-minute windows are not a multiple of 7: warning, not error.
    assert any("non è un multiplo dell'intervallo" in w for w in config.warnings)


def test_invalid_levels(config_dir):
    watchlist = config_dir.load("watchlist")
    watchlist["assets"][0]["levels"] = {"above": [16, "x"]}
    config_dir.save("watchlist", watchlist)
    assert errors_of(config_dir) == [
        "watchlist.yaml: assets[0] (ENI.MI).levels.above: deve essere un numero, trovato 'x'"
    ]


def test_telegram_message_limit(config_dir):
    app = config_dir.load("config")
    app["telegram"]["max_message_chars"] = 5000
    config_dir.save("config", app)
    assert errors_of(config_dir) == [
        "config.yaml: telegram.max_message_chars: deve essere tra 100 e 4096, trovato 5000"
    ]


def test_bool_is_not_a_number(config_dir):
    alerts = config_dir.load("alerts")
    alerts["delivery"]["cooldown_minutes"] = True
    config_dir.save("alerts", alerts)
    assert errors_of(config_dir) == [
        "alerts.yaml: delivery.cooldown_minutes: deve essere un numero intero, trovato True"
    ]


# --- .env and secrets ------------------------------------------------------

FAKE_TOKEN = "123456789:AAH_fake-token-for-tests-0123456789"


def test_parse_env_file(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n\n"
        f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\n"
        'export TELEGRAM_CHAT_ID="42"\n'
        "OTHER='a b' \n"
        "WITH_COMMENT=value # trailing\n"
        "not a valid line\n",
        encoding="utf-8",
    )
    assert parse_env_file(env) == {
        "TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
        "TELEGRAM_CHAT_ID": "42",
        "OTHER": "a b",
        "WITH_COMMENT": "value",
    }


def test_missing_env_file(tmp_path):
    assert parse_env_file(tmp_path / ".env") == {}
    secrets = load_secrets(tmp_path / ".env", environ={})
    assert secrets == Secrets()
    errors, warnings = check_secrets(secrets)
    assert errors == [] and len(warnings) == 2


def test_environment_takes_precedence(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\nTELEGRAM_CHAT_ID=1\n", encoding="utf-8")
    secrets = load_secrets(env, environ={"TELEGRAM_CHAT_ID": "2"})
    assert secrets.telegram_bot_token == FAKE_TOKEN
    assert secrets.telegram_chat_id == "2"
    assert check_secrets(secrets) == ([], [])


def test_secrets_repr_hides_values():
    secrets = Secrets(telegram_bot_token=FAKE_TOKEN, telegram_chat_id="12345")
    assert FAKE_TOKEN not in repr(secrets) and "12345" not in repr(secrets)
    assert FAKE_TOKEN not in str(secrets)


def test_invalid_secret_format_without_leaking_value():
    errors, _ = check_secrets(Secrets(telegram_bot_token="not-a-token-xyz", telegram_chat_id="chat-qwerty"))
    assert len(errors) == 2
    assert not any("not-a-token-xyz" in e or "chat-qwerty" in e for e in errors)


def test_unimplemented_provider_is_a_warning(config_dir):
    app = config_dir.load("config")
    app["providers"]["coingecko"] = {"base_url": "https://api.coingecko.com", "timeout_seconds": 10, "retries": 1}
    config_dir.save("config", app)
    config = load_config(config_dir.path)
    assert config.warnings == ("config.yaml: providers.coingecko: provider non implementato, verrà ignorato",)


def test_rearm_pct_from_example_and_override(config_dir):
    data = config_dir.load("watchlist")
    data["assets"][0]["overrides"] = {"day_move": {"rearm_pct": 0.5}}
    config_dir.save("watchlist", data)
    config = load_config(config_dir.path)
    assert asset(config, "ENI.MI").rules.day_move.rearm_pct == 0.5
    assert asset(config, "ENEL.MI").rules.day_move.rearm_pct == 1.0
    assert asset(config, "SWDA.MI").rules.day_move.rearm_pct == 0.6
    assert asset(config, "BTCEUR").rules.day_move.rearm_pct == 1.5


def test_rearm_pct_defaults_for_older_alerts_file(config_dir):
    alerts = config_dir.load("alerts")
    for rules in alerts["defaults"].values():
        rules["day_move"].pop("rearm_pct", None)
    config_dir.save("alerts", alerts)
    config = load_config(config_dir.path)
    assert asset(config, "ENI.MI").rules.day_move.rearm_pct == 1.0
    assert asset(config, "BTCEUR").rules.day_move.rearm_pct == 1.5
