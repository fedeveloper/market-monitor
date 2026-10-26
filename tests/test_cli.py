import subprocess
import sys

import pytest

from market_monitor.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "market_monitor" in capsys.readouterr().out


def test_module_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "market_monitor", "--version"], capture_output=True, text=True
    )
    assert result.returncode == 0


FAKE_TOKEN = "123456789:AAH_fake-token-for-tests-0123456789"


def run_check(config_dir, env_text=None):
    env = config_dir.path.parent / ".env"
    if env_text is not None:
        env.write_text(env_text, encoding="utf-8")
    return main(["--config-dir", str(config_dir.path), "--env-file", str(env), "check-config"])


@pytest.fixture(autouse=True)
def _no_real_secrets(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)


def test_check_config_valid(config_dir, capsys):
    assert run_check(config_dir, f"TELEGRAM_BOT_TOKEN={FAKE_TOKEN}\nTELEGRAM_CHAT_ID=42\n") == 0
    out = capsys.readouterr().out
    assert "Configurazione valida." in out
    assert "Asset attivi: 13 su 13 (crypto 1, etf 4, stock 8)" in out
    assert "BTCEUR" in out and "day 5/8/12% 24h" in out
    assert "Telegram: token presente, chat id presente" in out
    assert FAKE_TOKEN not in out and "42" not in out.split("Telegram:")[1]


def test_check_config_without_env_warns_but_passes(config_dir, capsys):
    assert run_check(config_dir) == 0
    out = capsys.readouterr().out
    assert "TELEGRAM_BOT_TOKEN mancante" in out
    assert "Configurazione valida." in out


def test_check_config_shows_overrides(config_dir, capsys):
    data = config_dir.load("watchlist")
    data["assets"][0]["overrides"] = {"fast_move": {"threshold_pct": 1.5}}
    config_dir.save("watchlist", data)
    assert run_check(config_dir) == 0
    assert "fast 1,5%/60m" in capsys.readouterr().out


def test_check_config_invalid(config_dir, capsys):
    data = config_dir.load("alerts")
    data["delivery"]["max_messages_per_hour"] = 0
    config_dir.save("alerts", data)
    assert run_check(config_dir) == 1
    out = capsys.readouterr().out
    assert "Configurazione NON valida" in out
    assert "alerts.yaml: delivery.max_messages_per_hour: deve essere >= 1, trovato 0" in out


def test_check_config_invalid_token_format(config_dir, capsys):
    assert run_check(config_dir, "TELEGRAM_BOT_TOKEN=wrong\nTELEGRAM_CHAT_ID=42\n") == 1
    assert "TELEGRAM_BOT_TOKEN in formato non valido" in capsys.readouterr().out
