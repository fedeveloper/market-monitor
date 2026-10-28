"""The monitoring cycle and the service loop."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from market_monitor import markets
from market_monitor.config import Asset, Config, ConfigManager
from market_monitor.logging_setup import setup_logging
from market_monitor.messages import overview
from market_monitor.models import Quote
from market_monitor.notifier import Notifier
from market_monitor.providers import ProviderError, QuoteProvider, build_providers
from market_monitor.scheduler import next_run
from market_monitor.storage import Storage

log = logging.getLogger(__name__)


@dataclass
class FetchResult:
    asset: Asset
    quote: Quote | None = None
    error: str | None = None
    skipped: bool = False  # market closed: not fetched
    fresh: bool = False


@dataclass
class CycleResult:
    now_ts: float
    results: list[FetchResult] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> list[FetchResult]:
        return [r for r in self.results if r.quote is not None]

    @property
    def failed(self) -> list[FetchResult]:
        return [r for r in self.results if r.error is not None]


def current_rss_mb() -> float | None:
    """Resident memory of this process in MB (Linux), for the cycle log."""
    try:
        with open("/proc/self/status", encoding="ascii") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        return None
    return None


def fetch_all(
    config: Config, providers: dict[str, QuoteProvider], now_ts: float
) -> list[FetchResult]:
    """Fetch every enabled asset whose market is open. A failing asset never stops the others."""
    stale_seconds = config.alerts.health.stale_data_minutes * 60
    results: list[FetchResult] = []
    for asset in config.enabled_assets:
        market = config.app.markets[asset.market]
        if not markets.should_fetch(market, now_ts):
            results.append(FetchResult(asset, skipped=True))
            continue
        provider = providers.get(asset.provider)
        if provider is None:
            results.append(FetchResult(asset, error=f"provider {asset.provider!r} not implemented"))
            continue
        try:
            quote = provider.fetch(asset)
        except ProviderError as exc:
            log.warning("%s: %s", asset.symbol, exc)
            results.append(FetchResult(asset, error=str(exc)))
            continue
        except Exception as exc:  # a provider bug must not stop the cycle
            log.exception("%s: unexpected error from provider %s", asset.symbol, asset.provider)
            results.append(FetchResult(asset, error=f"{type(exc).__name__}: {exc}"))
            continue
        fresh = now_ts - quote.ts <= stale_seconds
        if not fresh:
            log.info("%s: stale data (%d min old), no alerts this cycle", asset.symbol, (now_ts - quote.ts) // 60)
        results.append(FetchResult(asset, quote=quote, fresh=fresh))
    return results


class Service:
    def __init__(
        self,
        manager: ConfigManager,
        notifier: Notifier,
        *,
        dry_run: bool = False,
        storage: Storage | None = None,
        providers_factory: Callable[[Config], dict[str, QuoteProvider]] | None = None,
        clock: Callable[[], float] = time.time,
        configure_logging: bool = True,
    ):
        self.manager = manager
        self.notifier = notifier
        self.dry_run = dry_run
        self.clock = clock
        self._configure_logging = configure_logging
        self._providers_factory = providers_factory or (lambda config: build_providers(config.app))
        self._apply_config(manager.config)
        self.storage = storage or Storage(self.config.app.storage.path)
        self.stop_event = threading.Event()

    @property
    def config(self) -> Config:
        return self.manager.config

    def _apply_config(self, config: Config) -> None:
        if self._configure_logging:
            lg = config.app.logging
            setup_logging(lg.level, lg.file, lg.max_bytes, lg.backup_count)
        self.providers = self._providers_factory(config)

    # --- one cycle ---------------------------------------------------------------

    def run_cycle(self) -> CycleResult:
        if self.manager.reload_if_changed():
            self._apply_config(self.config)
        config = self.config
        now_ts = self.clock()
        started = time.monotonic()
        cycle = CycleResult(now_ts)

        cycle.results = fetch_all(config, self.providers, now_ts)
        for result in cycle.ok:
            self.storage.save_quote(result.quote)

        self.process(cycle)

        today = datetime.fromtimestamp(now_ts, config.app.timezone).date().isoformat()
        self.storage.maybe_prune(today, int(now_ts), config.app.storage.retention_days)
        if self.dry_run:
            self.storage.rollback()  # a dry run never changes the database
        else:
            self.storage.commit()

        skipped = sum(r.skipped for r in cycle.results)
        rss = current_rss_mb()
        log.info(
            "cycle done in %.1fs: %d ok, %d failed, %d skipped (market closed), %d messages%s",
            time.monotonic() - started, len(cycle.ok), len(cycle.failed), skipped, len(cycle.messages),
            f", RSS {rss:.0f} MB" if rss is not None else "",
        )
        return cycle

    def process(self, cycle: CycleResult) -> None:
        """Evaluate the fetched quotes and notify. M4: an overview of the variations."""
        rows = [(r.asset, r.quote) for r in cycle.ok]
        if not rows and not cycle.failed:
            return
        text = overview(
            "Market Monitor", rows, cycle.now_ts, self.config.app.timezone, [r.asset.symbol for r in cycle.failed]
        )
        self.send(cycle, text)

    def send(self, cycle: CycleResult, text: str) -> bool:
        ok = self.notifier.send(text)
        if ok:
            cycle.messages.append(text)
        return ok

    def run_cycle_safely(self) -> CycleResult | None:
        try:
            return self.run_cycle()
        except Exception:
            log.exception("cycle failed, continuing with the next one")
            try:
                self.storage.rollback()
            except Exception:
                log.exception("rollback failed")
            return None

    # --- loop ----------------------------------------------------------------------

    def run_forever(self) -> None:
        log.info(
            "service started: every %d min%s, %d assets",
            self.config.app.interval_minutes,
            " aligned to the clock" if self.config.app.align_to_clock else "",
            len(self.config.enabled_assets),
        )
        while not self.stop_event.is_set():
            app = self.config.app
            wake = next_run(self.clock(), app.interval_minutes, app.align_to_clock, app.timezone)
            if self.stop_event.wait(max(0.0, wake - self.clock())):
                break
            self.run_cycle_safely()
        log.info("service stopped")

    def stop(self, *_args) -> None:
        self.stop_event.set()

    def close(self) -> None:
        self.storage.close()
