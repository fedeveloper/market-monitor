"""Daily digest: prices and day changes, top movers, levels reached, service status."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from market_monitor.config import Asset, DigestConfig
from market_monitor.formatting import escape, format_pct
from market_monitor.messages import DELAY_NOTE, asset_label, quote_line
from market_monitor.models import Quote
from market_monitor.storage import ProviderHealth, SentAlert

WEEKDAYS_IT = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
CLASS_TITLES = (("stock", "Azioni"), ("etf", "ETF"), ("crypto", "Crypto"))
# A missed digest (service down at digest time) is still sent within this window.
LATE_WINDOW = timedelta(hours=4)


def digest_due(now_ts: float, config: DigestConfig, last_sent_day: str | None) -> str | None:
    """The local day (YYYY-MM-DD) whose digest is due now, or None."""
    if not config.enabled:
        return None
    now = datetime.fromtimestamp(now_ts, config.timezone)
    scheduled = datetime.combine(now.date(), config.time, config.timezone)
    day = now.date().isoformat()
    if scheduled <= now < scheduled + LATE_WINDOW and last_sent_day != day:
        return day
    return None


def is_weekend(now_ts: float, config: DigestConfig) -> bool:
    return datetime.fromtimestamp(now_ts, config.timezone).weekday() >= 5


@dataclass(frozen=True)
class DigestData:
    rows: Sequence[tuple[Asset, Quote | None]]
    alerts_today: Sequence[SentAlert]
    health: dict[str, ProviderHealth]


def build_digest(data: DigestData, now_ts: float, config: DigestConfig) -> str:
    tz = config.timezone
    now = datetime.fromtimestamp(now_ts, tz)
    weekend = is_weekend(now_ts, config)
    rows = [(a, q) for a, q in data.rows if not (weekend and config.weekend_crypto_only and a.asset_class != "crypto")]

    lines = [f"<b>Market Monitor · Riepilogo</b> · {WEEKDAYS_IT[now.weekday()]} {now:%d/%m %H:%M}"]
    if weekend and config.weekend_crypto_only:
        lines.append("Weekend: solo crypto.")
    for asset_class, title in CLASS_TITLES:
        group = [(a, q) for a, q in rows if a.asset_class == asset_class]
        if not group:
            continue
        lines += ["", f"<b>{title}</b>"]
        for asset, quote in group:
            if quote is None:
                lines.append(f"• {asset_label(asset)}  nessun dato")
                continue
            line = quote_line(asset, quote)
            quote_day = datetime.fromtimestamp(quote.ts, tz).date()
            if quote_day != now.date():
                line += f"  (dato del {quote_day:%d/%m})"
            lines.append(line)

    movers = [(a, q.change_pct) for a, q in rows if q is not None and q.change_pct is not None]
    if config.top_movers and movers:
        ups = sorted((m for m in movers if m[1] > 0), key=lambda m: -m[1])[: config.top_movers]
        downs = sorted((m for m in movers if m[1] < 0), key=lambda m: m[1])[: config.top_movers]
        lines.append("")
        if ups:
            lines.append("Maggiori rialzi: " + ", ".join(f"{escape(a.name)} {format_pct(c)}" for a, c in ups))
        if downs:
            lines.append("Maggiori ribassi: " + ", ".join(f"{escape(a.name)} {format_pct(c)}" for a, c in downs))

    names = {a.symbol: a.name for a, _ in data.rows}
    levels = [s for s in data.alerts_today if s.rule.startswith("level_")]
    if levels:
        lines += ["", "<b>Livelli raggiunti oggi</b>"]
        lines += [f"• {escape(names.get(s.symbol, s.symbol))}: {escape(s.detail or s.rule)}" for s in levels]

    lines += ["", f"Alert inviati oggi: {len(data.alerts_today)}"]
    problems = [(p, h) for p, h in sorted(data.health.items()) if h.consecutive_failures]
    if problems:
        for provider, health in problems:
            lines.append(
                f"Stato: {escape(provider)} in errore da {health.consecutive_failures} cicli"
                + (f" ({escape(health.last_error)})" if health.last_error else "")
            )
    else:
        lines.append("Stato: tutti i provider funzionano.")
    lines += ["", DELAY_NOTE]
    return "\n".join(lines)
