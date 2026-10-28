"""Telegram notifications via Bot API ``sendMessage`` only (never getUpdates: another program may poll the same bot)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol

import requests

from market_monitor.config import TelegramConfig

log = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
MAX_RETRY_AFTER_SECONDS = 30.0


class Notifier(Protocol):
    def send(self, text: str) -> bool:
        """Send a message (split if needed). True if every part was delivered."""
        ...


def split_message(text: str, limit: int) -> list[str]:
    """Split on line boundaries so that no part exceeds ``limit`` characters.

    Messages keep HTML tags within a single line, so a split never breaks a tag.
    A single line longer than the limit is cut hard.
    """
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return [p for p in parts if p.strip()]


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        config: TelegramConfig,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._url = f"{API_BASE}/bot{token}/sendMessage"
        self.chat_id = chat_id
        self.config = config
        self.session = session or requests.Session()
        self._sleep = sleep

    def send(self, text: str) -> bool:
        parts = split_message(text, self.config.max_message_chars)
        ok = True
        for part in parts:
            ok = self._send_part(part) and ok
        return ok

    def _send_part(self, text: str) -> bool:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": self.config.parse_mode,
            "disable_web_page_preview": True,
        }
        attempts = self.config.retries + 1
        for attempt in range(attempts):
            last = attempt == attempts - 1
            try:
                response = self.session.post(self._url, json=payload, timeout=self.config.timeout_seconds)
            except (requests.ConnectionError, requests.Timeout) as exc:
                # The exception text may contain the URL: log only its type.
                if last:
                    log.error("telegram: network error (%s), message dropped", type(exc).__name__)
                    return False
                log.warning("telegram: network error (%s), retrying", type(exc).__name__)
                self._sleep(2**attempt)
                continue

            if response.status_code == 200:
                return True
            description, retry_after = _error_details(response)
            retryable = response.status_code == 429 or response.status_code >= 500
            if retryable and not last:
                wait = max(2**attempt, min(retry_after, MAX_RETRY_AFTER_SECONDS))
                log.warning("telegram: HTTP %d (%s), retry in %.0fs", response.status_code, description, wait)
                self._sleep(wait)
                continue
            log.error("telegram: HTTP %d (%s), message dropped", response.status_code, description)
            return False
        return False


def _error_details(response) -> tuple[str, float]:
    try:
        data = response.json()
    except ValueError:
        return "no JSON body", 0.0
    if not isinstance(data, dict):
        return "unexpected body", 0.0
    retry_after = (data.get("parameters") or {}).get("retry_after") or 0
    return str(data.get("description") or "no description"), float(retry_after)


class DryRunNotifier:
    """Logs messages instead of sending them (``--dry-run``)."""

    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str) -> bool:
        self.sent.append(text)
        log.info("[dry-run] messaggio non inviato:\n%s", text)
        return True
