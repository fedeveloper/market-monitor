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


def window_label(minutes: int) -> str:
    if minutes == 60:
        return "1 ora"
    if minutes % 60 == 0:
        return f"{minutes // 60} ore"
    return f"{minutes} min"


def alert_fragment(asset: Asset, alert) -> str:
    """Plain-text description of one alert (no HTML), e.g. '+3,1% oggi (gradino 3%)'."""
    if alert.rule == "fast_move":
        return f"{format_pct(alert.change_pct)} in {window_label(alert.window_minutes)}"
    if alert.rule == "day_move":
        return (
            f"{format_pct(alert.change_pct)} {day_change_label(asset)} "
            f"(gradino {format_pct(alert.threshold, signed=False)})"
        )
    if alert.rule == "open_gap":
        return f"apertura {format_pct(alert.change_pct)} sulla chiusura precedente"
    if alert.rule == "level_above":
        return f"sopra il livello {format_price(alert.threshold, alert.currency)}"
    if alert.rule == "level_below":
        return f"sotto il livello {format_price(alert.threshold, alert.currency)}"
    return alert.rule


# Order of the fragments within an asset block.
_RULE_ORDER = {"fast_move": 0, "day_move": 1, "open_gap": 2, "level_above": 3, "level_below": 4}


def alert_message(
    title: str, assets: dict[str, Asset], alerts: Sequence, now_ts: float, tz: ZoneInfo, more: int = 0
) -> str:
    """One message for the cycle, alerts grouped by asset (see docs/ALERTS.md)."""
    lines = [header(title, now_ts, tz)]
    by_symbol: dict[str, list] = {}
    for alert in alerts:
        by_symbol.setdefault(alert.symbol, []).append(alert)
    for symbol, group in by_symbol.items():
        asset = assets[symbol]
        group = sorted(group, key=lambda a: _RULE_ORDER.get(a.rule, 9))
        main = next((a for a in group if a.rule == "day_move"), group[0])
        icon = "▲" if main.direction > 0 else "▼"
        if any(a.urgent for a in group):
            icon = "‼️ " + icon
        price = escape(format_price(main.price, main.currency))
        lines += ["", f"{icon} {asset_label(asset)}  {price}"]
        lines.append("   " + " · ".join(escape(alert_fragment(asset, a)) for a in group))
    if more:
        lines += ["", f"+{more} altri alert, al prossimo ciclo."]
    lines += ["", DELAY_NOTE]
    return "\n".join(lines)
