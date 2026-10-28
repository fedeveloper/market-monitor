"""Shared data model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Quote:
    """One price sample from a provider.

    ``prev_close`` is the reference for the day change: the previous session
    close for stocks and ETFs, the price 24 hours earlier for crypto.
    """

    symbol: str
    ts: int  # epoch seconds (UTC) of the price, not of the fetch
    price: float
    prev_close: float | None = None
    day_open: float | None = None
    volume: float | None = None
    currency: str | None = None

    @property
    def change_pct(self) -> float | None:
        if not self.prev_close:
            return None
        return (self.price - self.prev_close) / self.prev_close * 100
