"""Quote providers. ``build_providers`` maps config.yaml provider names to implementations."""

from __future__ import annotations

import logging

import requests

from market_monitor.config import AppConfig
from market_monitor.providers.base import HttpClient, ProviderError, QuoteProvider
from market_monitor.providers.binance import BinanceProvider
from market_monitor.providers.yahoo import YahooChartProvider

log = logging.getLogger(__name__)

PROVIDER_CLASSES = {"yahoo": YahooChartProvider, "binance": BinanceProvider}

__all__ = ["ProviderError", "QuoteProvider", "build_providers", "PROVIDER_CLASSES"]


def build_providers(app: AppConfig, session_factory=requests.Session) -> dict[str, QuoteProvider]:
    providers: dict[str, QuoteProvider] = {}
    for name, cfg in app.providers.items():
        cls = PROVIDER_CLASSES.get(name)
        if cls is None:
            log.warning("provider %r in config.yaml has no implementation, skipped", name)
            continue
        providers[name] = cls(HttpClient(cfg, session=session_factory()))
    return providers
