"""Provider health (pure): consecutive failed cycles, one error message, one recovery message."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from market_monitor.formatting import escape
from market_monitor.storage import ProviderHealth

MAX_ERROR_CHARS = 200


@dataclass(frozen=True)
class HealthEvent:
    provider: str
    kind: str  # "down" or "recovered"
    failures: int
    error: str | None = None


def provider_outcome(results: Sequence[tuple[bool, str | None]]) -> tuple[bool | None, str | None]:
    """Outcome of a provider in one cycle from its (ok, error) fetches.

    None if nothing was fetched (markets closed), True if at least one fetch
    worked, False if all failed (with the last error).
    """
    if not results:
        return None, None
    if any(ok for ok, _ in results):
        return True, None
    return False, results[-1][1]


def update_health(
    provider: str, previous: ProviderHealth, outcome: bool | None, error: str | None, alert_after: int
) -> tuple[ProviderHealth, HealthEvent | None]:
    """Return the new state and the event to notify, if any.

    ``alerted`` in the new state is left unchanged: the caller flips it only when
    the event message is actually delivered, so a held message is retried.
    """
    if outcome is None:
        return previous, None
    if outcome:
        event = HealthEvent(provider, "recovered", previous.consecutive_failures) if previous.alerted else None
        return ProviderHealth(0, None, previous.alerted), event
    failures = previous.consecutive_failures + 1
    state = ProviderHealth(failures, (error or "")[:MAX_ERROR_CHARS], previous.alerted)
    event = None
    if failures >= alert_after and not previous.alerted:
        event = HealthEvent(provider, "down", failures, state.last_error)
    return state, event


def health_message(events: Sequence[HealthEvent]) -> str:
    lines = []
    for event in events:
        if event.kind == "down":
            lines.append(
                f"⚠️ <b>Market Monitor</b>: il provider {escape(event.provider)} non risponde "
                f"da {event.failures} cicli consecutivi."
            )
            if event.error:
                lines.append(f"Ultimo errore: {escape(event.error)}")
        else:
            lines.append(f"✅ <b>Market Monitor</b>: il provider {escape(event.provider)} funziona di nuovo.")
    return "\n".join(lines)
