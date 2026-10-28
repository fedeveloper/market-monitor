"""Command line interface."""

from __future__ import annotations

import argparse
import logging
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from market_monitor import __version__
from market_monitor.config import (
    Asset,
    Config,
    ConfigError,
    check_secrets,
    load_config,
    load_secrets,
)
from market_monitor.formatting import format_pct, format_price
from market_monitor.logging_setup import PLAIN_FORMAT, register_secret, setup_logging
from market_monitor.providers import ProviderError, build_providers

log = logging.getLogger("market_monitor")


def _pct(value: float) -> str:
    return f"{value:g}".replace(".", ",") + "%"


def _describe_rules(asset: Asset) -> str:
    rules = asset.rules
    parts = []
    if rules.fast_move.enabled:
        parts.append(f"fast {_pct(rules.fast_move.threshold_pct)}/{rules.fast_move.window_minutes}m")
    if rules.day_move.enabled:
        steps = "/".join(f"{s:g}".replace(".", ",") for s in rules.day_move.steps_pct)
        parts.append(f"day {steps}%" + (" 24h" if rules.day_move.reference == "rolling_24h" else ""))
    if rules.open_gap.enabled:
        parts.append(f"gap {_pct(rules.open_gap.threshold_pct)}")
    text = " · ".join(parts) or "nessuna regola attiva"
    if asset.levels.above or asset.levels.below:
        text += f" · livelli {len(asset.levels.above) + len(asset.levels.below)}"
    if asset.overridden_rules:
        text += f"  [override: {', '.join(asset.overridden_rules)}]"
    if not asset.enabled:
        text += "  (disattivato)"
    return text


def cmd_check_config(args: argparse.Namespace) -> int:
    config_dir = Path(args.config_dir)
    secrets = load_secrets(Path(args.env_file))
    register_secret(secrets.telegram_bot_token)
    secret_errors, secret_warnings = check_secrets(secrets)

    try:
        config = load_config(config_dir)
    except ConfigError as exc:
        log.error("Configurazione NON valida (%s), %d errori:", config_dir, len(exc.errors) + len(secret_errors))
        for error in exc.errors + secret_errors:
            log.error("  - %s", error)
        return 1

    app = config.app
    log.info("Configurazione letta da %s", config_dir)
    log.info(
        "Intervallo: %d min%s · fuso %s",
        app.interval_minutes,
        " (allineato all'orologio)" if app.align_to_clock else "",
        app.timezone,
    )
    log.info("Mercati: %s · provider: %s", ", ".join(app.markets), ", ".join(app.providers))
    enabled = config.enabled_assets
    by_class = Counter(a.asset_class for a in enabled)
    log.info(
        "Asset attivi: %d su %d (%s)",
        len(enabled),
        len(config.assets),
        ", ".join(f"{c} {n}" for c, n in sorted(by_class.items())),
    )
    rows = [(a.symbol, f"{a.asset_class}/{a.market}/{a.provider}", _describe_rules(a)) for a in config.assets]
    w1 = max((len(r[0]) for r in rows), default=0)
    w2 = max((len(r[1]) for r in rows), default=0)
    for symbol, where, rules in rows:
        log.info("  %-*s  %-*s  %s", w1, symbol, w2, where, rules)
    log.info(
        "Telegram: token %s, chat id %s",
        "presente" if secrets.telegram_bot_token else "mancante",
        "presente" if secrets.telegram_chat_id else "mancante",
    )
    for warning in list(config.warnings) + secret_warnings:
        log.warning("Avviso: %s", warning)
    if secret_errors:
        log.error("Configurazione NON valida, %d errori:", len(secret_errors))
        for error in secret_errors:
            log.error("  - %s", error)
        return 1
    log.info("Configurazione valida.")
    return 0


def _load_or_report(config_dir: Path) -> Config | None:
    try:
        config = load_config(config_dir)
    except ConfigError as exc:
        log.error("Configurazione NON valida (%s): lancia check-config per i dettagli.", config_dir)
        for error in exc.errors:
            log.error("  - %s", error)
        return None
    return config


def cmd_check_symbols(args: argparse.Namespace, session_factory=None) -> int:
    config = _load_or_report(Path(args.config_dir))
    if config is None:
        return 1
    providers = build_providers(config.app, **({"session_factory": session_factory} if session_factory else {}))
    tz = config.app.timezone
    now = time.time()
    failures = 0
    assets = config.enabled_assets
    for asset in assets:
        provider = providers.get(asset.provider)
        if provider is None:
            log.error("  ERRORE %-9s provider %r non implementato", asset.symbol, asset.provider)
            failures += 1
            continue
        try:
            quote = provider.fetch(asset)
        except ProviderError as exc:
            log.error("  ERRORE %-9s %s", asset.symbol, exc)
            failures += 1
            continue
        when = datetime.fromtimestamp(quote.ts, tz).strftime("%d/%m %H:%M")
        delay = max(0, round((now - quote.ts) / 60))
        change = format_pct(quote.change_pct) if quote.change_pct is not None else "n.d."
        log.info(
            "  OK     %-9s %-24s %14s  %7s  dato delle %s (%d min fa)",
            asset.symbol, asset.name[:24], format_price(quote.price, quote.currency), change, when, delay,
        )
    log.info("Simboli verificati: %d, falliti: %d", len(assets), failures)
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="market_monitor", description="Market Monitor")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config-dir", default="config", help="cartella con i file YAML (default: config)")
    parser.add_argument("--env-file", default=".env", help="file con i segreti (default: .env)")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    check = sub.add_parser("check-config", help="valida config, watchlist e alert")
    check.set_defaults(func=cmd_check_config)
    symbols = sub.add_parser("check-symbols", help="prova ogni simbolo con il suo provider (usa la rete)")
    symbols.set_defaults(func=cmd_check_symbols)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # CLI commands log to stdout only; the rotating file is for the service (run).
    setup_logging(level="INFO", fmt=PLAIN_FORMAT)
    return args.func(args)
