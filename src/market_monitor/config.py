"""Loading and validation of config.yaml, watchlist.yaml, alerts.yaml and .env.

Validation collects every problem instead of stopping at the first one.
Error messages are user facing, so they are in Italian and carry the file
and the path of the offending key (e.g. ``alerts.yaml: defaults.etf.fast_move``).

Per-asset rules are the class defaults from alerts.yaml with the asset's
``overrides`` from watchlist.yaml merged field by field.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

log = logging.getLogger(__name__)

CONFIG_FILE = "config.yaml"
WATCHLIST_FILE = "watchlist.yaml"
ALERTS_FILE = "alerts.yaml"
CONFIG_FILES = (CONFIG_FILE, WATCHLIST_FILE, ALERTS_FILE)

ASSET_CLASSES = ("stock", "etf", "crypto")
RULES = ("fast_move", "day_move", "open_gap")
DAY_REFERENCES = ("prev_close", "rolling_24h")
# Default day_move.rearm_pct when alerts.yaml does not set it (files copied before M5).
DEFAULT_REARM_PCT = {"stock": 1.0, "etf": 0.6, "crypto": 1.5}
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
TELEGRAM_MAX_CHARS = 4096
# Provider names with an implementation in market_monitor.providers.
IMPLEMENTED_PROVIDERS = ("yahoo", "binance")


class ConfigError(Exception):
    """Invalid or unreadable configuration. ``errors`` lists every problem found."""

    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MarketHours:
    name: str
    always_open: bool = False
    timezone: ZoneInfo | None = None
    open: time | None = None
    close: time | None = None
    days: frozenset[int] = frozenset()  # 0 = Monday, as datetime.weekday()


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    timeout_seconds: float
    retries: int
    request_delay_ms: int
    user_agent: str | None = None


@dataclass(frozen=True)
class TelegramConfig:
    parse_mode: str
    max_message_chars: int
    timeout_seconds: float
    retries: int


@dataclass(frozen=True)
class StorageConfig:
    path: Path
    retention_days: int


@dataclass(frozen=True)
class LoggingConfig:
    level: str
    file: Path | None
    max_bytes: int
    backup_count: int


@dataclass(frozen=True)
class AppConfig:
    interval_minutes: int
    align_to_clock: bool
    timezone: ZoneInfo
    markets: dict[str, MarketHours]
    providers: dict[str, ProviderConfig]
    telegram: TelegramConfig
    storage: StorageConfig
    logging: LoggingConfig


# Rule fields other than ``enabled`` are None only when the rule is disabled.
@dataclass(frozen=True)
class FastMoveRule:
    enabled: bool
    window_minutes: int | None = None
    threshold_pct: float | None = None
    step_pct: float | None = None


@dataclass(frozen=True)
class DayMoveRule:
    enabled: bool
    steps_pct: tuple[float, ...] | None = None
    reference: str | None = None
    # A notified step re-arms when the change falls this many points below it.
    rearm_pct: float | None = None


@dataclass(frozen=True)
class OpenGapRule:
    enabled: bool
    threshold_pct: float | None = None
    max_delay_minutes: int | None = None


@dataclass(frozen=True)
class AssetRules:
    fast_move: FastMoveRule
    day_move: DayMoveRule
    open_gap: OpenGapRule


@dataclass(frozen=True)
class LevelsConfig:
    enabled: bool
    hysteresis_pct: float


@dataclass(frozen=True)
class QuietHours:
    enabled: bool
    start: time
    end: time
    timezone: ZoneInfo
    urgent_bypass: bool


@dataclass(frozen=True)
class DeliveryConfig:
    cooldown_minutes: int
    max_alerts_per_message: int
    max_messages_per_hour: int
    quiet_hours: QuietHours
    urgent_pct: dict[str, float]


@dataclass(frozen=True)
class DigestConfig:
    enabled: bool
    time: time
    timezone: ZoneInfo
    weekend_crypto_only: bool
    top_movers: int


@dataclass(frozen=True)
class HealthConfig:
    error_alert_after_failures: int
    stale_data_minutes: int


@dataclass(frozen=True)
class AlertsConfig:
    defaults: dict[str, AssetRules]
    levels: LevelsConfig
    delivery: DeliveryConfig
    digest: DigestConfig
    health: HealthConfig


@dataclass(frozen=True)
class PriceLevels:
    above: tuple[float, ...] = ()
    below: tuple[float, ...] = ()


@dataclass(frozen=True)
class Asset:
    symbol: str
    name: str
    asset_class: str
    market: str
    provider: str
    enabled: bool
    levels: PriceLevels
    rules: AssetRules
    overridden_rules: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    app: AppConfig
    alerts: AlertsConfig
    assets: tuple[Asset, ...]
    warnings: tuple[str, ...] = ()

    @property
    def enabled_assets(self) -> tuple[Asset, ...]:
        return tuple(a for a in self.assets if a.enabled)


@dataclass(frozen=True)
class Secrets:
    telegram_bot_token: str | None = field(default=None, repr=False)
    telegram_chat_id: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        token = "set" if self.telegram_bot_token else "missing"
        chat = "set" if self.telegram_chat_id else "missing"
        return f"Secrets(telegram_bot_token=<{token}>, telegram_chat_id=<{chat}>)"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_MISSING = object()


class _Ctx:
    """Collects errors and warnings for one file."""

    def __init__(self, file: str, errors: list[str], warnings: list[str]):
        self.file = file
        self.errors = errors
        self.warnings = warnings

    def error(self, path: str, msg: str) -> None:
        self.errors.append(f"{self.file}: {path}: {msg}" if path else f"{self.file}: {msg}")

    def warn(self, path: str, msg: str) -> None:
        self.warnings.append(f"{self.file}: {path}: {msg}" if path else f"{self.file}: {msg}")


def _join(path: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if path else key


class _Invalid(ValueError):
    pass


def _bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise _Invalid("deve essere true o false")
    return value


def _str(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Invalid("deve essere un testo non vuoto")
    return value.strip()


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _Invalid(f"deve essere un numero, trovato {value!r}")
    return float(value)


def _positive(value: Any) -> float:
    number = _number(value)
    if number <= 0:
        raise _Invalid(f"deve essere maggiore di 0, trovato {value!r}")
    return number


def _non_negative(value: Any) -> float:
    number = _number(value)
    if number < 0:
        raise _Invalid(f"non può essere negativo, trovato {value!r}")
    return number


def _int_range(low: int, high: int | None = None) -> Callable[[Any], int]:
    def check(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise _Invalid(f"deve essere un numero intero, trovato {value!r}")
        if value < low or (high is not None and value > high):
            bounds = f"tra {low} e {high}" if high is not None else f">= {low}"
            raise _Invalid(f"deve essere {bounds}, trovato {value}")
        return value

    return check


def _choice(*options: str) -> Callable[[Any], str]:
    def check(value: Any) -> str:
        if value not in options:
            raise _Invalid(f"valore {value!r} non ammesso, valori possibili: {', '.join(options)}")
        return value

    return check


def _hhmm(value: Any) -> time:
    if isinstance(value, int) and not isinstance(value, bool):
        # YAML 1.1 reads unquoted 9:30 as a base-60 integer (570).
        raise _Invalid('orario da scrivere tra virgolette, es. "09:30"')
    if not isinstance(value, str) or not re.fullmatch(r"\d{2}:\d{2}", value):
        raise _Invalid(f'orario non valido {value!r}, formato atteso "HH:MM"')
    hours, minutes = int(value[:2]), int(value[3:])
    if hours > 23 or minutes > 59:
        raise _Invalid(f"orario non valido {value!r}")
    return time(hours, minutes)


def _timezone(value: Any) -> ZoneInfo:
    if not isinstance(value, str) or not value:
        raise _Invalid("fuso orario mancante, es. Europe/Rome")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise _Invalid(f"fuso orario sconosciuto {value!r} (es. Europe/Rome)") from None


def _url(value: Any) -> str:
    text = _str(value)
    if not re.match(r"https?://[^/\s]+", text):
        raise _Invalid(f"URL non valido {text!r}")
    return text.rstrip("/")


def _price_list(value: Any) -> tuple[float, ...]:
    if not isinstance(value, list):
        raise _Invalid("deve essere una lista di prezzi, es. [16.0]")
    return tuple(_positive(item) for item in value)


def _steps(value: Any) -> tuple[float, ...]:
    if not isinstance(value, list) or not value:
        raise _Invalid("deve essere una lista non vuota di percentuali, es. [3, 5, 8]")
    steps = tuple(_positive(item) for item in value)
    if any(b <= a for a, b in zip(steps, steps[1:])):
        raise _Invalid(f"i gradini devono essere crescenti, trovato {list(value)}")
    return steps


def _days(value: Any) -> frozenset[int]:
    if not isinstance(value, list) or not value:
        raise _Invalid(f"deve essere una lista non vuota di giorni tra {', '.join(WEEKDAYS)}")
    days = set()
    for item in value:
        if item not in WEEKDAYS:
            raise _Invalid(f"giorno {item!r} non valido, valori possibili: {', '.join(WEEKDAYS)}")
        days.add(WEEKDAYS.index(item))
    return frozenset(days)


def _mapping(ctx: _Ctx, data: Any, path: str, *, required: bool = True) -> dict | None:
    if data is None:
        if required:
            ctx.error(path, "sezione obbligatoria mancante")
        return None
    if not isinstance(data, dict):
        ctx.error(path, f"deve essere una sezione (chiave: valore), trovato {type(data).__name__}")
        return None
    return data


def _unknown_keys(ctx: _Ctx, data: Mapping, path: str, allowed: tuple[str, ...] | set[str]) -> None:
    for key in data:
        if key not in allowed:
            ctx.error(_join(path, str(key)), f"chiave sconosciuta (refuso?), ammesse: {', '.join(allowed)}")


def _get(
    ctx: _Ctx,
    data: Mapping,
    key: str,
    path: str,
    check: Callable[[Any], Any],
    default: Any = _MISSING,
) -> Any:
    """Read and validate ``data[key]``. Returns None (and records an error) if invalid."""
    full = _join(path, key)
    if data.get(key) is None:
        if default is _MISSING:
            ctx.error(full, "campo obbligatorio mancante")
            return None
        return default
    try:
        return check(data[key])
    except _Invalid as exc:
        ctx.error(full, str(exc))
        return None


# ---------------------------------------------------------------------------
# config.yaml
# ---------------------------------------------------------------------------


def _parse_market(ctx: _Ctx, name: str, data: Any, path: str) -> MarketHours | None:
    data = _mapping(ctx, data, path)
    if data is None:
        return None
    if data.get("always_open") is True:
        _unknown_keys(ctx, data, path, ("always_open",))
        return MarketHours(name=name, always_open=True)
    _unknown_keys(ctx, data, path, ("always_open", "timezone", "open", "close", "days"))
    _get(ctx, data, "always_open", path, _bool, default=False)
    tz = _get(ctx, data, "timezone", path, _timezone)
    opens = _get(ctx, data, "open", path, _hhmm)
    closes = _get(ctx, data, "close", path, _hhmm)
    days = _get(ctx, data, "days", path, _days)
    if opens and closes and opens >= closes:
        ctx.error(path, "l'orario di apertura deve precedere quello di chiusura")
        return None
    if None in (tz, opens, closes, days):
        return None
    return MarketHours(name=name, timezone=tz, open=opens, close=closes, days=days)


def _parse_provider(ctx: _Ctx, name: str, data: Any, path: str) -> ProviderConfig | None:
    data = _mapping(ctx, data, path)
    if data is None:
        return None
    _unknown_keys(
        ctx, data, path, ("base_url", "timeout_seconds", "retries", "request_delay_ms", "user_agent")
    )
    values = {
        "base_url": _get(ctx, data, "base_url", path, _url),
        "timeout_seconds": _get(ctx, data, "timeout_seconds", path, _positive),
        "retries": _get(ctx, data, "retries", path, _int_range(0, 10)),
        "request_delay_ms": _get(ctx, data, "request_delay_ms", path, _int_range(0), default=0),
        "user_agent": _get(ctx, data, "user_agent", path, _str, default=None),
    }
    if any(values[k] is None for k in ("base_url", "timeout_seconds", "retries", "request_delay_ms")):
        return None
    return ProviderConfig(name=name, **values)


def _resolve(base_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else base_dir / path


def parse_app_config(data: Any, base_dir: Path, errors: list[str], warnings: list[str]) -> AppConfig | None:
    ctx = _Ctx(CONFIG_FILE, errors, warnings)
    data = _mapping(ctx, data, "")
    if data is None:
        return None
    n_errors = len(errors)
    sections = (
        "interval_minutes", "align_to_clock", "timezone", "markets", "providers",
        "telegram", "storage", "logging",
    )
    _unknown_keys(ctx, data, "", sections)

    interval = _get(ctx, data, "interval_minutes", "", _int_range(1, 1440))
    align = _get(ctx, data, "align_to_clock", "", _bool, default=True)
    if interval and align and 1440 % interval:
        ctx.error(
            "interval_minutes",
            f"con align_to_clock: true l'intervallo deve dividere le 24 ore "
            f"(es. 5, 10, 15, 20, 30, 60), trovato {interval}",
        )
    tz = _get(ctx, data, "timezone", "", _timezone)

    markets: dict[str, MarketHours] = {}
    markets_data = _mapping(ctx, data.get("markets"), "markets")
    if markets_data is not None:
        if not markets_data:
            ctx.error("markets", "definisci almeno un mercato")
        for name, raw in markets_data.items():
            market = _parse_market(ctx, str(name), raw, _join("markets", str(name)))
            if market:
                markets[str(name)] = market

    providers: dict[str, ProviderConfig] = {}
    providers_data = _mapping(ctx, data.get("providers"), "providers")
    if providers_data is not None:
        for name, raw in providers_data.items():
            provider = _parse_provider(ctx, str(name), raw, _join("providers", str(name)))
            if provider:
                providers[str(name)] = provider
                if provider.name not in IMPLEMENTED_PROVIDERS:
                    ctx.warn(_join("providers", provider.name), "provider non implementato, verrà ignorato")

    telegram = None
    tg = _mapping(ctx, data.get("telegram"), "telegram")
    if tg is not None:
        _unknown_keys(ctx, tg, "telegram", ("parse_mode", "max_message_chars", "timeout_seconds", "retries"))
        values = {
            "parse_mode": _get(ctx, tg, "parse_mode", "telegram", _choice("HTML"), default="HTML"),
            "max_message_chars": _get(
                ctx, tg, "max_message_chars", "telegram", _int_range(100, TELEGRAM_MAX_CHARS), default=4000
            ),
            "timeout_seconds": _get(ctx, tg, "timeout_seconds", "telegram", _positive, default=10.0),
            "retries": _get(ctx, tg, "retries", "telegram", _int_range(0, 10), default=2),
        }
        if None not in values.values():
            telegram = TelegramConfig(**values)

    storage = None
    st = _mapping(ctx, data.get("storage"), "storage")
    if st is not None:
        _unknown_keys(ctx, st, "storage", ("path", "retention_days"))
        path = _get(ctx, st, "path", "storage", _str)
        retention = _get(ctx, st, "retention_days", "storage", _int_range(1, 3650))
        if path and retention:
            storage = StorageConfig(path=_resolve(base_dir, path), retention_days=retention)

    logging_cfg = None
    lg = _mapping(ctx, data.get("logging"), "logging")
    if lg is not None:
        _unknown_keys(ctx, lg, "logging", ("level", "file", "max_bytes", "backup_count"))
        level = _get(ctx, lg, "level", "logging", lambda v: _choice(*LOG_LEVELS)(str(v).upper()), default="INFO")
        file = _get(ctx, lg, "file", "logging", _str, default=None)
        max_bytes = _get(ctx, lg, "max_bytes", "logging", _int_range(10_000), default=1_000_000)
        backup_count = _get(ctx, lg, "backup_count", "logging", _int_range(0, 100), default=3)
        if None not in (level, max_bytes, backup_count):
            logging_cfg = LoggingConfig(
                level=level,
                file=_resolve(base_dir, file) if file else None,
                max_bytes=max_bytes,
                backup_count=backup_count,
            )

    if len(errors) > n_errors:
        return None
    return AppConfig(
        interval_minutes=interval,
        align_to_clock=align,
        timezone=tz,
        markets=markets,
        providers=providers,
        telegram=telegram,
        storage=storage,
        logging=logging_cfg,
    )


# ---------------------------------------------------------------------------
# alerts.yaml
# ---------------------------------------------------------------------------

_RULE_FIELDS: dict[str, dict[str, Callable[[Any], Any]]] = {
    "fast_move": {
        "enabled": _bool,
        "window_minutes": _int_range(1, 1440),
        "threshold_pct": _positive,
        "step_pct": _positive,
    },
    "day_move": {
        "enabled": _bool,
        "steps_pct": _steps,
        "reference": _choice(*DAY_REFERENCES),
        "rearm_pct": _positive,
    },
    "open_gap": {
        "enabled": _bool,
        "threshold_pct": _positive,
        "max_delay_minutes": _int_range(1, 1440),
    },
}
# Fields that must have a value (from defaults or override) when the rule is enabled.
_RULE_REQUIRED: dict[str, tuple[str, ...]] = {
    "fast_move": ("window_minutes", "threshold_pct", "step_pct"),
    "day_move": ("steps_pct",),
    "open_gap": ("threshold_pct", "max_delay_minutes"),
}
_RULE_CLASSES = {"fast_move": FastMoveRule, "day_move": DayMoveRule, "open_gap": OpenGapRule}


def _rule_values(ctx: _Ctx, rule: str, data: Any, path: str) -> dict[str, Any] | None:
    """Validate the fields present in a rule mapping. Missing fields are not an error here."""
    data = _mapping(ctx, data, path)
    if data is None:
        return None
    fields = _RULE_FIELDS[rule]
    _unknown_keys(ctx, data, path, tuple(fields))
    values: dict[str, Any] = {}
    ok = True
    for key, check in fields.items():
        if key in data:
            value = _get(ctx, data, key, path, check)
            if value is None:
                ok = False
            else:
                values[key] = value
    return values if ok else None


def _build_rule(ctx: _Ctx, rule: str, asset_class: str, values: dict[str, Any], path: str):
    values = dict(values)
    values.setdefault("enabled", True)
    if rule == "day_move":
        values.setdefault("reference", "rolling_24h" if asset_class == "crypto" else "prev_close")
        values.setdefault("rearm_pct", DEFAULT_REARM_PCT[asset_class])
    if values["enabled"]:
        missing = [k for k in _RULE_REQUIRED[rule] if k not in values]
        if missing:
            ctx.error(path, f"regola attiva ma mancano i campi: {', '.join(missing)}")
            return None
    if rule == "open_gap" and values["enabled"] and asset_class == "crypto":
        ctx.error(path, "open_gap non si applica alle crypto (mercato sempre aperto): usa enabled: false")
        return None
    return _RULE_CLASSES[rule](**values)


def _parse_defaults(ctx: _Ctx, data: Any) -> tuple[dict[str, AssetRules], dict[str, dict[str, dict]]]:
    """Return (validated rules per class, raw validated values per class/rule for merging)."""
    rules: dict[str, AssetRules] = {}
    raw: dict[str, dict[str, dict]] = {}
    data = _mapping(ctx, data, "defaults")
    if data is None:
        return rules, raw
    _unknown_keys(ctx, data, "defaults", ASSET_CLASSES)
    for asset_class in ASSET_CLASSES:
        path = _join("defaults", asset_class)
        class_data = _mapping(ctx, data.get(asset_class), path)
        if class_data is None:
            continue
        _unknown_keys(ctx, class_data, path, RULES)
        built = {}
        raw[asset_class] = {}
        for rule in RULES:
            rule_path = _join(path, rule)
            values = _rule_values(ctx, rule, class_data.get(rule), rule_path)
            if values is None:
                continue
            raw[asset_class][rule] = values
            parsed = _build_rule(ctx, rule, asset_class, values, rule_path)
            if parsed is not None:
                built[rule] = parsed
        if len(built) == len(RULES):
            rules[asset_class] = AssetRules(**built)
    return rules, raw


def parse_alerts_config(
    data: Any, errors: list[str], warnings: list[str]
) -> tuple[AlertsConfig | None, dict[str, dict[str, dict]]]:
    """Return the alerts config and the raw class defaults used for per-asset merging."""
    ctx = _Ctx(ALERTS_FILE, errors, warnings)
    data = _mapping(ctx, data, "")
    if data is None:
        return None, {}
    n_errors = len(errors)
    _unknown_keys(ctx, data, "", ("defaults", "levels", "delivery", "digest", "health"))

    defaults, raw_defaults = _parse_defaults(ctx, data.get("defaults"))

    levels = None
    lv = _mapping(ctx, data.get("levels"), "levels", required=False) or {}
    _unknown_keys(ctx, lv, "levels", ("enabled", "hysteresis_pct"))
    enabled = _get(ctx, lv, "enabled", "levels", _bool, default=True)
    hysteresis = _get(ctx, lv, "hysteresis_pct", "levels", _non_negative, default=0.5)
    if enabled is not None and hysteresis is not None:
        levels = LevelsConfig(enabled=enabled, hysteresis_pct=hysteresis)

    delivery = None
    dv = _mapping(ctx, data.get("delivery"), "delivery")
    if dv is not None:
        _unknown_keys(
            ctx, dv, "delivery",
            ("cooldown_minutes", "max_alerts_per_message", "max_messages_per_hour", "quiet_hours", "urgent_pct"),
        )
        cooldown = _get(ctx, dv, "cooldown_minutes", "delivery", _int_range(0))
        per_message = _get(ctx, dv, "max_alerts_per_message", "delivery", _int_range(1, 100))
        per_hour = _get(ctx, dv, "max_messages_per_hour", "delivery", _int_range(1))

        quiet = None
        qh = _mapping(ctx, dv.get("quiet_hours"), "delivery.quiet_hours")
        if qh is not None:
            p = "delivery.quiet_hours"
            _unknown_keys(ctx, qh, p, ("enabled", "start", "end", "timezone", "urgent_bypass"))
            values = {
                "enabled": _get(ctx, qh, "enabled", p, _bool, default=True),
                "start": _get(ctx, qh, "start", p, _hhmm),
                "end": _get(ctx, qh, "end", p, _hhmm),
                "timezone": _get(ctx, qh, "timezone", p, _timezone),
                "urgent_bypass": _get(ctx, qh, "urgent_bypass", p, _bool, default=True),
            }
            if values["start"] is not None and values["start"] == values["end"]:
                ctx.error(p, "start ed end coincidono: la fascia sarebbe vuota")
            elif None not in values.values():
                quiet = QuietHours(**values)

        urgent: dict[str, float] = {}
        ug = _mapping(ctx, dv.get("urgent_pct"), "delivery.urgent_pct")
        if ug is not None:
            _unknown_keys(ctx, ug, "delivery.urgent_pct", ASSET_CLASSES)
            for asset_class in ASSET_CLASSES:
                value = _get(ctx, ug, asset_class, "delivery.urgent_pct", _positive)
                if value is not None:
                    urgent[asset_class] = value

        if None not in (cooldown, per_message, per_hour, quiet) and len(urgent) == len(ASSET_CLASSES):
            delivery = DeliveryConfig(
                cooldown_minutes=cooldown,
                max_alerts_per_message=per_message,
                max_messages_per_hour=per_hour,
                quiet_hours=quiet,
                urgent_pct=urgent,
            )

    digest = None
    dg = _mapping(ctx, data.get("digest"), "digest")
    if dg is not None:
        _unknown_keys(ctx, dg, "digest", ("enabled", "time", "timezone", "weekend_crypto_only", "top_movers"))
        values = {
            "enabled": _get(ctx, dg, "enabled", "digest", _bool, default=True),
            "time": _get(ctx, dg, "time", "digest", _hhmm),
            "timezone": _get(ctx, dg, "timezone", "digest", _timezone),
            "weekend_crypto_only": _get(ctx, dg, "weekend_crypto_only", "digest", _bool, default=True),
            "top_movers": _get(ctx, dg, "top_movers", "digest", _int_range(0, 50), default=3),
        }
        if None not in values.values():
            digest = DigestConfig(**values)

    health = None
    hl = _mapping(ctx, data.get("health"), "health")
    if hl is not None:
        _unknown_keys(ctx, hl, "health", ("error_alert_after_failures", "stale_data_minutes"))
        failures = _get(ctx, hl, "error_alert_after_failures", "health", _int_range(1))
        stale = _get(ctx, hl, "stale_data_minutes", "health", _int_range(1))
        if failures is not None and stale is not None:
            health = HealthConfig(error_alert_after_failures=failures, stale_data_minutes=stale)

    if len(errors) > n_errors:
        return None, raw_defaults
    return AlertsConfig(defaults=defaults, levels=levels, delivery=delivery, digest=digest, health=health), raw_defaults


# ---------------------------------------------------------------------------
# watchlist.yaml
# ---------------------------------------------------------------------------

_ASSET_KEYS = ("symbol", "name", "class", "market", "provider", "levels", "overrides", "enabled")


def parse_watchlist(
    data: Any,
    app: AppConfig | None,
    raw_defaults: dict[str, dict[str, dict]] | None,
    errors: list[str],
    warnings: list[str],
) -> tuple[Asset, ...] | None:
    """Parse the watchlist. ``app`` and ``raw_defaults`` are None when their files are invalid:
    the watchlist is then checked on its own, without cross references or merging."""
    ctx = _Ctx(WATCHLIST_FILE, errors, warnings)
    data = _mapping(ctx, data, "")
    if data is None:
        return None
    n_errors = len(errors)
    _unknown_keys(ctx, data, "", ("assets",))
    items = data.get("assets")
    if not isinstance(items, list):
        ctx.error("assets", "deve essere una lista di asset")
        return None
    if not items:
        ctx.warn("assets", "watchlist vuota: nessun asset da monitorare")

    assets: list[Asset] = []
    seen: dict[str, str] = {}
    for index, item in enumerate(items):
        base_path = _join("assets", index)
        item = _mapping(ctx, item, base_path)
        if item is None:
            continue
        symbol = _get(ctx, item, "symbol", base_path, _str)
        path = f"{base_path} ({symbol})" if symbol else base_path
        _unknown_keys(ctx, item, path, _ASSET_KEYS)
        name = _get(ctx, item, "name", path, _str, default=symbol)
        asset_class = _get(ctx, item, "class", path, _choice(*ASSET_CLASSES))
        market = _get(ctx, item, "market", path, _str)
        provider = _get(ctx, item, "provider", path, _str)
        enabled = _get(ctx, item, "enabled", path, _bool, default=True)

        if symbol:
            if symbol in seen:
                ctx.error(path, f"simbolo duplicato, già definito in {seen[symbol]}")
            else:
                seen[symbol] = base_path
        if app is not None:
            if market and market not in app.markets:
                ctx.error(_join(path, "market"), f"mercato {market!r} non definito in config.yaml "
                          f"(disponibili: {', '.join(app.markets)})")
            elif market and asset_class == "crypto" and not app.markets[market].always_open:
                ctx.warn(_join(path, "market"), f"asset crypto su un mercato con orari ({market})")
            if provider and provider not in app.providers:
                ctx.error(_join(path, "provider"), f"provider {provider!r} non definito in config.yaml "
                          f"(disponibili: {', '.join(app.providers)})")

        levels = PriceLevels()
        lv = _mapping(ctx, item.get("levels"), _join(path, "levels"), required=False)
        if lv is not None:
            _unknown_keys(ctx, lv, _join(path, "levels"), ("above", "below"))
            above = _get(ctx, lv, "above", _join(path, "levels"), _price_list, default=())
            below = _get(ctx, lv, "below", _join(path, "levels"), _price_list, default=())
            levels = PriceLevels(above=tuple(sorted(above or ())), below=tuple(sorted(below or ())))

        overrides: dict[str, dict] = {}
        ov_path = _join(path, "overrides")
        ov = _mapping(ctx, item.get("overrides"), ov_path, required=False)
        if ov is not None:
            _unknown_keys(ctx, ov, ov_path, RULES)
            for rule in RULES:
                if rule in ov:
                    values = _rule_values(ctx, rule, ov[rule], _join(ov_path, rule))
                    if values is not None:
                        overrides[rule] = values

        if raw_defaults is None or asset_class is None or asset_class not in raw_defaults:
            continue
        class_defaults = raw_defaults[asset_class]
        if any(rule not in class_defaults for rule in RULES):
            continue  # the defaults themselves are invalid, already reported
        built = {}
        for rule in RULES:
            merged = {**class_defaults[rule], **overrides.get(rule, {})}
            parsed = _build_rule(ctx, rule, asset_class, merged, _join(ov_path, rule) if rule in overrides
                                 else f"{path} (default {asset_class}.{rule})")
            if parsed is not None:
                built[rule] = parsed
        if len(built) != len(RULES) or None in (symbol, name, market, provider, enabled):
            continue
        rules = AssetRules(**built)
        if app is not None and rules.fast_move.enabled and rules.fast_move.window_minutes % app.interval_minutes:
            ctx.warn(
                _join(path, "fast_move.window_minutes"),
                f"la finestra ({rules.fast_move.window_minutes} min) non è un multiplo "
                f"dell'intervallo ({app.interval_minutes} min)",
            )
        assets.append(
            Asset(
                symbol=symbol,
                name=name,
                asset_class=asset_class,
                market=market,
                provider=provider,
                enabled=enabled,
                levels=levels,
                rules=rules,
                overridden_rules=tuple(r for r in RULES if r in overrides),
            )
        )

    if len(errors) > n_errors:
        return None
    if items and not any(a.enabled for a in assets):
        ctx.warn("assets", "tutti gli asset sono disattivati (enabled: false)")
    return tuple(assets)


# ---------------------------------------------------------------------------
# Loading files
# ---------------------------------------------------------------------------


def _read_yaml(path: Path, errors: list[str]) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        example = path.name.replace(".yaml", ".example.yaml")
        errors.append(f"{path.name}: file mancante ({path}); copialo da {example}")
        return None
    except OSError as exc:
        errors.append(f"{path.name}: impossibile leggere il file: {exc}")
        return None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (riga {mark.line + 1}, colonna {mark.column + 1})" if mark else ""
        problem = getattr(exc, "problem", None) or str(exc)
        errors.append(f"{path.name}: YAML non valido{where}: {problem}")
        return None
    if data is None:
        errors.append(f"{path.name}: file vuoto")
    return data


def load_config(config_dir: Path, base_dir: Path | None = None) -> Config:
    """Load and validate the three YAML files. Raises ConfigError listing every problem.

    Relative paths in config.yaml (storage, log file) are resolved against ``base_dir``,
    by default the parent of ``config_dir`` (the project root).
    """
    config_dir = Path(config_dir)
    base_dir = Path(base_dir) if base_dir is not None else config_dir.resolve().parent
    errors: list[str] = []
    warnings: list[str] = []

    app_data = _read_yaml(config_dir / CONFIG_FILE, errors)
    alerts_data = _read_yaml(config_dir / ALERTS_FILE, errors)
    watchlist_data = _read_yaml(config_dir / WATCHLIST_FILE, errors)

    app = parse_app_config(app_data, base_dir, errors, warnings) if app_data is not None else None
    alerts, raw_defaults = (None, None)
    if alerts_data is not None:
        alerts, raw_defaults = parse_alerts_config(alerts_data, errors, warnings)
    assets = None
    if watchlist_data is not None:
        assets = parse_watchlist(watchlist_data, app, raw_defaults, errors, warnings)

    if errors or app is None or alerts is None or assets is None:
        raise ConfigError(errors or ["configurazione non valida"])
    return Config(app=app, alerts=alerts, assets=assets, warnings=tuple(warnings))


# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------

_ENV_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
TOKEN_FORMAT = re.compile(r"^\d+:[A-Za-z0-9_-]{20,}$")
CHAT_ID_FORMAT = re.compile(r"^-?\d+$")


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE lines, # comments, optional quotes and ``export``.

    Returns an empty dict if the file does not exist. Values are never logged.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if not match:
            log.warning("%s: line %d ignored (expected KEY=VALUE)", path, number)
            continue
        key, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def load_secrets(env_path: Path, environ: Mapping[str, str] | None = None) -> Secrets:
    """Read Telegram secrets. Process environment variables (e.g. systemd's
    EnvironmentFile) take precedence over the .env file."""
    environ = os.environ if environ is None else environ
    file_values = parse_env_file(env_path)

    def pick(key: str) -> str | None:
        value = environ.get(key) or file_values.get(key)
        return value.strip() if value and value.strip() else None

    return Secrets(telegram_bot_token=pick("TELEGRAM_BOT_TOKEN"), telegram_chat_id=pick("TELEGRAM_CHAT_ID"))


def check_secrets(secrets: Secrets) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) about the secrets, without ever including their values."""
    errors: list[str] = []
    warnings: list[str] = []
    if secrets.telegram_bot_token is None:
        warnings.append(".env: TELEGRAM_BOT_TOKEN mancante: test-telegram e l'invio degli alert non funzioneranno")
    elif not TOKEN_FORMAT.match(secrets.telegram_bot_token):
        errors.append(".env: TELEGRAM_BOT_TOKEN in formato non valido (atteso <numero>:<chiave>)")
    if secrets.telegram_chat_id is None:
        warnings.append(".env: TELEGRAM_CHAT_ID mancante: test-telegram e l'invio degli alert non funzioneranno")
    elif not CHAT_ID_FORMAT.match(secrets.telegram_chat_id):
        errors.append(".env: TELEGRAM_CHAT_ID in formato non valido (atteso un numero intero)")
    return errors, warnings



# ---------------------------------------------------------------------------
# Hot reload
# ---------------------------------------------------------------------------

_Signature = tuple[tuple[int, int] | None, ...]


class ConfigManager:
    """Keeps the current Config and reloads it when one of the YAML files changes.

    Changes are detected from (mtime_ns, size). If the new files are invalid the
    previous config stays active and the errors are logged once per change.
    .env is not reloaded: secrets need a restart.
    """

    def __init__(self, config_dir: Path, base_dir: Path | None = None):
        self.config_dir = Path(config_dir)
        self.base_dir = base_dir
        self._config: Config | None = None
        self._signature: _Signature | None = None

    @property
    def config(self) -> Config:
        if self._config is None:
            raise RuntimeError("configuration not loaded yet: call load() first")
        return self._config

    def _current_signature(self) -> _Signature:
        signature = []
        for name in CONFIG_FILES:
            try:
                st = (self.config_dir / name).stat()
                signature.append((st.st_mtime_ns, st.st_size))
            except FileNotFoundError:
                signature.append(None)
        return tuple(signature)

    def load(self) -> Config:
        """Initial load. Raises ConfigError if the files are invalid."""
        signature = self._current_signature()
        self._config = load_config(self.config_dir, self.base_dir)
        self._signature = signature
        return self._config

    def reload_if_changed(self) -> bool:
        """Reload if any file changed since the last attempt. Returns True if a new
        config was applied, False if nothing changed or the new files are invalid."""
        signature = self._current_signature()
        if signature == self._signature:
            return False
        # Remember the attempt even if it fails, so broken files are reported once.
        self._signature = signature
        try:
            config = load_config(self.config_dir, self.base_dir)
        except ConfigError as exc:
            log.error(
                "configuration changed but is invalid, keeping the previous one:\n  %s",
                "\n  ".join(exc.errors),
            )
            return False
        self._config = config
        log.info("configuration reloaded: %d assets enabled", len(config.enabled_assets))
        for warning in config.warnings:
            log.warning("%s", warning)
        return True
