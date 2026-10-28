"""Binance public API (no key) for crypto pairs such as BTCEUR."""

from __future__ import annotations

from typing import Any

from market_monitor.config import Asset
from market_monitor.models import Quote
from market_monitor.providers.base import HttpClient, HttpStatusError, ProviderError, to_float

# Quote assets recognised at the end of a pair symbol, longest first.
_QUOTE_ASSETS = ("FDUSD", "USDT", "USDC", "EUR", "USD", "GBP", "TRY", "BRL", "JPY", "BTC", "ETH", "BNB")


def pair_currency(symbol: str) -> str | None:
    for quote in _QUOTE_ASSETS:
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return quote
    return None


def parse_ticker_24hr(symbol: str, data: Any) -> Quote:
    """Build a Quote from ``/api/v3/ticker/24hr``.

    ``openPrice`` is the price 24 hours ago, used as the rolling day reference.
    """
    if not isinstance(data, dict):
        raise ProviderError(f"binance {symbol}: unexpected response")
    price = to_float(data.get("lastPrice"))
    close_time = data.get("closeTime")
    if price is None or not isinstance(close_time, int):
        raise ProviderError(f"binance {symbol}: no price in response")
    return Quote(
        symbol=symbol,
        ts=close_time // 1000,
        price=price,
        prev_close=to_float(data.get("openPrice")),
        volume=to_float(data.get("volume")),
        currency=pair_currency(symbol),
    )


class BinanceProvider:
    name = "binance"

    def __init__(self, client: HttpClient):
        self.client = client

    def fetch(self, asset: Asset) -> Quote:
        try:
            data = self.client.get_json("/api/v3/ticker/24hr", params={"symbol": asset.symbol})
        except HttpStatusError as exc:
            if isinstance(exc.payload, dict) and "msg" in exc.payload:
                raise ProviderError(f"binance {asset.symbol}: {exc.payload['msg']} (HTTP {exc.status})") from None
            raise
        return parse_ticker_24hr(asset.symbol, data)
