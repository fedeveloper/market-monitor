"""Italian message texts (Telegram HTML)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from market_monitor.config import Asset
from market_monitor.formatting import escape, format_pct, format_price
from market_monitor.models import Quote

DELAY_NOTE = "Dati con ritardo fino a 15 minuti."


def header(title: str, now_ts: float, tz: ZoneInfo) -> str:
    return f"<b>{escape(title)}</b> · {datetime.fromtimestamp(now_ts, tz):%H:%M}"


def arrow(change: float | None) -> str:
    if change is None or round(change, 1) == 0:
        return "•"
    return "▲" if change > 0 else "▼"


def asset_label(asset: Asset) -> str:
    if asset.name == asset.symbol:
        return escape(asset.symbol)
    return f"{escape(asset.name)} ({escape(asset.symbol)})"


def day_change_label(asset: Asset) -> str:
    return "in 24 ore" if asset.rules.day_move.reference == "rolling_24h" else "oggi"


def quote_line(asset: Asset, quote: Quote) -> str:
    change = quote.change_pct
    text = f"{arrow(change)} {asset_label(asset)}  {escape(format_price(quote.price, quote.currency))}"
    if change is not None:
        text += f"  {format_pct(change)} {day_change_label(asset)}"
    return text


def overview(
    title: str, rows: Sequence[tuple[Asset, Quote]], now_ts: float, tz: ZoneInfo, failed: Sequence[str] = ()
) -> str:
    lines = [header(title, now_ts, tz), ""]
    lines += [quote_line(asset, quote) for asset, quote in rows] or ["Nessun dato disponibile."]
    if failed:
        lines += ["", f"Non disponibili: {escape(', '.join(failed))}"]
    lines += ["", DELAY_NOTE]
    return "\n".join(lines)
