import logging

import pytest

from market_monitor import logging_setup
from market_monitor.logging_setup import MASK, mask_secrets, register_secret, setup_logging

TOKEN = "123456789:AAH_fake-token-for-tests-0123456789"


@pytest.fixture(autouse=True)
def _reset_logging():
    yield
    logging_setup._secrets.clear()
    setup_logging()


def test_mask_registered_secret():
    register_secret("supersecretvalue")
    assert mask_secrets("value=supersecretvalue!") == f"value={MASK}!"


def test_mask_token_pattern_without_registration():
    assert TOKEN not in mask_secrets(f"token is {TOKEN}")


def test_mask_bot_url():
    url = "https://api.telegram.org/botWHATEVER/sendMessage"
    assert mask_secrets(url) == f"https://api.telegram.org/bot{MASK}/sendMessage"


def test_rotating_file_masks_messages_and_tracebacks(tmp_path):
    log_file = tmp_path / "logs" / "mm.log"
    setup_logging(level="DEBUG", file=log_file, max_bytes=10_000, backup_count=2)
    register_secret(TOKEN)
    log = logging.getLogger("test")
    log.info("calling https://api.telegram.org/bot%s/sendMessage", TOKEN)
    try:
        raise RuntimeError(f"connection failed for /bot{TOKEN}/sendMessage")
    except RuntimeError:
        log.exception("send failed")
    content = log_file.read_text(encoding="utf-8")
    assert TOKEN not in content
    assert "send failed" in content and "RuntimeError" in content
    assert MASK in content


def test_file_rotation(tmp_path):
    log_file = tmp_path / "mm.log"
    setup_logging(file=log_file, max_bytes=500, backup_count=2)
    log = logging.getLogger("test")
    for i in range(100):
        log.info("line %03d %s", i, "x" * 40)
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["mm.log", "mm.log.1", "mm.log.2"]


def test_setup_logging_is_idempotent(tmp_path):
    setup_logging(file=tmp_path / "a.log")
    setup_logging(file=tmp_path / "b.log")
    root = logging.getLogger()
    assert len(logging_setup._installed) == 2
    assert all(h in root.handlers for h in logging_setup._installed)
