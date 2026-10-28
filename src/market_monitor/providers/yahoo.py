"""Yahoo Finance ``chart`` endpoint (unofficial) for stocks and ETFs."""

from __future__ import annotations

from typing import Any

from market_monitor.config import Asset
from market_monitor.models import Quote
from market_monitor.providers.base import HttpClient, HttpStatusError, ProviderError, to_float


def parse_chart(symbol: str, data: Any) -> Quote:
    """Build a Quote from a ``/v8/finance/chart`` response."""
    try:
        chart = data["chart"]
    except (KeyError, TypeError):
        raise ProviderError(f"yahoo {symbol}: unexpected response, missing 'chart'") from None
    error = chart.get("error")
    if error:
        description = error.get("description") if isinstance(error, dict) else error
        raise ProviderError(f"yahoo {symbol}: {description}")
    results = chart.get("result") or []
    if not results:
        raise ProviderError(f"yahoo {symbol}: empty result")
    result = results[0]
    meta = result.get("meta") or {}

    timestamps = result.get("timestamp") or []
    quotes = (result.get("indicators") or {}).get("quote") or [{}]
    candles = quotes[0] if quotes else {}
    closes = candles.get("close") or []
    opens = candles.get("open") or []

    price = to_float(meta.get("regularMarketPrice"))
    ts = meta.get("regularMarketTime")
    if price is None or not isinstance(ts, int):
        # Fall back to the last candle with a close.
        for candle_ts, close in reversed(list(zip(timestamps, closes))):
            if to_float(close) is not None:
                price, ts = to_float(close), int(candle_ts)
                break
    if price is None or not isinstance(ts, int):
        raise ProviderError(f"yahoo {symbol}: no price in response")

    prev_close = to_float(meta.get("previousClose"))
    if prev_close is None:
        prev_close = to_float(meta.get("chartPreviousClose"))
    day_open = next((to_float(v) for v in opens if to_float(v) is not None), None)

    return Quote(
        symbol=symbol,
        ts=ts,
        price=price,
        prev_close=prev_close,
        day_open=day_open,
        volume=to_float(meta.get("regularMarketVolume")),
        currency=meta.get("currency"),
    )


class YahooChartProvider:
    name = "yahoo"

    def __init__(self, client: HttpClient):
        self.client = client

    def fetch(self, asset: Asset) -> Quote:
        try:
            data = self.client.get_json(
                f"/v8/finance/chart/{asset.symbol}", params={"interval": "15m", "range": "1d"}
            )
        except HttpStatusError as exc:
            # Unknown symbols come back as 404 with a chart.error body.
            if isinstance(exc.payload, dict) and "chart" in exc.payload:
                return parse_chart(asset.symbol, exc.payload)
            raise
        return parse_chart(asset.symbol, data)
