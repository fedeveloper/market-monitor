"""SQLite storage: price samples, alert state, provider health, sent alerts and a key/value table.

Writes are not committed by the individual methods: the cycle calls ``commit()``
once at the end, so each cycle costs a single transaction on the SD card.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from market_monitor.models import Quote

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
  symbol TEXT NOT NULL,
  ts INTEGER NOT NULL,
  price REAL NOT NULL,
  prev_close REAL,
  day_open REAL,
  volume REAL,
  currency TEXT,
  PRIMARY KEY (symbol, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS alert_state (
  symbol TEXT NOT NULL,
  rule TEXT NOT NULL,
  last_sent_ts INTEGER,
  last_level REAL,
  session_key TEXT,
  armed INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (symbol, rule)
);
CREATE TABLE IF NOT EXISTS provider_health (
  provider TEXT PRIMARY KEY,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  alerted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sent_alerts (
  ts INTEGER NOT NULL,
  symbol TEXT NOT NULL,
  rule TEXT NOT NULL,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS sent_alerts_ts ON sent_alerts (ts);
CREATE TABLE IF NOT EXISTS sent_messages (ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


@dataclass(frozen=True)
class RuleState:
    """Persistent state of one alert rule key for one asset (see alerts.py for the keys)."""

    last_sent_ts: int | None = None
    last_level: float | None = None
    session_key: str | None = None
    armed: bool = True


@dataclass(frozen=True)
class ProviderHealth:
    consecutive_failures: int = 0
    last_error: str | None = None
    alerted: bool = False


@dataclass(frozen=True)
class SentAlert:
    ts: int
    symbol: str
    rule: str
    detail: str | None


class Storage:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        if self.path != ":memory:":
            # WAL appends to a log and fsyncs less often than the default rollback
            # journal: fewer writes on the SD card for one small commit per cycle.
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()

    # --- quotes ---------------------------------------------------------------

    def save_quote(self, quote: Quote) -> bool:
        """Store a sample. Returns False if a sample with the same (symbol, ts) exists:
        a stale quote fetched again is not duplicated."""
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO quotes (symbol, ts, price, prev_close, day_open, volume, currency)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (quote.symbol, quote.ts, quote.price, quote.prev_close, quote.day_open, quote.volume, quote.currency),
        )
        return cursor.rowcount == 1

    def samples(self, symbol: str, since_ts: int, until_ts: int | None = None) -> list[tuple[int, float]]:
        """(ts, price) samples with since_ts <= ts <= until_ts, oldest first."""
        if until_ts is None:
            rows = self.conn.execute(
                "SELECT ts, price FROM quotes WHERE symbol = ? AND ts >= ? ORDER BY ts", (symbol, since_ts)
            )
        else:
            rows = self.conn.execute(
                "SELECT ts, price FROM quotes WHERE symbol = ? AND ts BETWEEN ? AND ? ORDER BY ts",
                (symbol, since_ts, until_ts),
            )
        return [(ts, price) for ts, price in rows]

    def last_quote(self, symbol: str) -> Quote | None:
        row = self.conn.execute(
            "SELECT symbol, ts, price, prev_close, day_open, volume, currency FROM quotes"
            " WHERE symbol = ? ORDER BY ts DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        return Quote(*row) if row else None

    def prune(self, before_ts: int) -> int:
        """Delete samples and sent-alert history older than ``before_ts``. Returns deleted quotes."""
        deleted = self.conn.execute("DELETE FROM quotes WHERE ts < ?", (before_ts,)).rowcount
        self.conn.execute("DELETE FROM sent_alerts WHERE ts < ?", (before_ts,))
        self.conn.execute("DELETE FROM sent_messages WHERE ts < ?", (before_ts,))
        return deleted

    def maybe_prune(self, today: str, now_ts: int, retention_days: int) -> int | None:
        """Prune at most once per calendar day (``today`` as YYYY-MM-DD).
        Returns the deleted quotes, or None if pruning already ran today."""
        if self.get("last_prune") == today:
            return None
        deleted = self.prune(now_ts - retention_days * 86400)
        self.set("last_prune", today)
        if deleted:
            log.info("retention: deleted %d samples older than %d days", deleted, retention_days)
        return deleted

    # --- alert state -------------------------------------------------------------

    def rule_states(self, symbol: str) -> dict[str, RuleState]:
        rows = self.conn.execute(
            "SELECT rule, last_sent_ts, last_level, session_key, armed FROM alert_state WHERE symbol = ?",
            (symbol,),
        )
        return {rule: RuleState(sent, level, session, bool(armed)) for rule, sent, level, session, armed in rows}

    def set_rule_state(self, symbol: str, rule: str, state: RuleState) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO alert_state (symbol, rule, last_sent_ts, last_level, session_key, armed)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, rule, state.last_sent_ts, state.last_level, state.session_key, int(state.armed)),
        )

    # --- provider health ----------------------------------------------------------

    def provider_health(self, provider: str) -> ProviderHealth:
        row = self.conn.execute(
            "SELECT consecutive_failures, last_error, alerted FROM provider_health WHERE provider = ?", (provider,)
        ).fetchone()
        return ProviderHealth(row[0], row[1], bool(row[2])) if row else ProviderHealth()

    def all_provider_health(self) -> dict[str, ProviderHealth]:
        rows = self.conn.execute("SELECT provider, consecutive_failures, last_error, alerted FROM provider_health")
        return {p: ProviderHealth(f, e, bool(a)) for p, f, e, a in rows}

    def set_provider_health(self, provider: str, health: ProviderHealth) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO provider_health (provider, consecutive_failures, last_error, alerted)"
            " VALUES (?, ?, ?, ?)",
            (provider, health.consecutive_failures, health.last_error, int(health.alerted)),
        )

    # --- sent alerts and messages ----------------------------------------------------

    def log_sent_alert(self, alert: SentAlert) -> None:
        self.conn.execute(
            "INSERT INTO sent_alerts (ts, symbol, rule, detail) VALUES (?, ?, ?, ?)",
            (alert.ts, alert.symbol, alert.rule, alert.detail),
        )

    def sent_alerts(self, since_ts: int) -> list[SentAlert]:
        rows = self.conn.execute(
            "SELECT ts, symbol, rule, detail FROM sent_alerts WHERE ts >= ? ORDER BY ts", (since_ts,)
        )
        return [SentAlert(*row) for row in rows]

    def log_sent_message(self, ts: int) -> None:
        self.conn.execute("INSERT INTO sent_messages (ts) VALUES (?)", (ts,))

    def sent_message_times(self, since_ts: int) -> list[int]:
        rows = self.conn.execute("SELECT ts FROM sent_messages WHERE ts >= ? ORDER BY ts", (since_ts,))
        return [ts for (ts,) in rows]

    # --- key/value ----------------------------------------------------------------------

    def get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
