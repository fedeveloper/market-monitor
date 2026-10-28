import logging

import pytest
import requests

from conftest import FakeResponse, FakeSession
from market_monitor.config import TelegramConfig
from market_monitor.logging_setup import register_secret, setup_logging
from market_monitor.notifier import DryRunNotifier, TelegramNotifier, split_message

TOKEN = "123456789:AAH_fake-token-for-tests-0123456789"
CFG = TelegramConfig(parse_mode="HTML", max_message_chars=100, timeout_seconds=5, retries=2)


def notifier(*responses, sleeps=None):
    session = FakeSession(*responses)
    return TelegramNotifier(TOKEN, "42", CFG, session=session, sleep=(sleeps if sleeps is not None else []).append), session


def test_split_short_message_untouched():
    assert split_message("ciao", 100) == ["ciao"]


def test_split_on_lines():
    lines = [f"riga {i:02d} " + "x" * 20 for i in range(10)]
    parts = split_message("\n".join(lines), 100)
    assert all(len(p) <= 100 for p in parts)
    assert "\n".join(parts).split("\n") == lines
    assert len(parts) == 4


def test_split_long_single_line():
    parts = split_message("a" * 250, 100)
    assert parts == ["a" * 100, "a" * 100, "a" * 50]


def test_split_exact_limit():
    text = "x" * 100
    assert split_message(text, 100) == [text]


def test_send_payload():
    n, session = notifier(FakeResponse(200, {"ok": True}))
    assert n.send("<b>Eni</b> &amp;") is True
    method, url, kwargs = session.calls[0]
    assert method == "POST" and url.endswith("/sendMessage")
    assert kwargs["json"] == {
        "chat_id": "42",
        "text": "<b>Eni</b> &amp;",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    assert kwargs["timeout"] == 5


def test_long_message_sent_in_parts():
    text = "\n".join("y" * 60 for _ in range(3))
    n, session = notifier(*[FakeResponse(200, {"ok": True})] * 3)
    assert n.send(text) is True
    assert len(session.calls) == 3


def test_4xx_not_retried(caplog):
    n, session = notifier(FakeResponse(400, {"ok": False, "description": "Bad Request: can't parse entities"}))
    with caplog.at_level(logging.ERROR):
        assert n.send("<b>") is False
    assert len(session.calls) == 1
    assert "can't parse entities" in caplog.text


def test_5xx_retried_then_ok():
    sleeps = []
    n, session = notifier(FakeResponse(502, None), FakeResponse(200, {"ok": True}), sleeps=sleeps)
    assert n.send("x") is True
    assert sleeps == [1]


def test_429_uses_retry_after():
    sleeps = []
    n, _ = notifier(
        FakeResponse(429, {"ok": False, "description": "Too Many Requests", "parameters": {"retry_after": 5}}),
        FakeResponse(200, {"ok": True}),
        sleeps=sleeps,
    )
    assert n.send("x") is True
    assert sleeps == [5]


def test_network_errors_exhaust_retries():
    n, session = notifier(*[requests.ConnectionError(f"https://api.telegram.org/bot{TOKEN}/sendMessage")] * 3)
    assert n.send("x") is False
    assert len(session.calls) == 3


def test_token_never_logged(tmp_path, capsys):
    log_file = tmp_path / "mm.log"
    setup_logging(level="DEBUG", file=log_file)
    register_secret(TOKEN)
    n, _ = notifier(
        requests.ConnectionError(f"Max retries exceeded with url: /bot{TOKEN}/sendMessage"),
        FakeResponse(500, None),
        FakeResponse(401, {"ok": False, "description": "Unauthorized"}),
    )
    assert n.send("x") is False
    logging.getLogger("x").error("url was https://api.telegram.org/bot%s/sendMessage", TOKEN)
    content = log_file.read_text() + capsys.readouterr().out
    assert TOKEN not in content
    assert "Unauthorized" in content
    setup_logging()


def test_dry_run_notifier(caplog):
    n = DryRunNotifier()
    with caplog.at_level(logging.INFO):
        assert n.send("ciao") is True
    assert n.sent == ["ciao"] and "ciao" in caplog.text
