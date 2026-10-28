from market_monitor.health import HealthEvent, health_message, provider_outcome, update_health
from market_monitor.storage import ProviderHealth


def test_provider_outcome():
    assert provider_outcome([]) == (None, None)
    assert provider_outcome([(False, "a"), (True, None)]) == (True, None)
    assert provider_outcome([(False, "a"), (False, "b")]) == (False, "b")


def run(outcomes, alert_after=3, deliver=True):
    state = ProviderHealth()
    events = []
    for ok in outcomes:
        state, event = update_health("yahoo", state, ok, None if ok else "HTTP 429", alert_after)
        if event:
            events.append(event.kind)
            if deliver:
                state = ProviderHealth(state.consecutive_failures, state.last_error, event.kind == "down")
    return state, events


def test_one_error_message_after_threshold_and_no_repeats():
    state, events = run([False, False, False, False, False])
    assert events == ["down"]
    assert state.consecutive_failures == 5 and state.alerted


def test_one_recovery_message():
    state, events = run([False, False, False, True, True])
    assert events == ["down", "recovered"]
    assert state == ProviderHealth(0, None, False)


def test_recovery_before_threshold_is_silent():
    _, events = run([False, False, True, False, False])
    assert events == []


def test_closed_markets_do_not_count():
    _, events = run([False, None, False, None, False])
    assert events == ["down"]


def test_undelivered_event_is_retried():
    state = ProviderHealth(2, "x", False)
    state, event = update_health("yahoo", state, False, "HTTP 500", 3)
    assert event.kind == "down"
    # Not delivered (quiet hours): alerted stays False, so the next failure retries.
    state, event = update_health("yahoo", state, False, "HTTP 500", 3)
    assert event is not None and event.failures == 4


def test_error_truncated():
    state, _ = update_health("yahoo", ProviderHealth(), False, "x" * 500, 3)
    assert len(state.last_error) == 200


def test_health_message():
    text = health_message([HealthEvent("yahoo", "down", 3, "HTTP 429 <html>"), HealthEvent("binance", "recovered", 4)])
    assert "il provider yahoo non risponde da 3 cicli consecutivi" in text
    assert "Ultimo errore: HTTP 429 &lt;html&gt;" in text
    assert "il provider binance funziona di nuovo" in text
