"""Logging setup: stdout plus optional rotating file, with secret masking.

Masking happens in the formatter, so it also covers exception tracebacks
(requests errors embed the full URL, which contains ``bot<TOKEN>``).
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path

MASK = "<TOKEN>"

# Telegram bot tokens look like "123456789:AAH...". Masked even when not
# registered explicitly, as a safety net.
_TOKEN_RE = re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{20,}")
# Anything after "/bot" in a Telegram API URL, up to the next "/".
_BOT_URL_RE = re.compile(r"(/bot)[^/\s]+")

DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
PLAIN_FORMAT = "%(message)s"

_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Make every future log line replace ``value`` with a mask."""
    if value and len(value) >= 4:
        _secrets.add(value)


def mask_secrets(text: str) -> str:
    for secret in sorted(_secrets, key=len, reverse=True):
        text = text.replace(secret, MASK)
    text = _BOT_URL_RE.sub(r"\1" + MASK, text)
    return _TOKEN_RE.sub(MASK, text)


class MaskingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return mask_secrets(super().format(record))


# Handlers installed by setup_logging, so that a second call replaces them.
_installed: list[logging.Handler] = []


def setup_logging(
    level: str = "INFO",
    file: Path | None = None,
    max_bytes: int = 1_000_000,
    backup_count: int = 3,
    fmt: str = DEFAULT_FORMAT,
) -> None:
    """Configure the root logger. Safe to call again (e.g. after a config reload)."""
    root = logging.getLogger()
    for handler in _installed:
        root.removeHandler(handler)
        handler.close()
    _installed.clear()

    formatter = MaskingFormatter(fmt)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if file is not None:
        file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
            )
        )
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
        _installed.append(handler)
    root.setLevel(level.upper())
