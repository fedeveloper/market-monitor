"""Provider interface and a small HTTP client with timeout, retry, backoff and 429 handling."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

import requests

from market_monitor.config import Asset, ProviderConfig
from market_monitor.models import Quote

log = logging.getLogger(__name__)

# Upper bound for a server-requested Retry-After wait, to keep a cycle short.
MAX_RETRY_AFTER_SECONDS = 30.0


class ProviderError(Exception):
    """A quote could not be fetched or parsed. The message is safe to log."""


class HttpStatusError(ProviderError):
    def __init__(self, status: int, url: str, payload: Any):
        super().__init__(f"HTTP {status} from {url}")
        self.status = status
        self.payload = payload


class QuoteProvider(Protocol):
    name: str

    def fetch(self, asset: Asset) -> Quote: ...


def _payload(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:200]


class HttpClient:
    """GET JSON with a polite delay between requests and retries on transient errors.

    Retried: network errors, timeouts, HTTP 5xx and 429 (honouring Retry-After).
    Other HTTP errors raise HttpStatusError immediately.
    """

    def __init__(
        self,
        config: ProviderConfig,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        backoff_seconds: float = 1.0,
    ):
        self.config = config
        self.session = session or requests.Session()
        if config.user_agent:
            self.session.headers["User-Agent"] = config.user_agent
        self._sleep = sleep
        self._clock = clock
        self._backoff = backoff_seconds
        self._last_request: float | None = None

    def _wait_for_slot(self) -> None:
        delay = self.config.request_delay_ms / 1000
        if self._last_request is not None and delay > 0:
            remaining = self._last_request + delay - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last_request = self._clock()

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.config.base_url}{path}"
        attempts = self.config.retries + 1
        for attempt in range(attempts):
            last = attempt == attempts - 1
            self._wait_for_slot()
            try:
                response = self.session.get(url, params=params, timeout=self.config.timeout_seconds)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if last:
                    raise ProviderError(f"{self.config.name}: network error for {url}: {exc}") from exc
                wait = self._backoff * 2**attempt
                log.warning("%s: network error (%s), retry in %.1fs", self.config.name, type(exc).__name__, wait)
                self._sleep(wait)
                continue

            status = response.status_code
            if status == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise ProviderError(f"{self.config.name}: invalid JSON from {url}") from exc
            if (status == 429 or status >= 500) and not last:
                wait = self._backoff * 2**attempt
                if status == 429:
                    wait = max(wait, self._retry_after(response))
                log.warning("%s: HTTP %d from %s, retry in %.1fs", self.config.name, status, url, wait)
                self._sleep(wait)
                continue
            raise HttpStatusError(status, url, _payload(response))
        raise AssertionError("unreachable")

    @staticmethod
    def _retry_after(response: requests.Response) -> float:
        try:
            value = float(response.headers.get("Retry-After", 0))
        except ValueError:
            return 0.0
        return min(max(value, 0.0), MAX_RETRY_AFTER_SECONDS)


def to_float(value: Any) -> float | None:
    """Convert a provider value to float; None for null, empty or non numeric values."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # NaN check
