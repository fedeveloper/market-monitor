"""M9: status file read by picoclaw (one direction: the service writes, picoclaw reads).

The file is rewritten atomically at the end of each cycle (temporary file in the
same directory + os.replace), so a reader never sees a half-written file. It
holds market data and alert state only: never the Telegram token or chat id.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from market_monitor.config import Asset
from market_monitor.models import Quote
from market_monitor.storage import ProviderHealth, SentAlert

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class StatusData:
    now_ts: float
    interval_minutes: int
    tz: ZoneInfo
    rows: Sequence[tuple[Asset, Quote | None, bool]]  # asset, last stored quote, market open now
    recent_alerts: Sequence[SentAlert]
    levels_today: Sequence[SentAlert]
    health: dict[str, ProviderHealth]
    last_digest_ts: int | None


def _iso(ts: float, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ts, tz).isoformat(timespec="seconds")


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


def build_status(data: StatusData) -> dict[str, Any]:
    tz = data.tz
    assets = []
    for asset, quote, market_open in data.rows:
        entry: dict[str, Any] = {
            "symbol": asset.symbol,
            "name": asset.name,
            "class": asset.asset_class,
            "market": asset.market,
            "market_open": market_open,
            "price": None,
            "currency": None,
            "quote_time": None,
            "change_day_pct": None,
            "change_reference": asset.rules.day_move.reference or "prev_close",
        }
        if quote is not None:
            entry.update(
                price=quote.price,
                currency=quote.currency,
                quote_time=_iso(quote.ts, tz),
                change_day_pct=_round(quote.change_pct, 2),
            )
        if asset.levels.above or asset.levels.below:
            entry["levels"] = {"above": list(asset.levels.above), "below": list(asset.levels.below)}
        assets.append(entry)

    def alert_entry(alert: SentAlert) -> dict[str, Any]:
        return {"ts": _iso(alert.ts, tz), "symbol": alert.symbol, "rule": alert.rule, "detail": alert.detail}

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(data.now_ts, tz),
        "interval_minutes": data.interval_minutes,
        "assets": assets,
        "recent_alerts": [alert_entry(a) for a in data.recent_alerts],
        "levels_reached_today": [alert_entry(a) for a in data.levels_today],
        "providers": {
            name: {"ok": h.consecutive_failures == 0, "consecutive_failures": h.consecutive_failures}
            for name, h in sorted(data.health.items())
        },
        "last_digest": _iso(data.last_digest_ts, tz) if data.last_digest_ts else None,
        "note": "Dati di mercato con ritardo fino a 15 minuti. Non sono consigli di investimento.",
    }


def write_atomic(path: Path, content: dict[str, Any]) -> None:
    """Write JSON to ``path`` atomically. On error the previous file is left untouched."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            json.dump(content, tmp, ensure_ascii=False, indent=1)
            tmp.write("\n")
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
