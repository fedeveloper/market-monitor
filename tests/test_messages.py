from datetime import datetime
from zoneinfo import ZoneInfo

from market_monitor.alerts import Alert
from market_monitor.config import Asset, AssetRules, DayMoveRule, FastMoveRule, OpenGapRule, PriceLevels
from market_monitor.formatting import MINUS
from market_monitor.messages import alert_fragment, alert_message, window_label

ROME = ZoneInfo("Europe/Rome")
NOW = datetime(2026, 10, 28, 14, 30, tzinfo=ROME).timestamp()

STOCK = Asset("ENI.MI", "Eni", "stock", "IT", "yahoo", True, PriceLevels(),
              AssetRules(FastMoveRule(True, 60, 2, 1), DayMoveRule(True, (3, 5, 8), "prev_close", 1), OpenGapRule(True, 2, 45)))
BTC = Asset("BTCEUR", "Bitcoin", "crypto", "CRYPTO", "binance", True, PriceLevels(),
            AssetRules(FastMoveRule(True, 60, 3, 1.5), DayMoveRule(True, (5, 8, 12), "rolling_24h", 1.5), OpenGapRule(False)))
ASSETS = {"ENI.MI": STOCK, "BTCEUR": BTC}


def test_message_matches_docs_example():
    alerts = [
        Alert("ENI.MI", "day_move", "day_move:up:3", 1, 14.52, "EUR", 0, change_pct=3.1, threshold=3),
        Alert("ENI.MI", "fast_move", "fast_move", 1, 14.52, "EUR", 0, change_pct=2.3, threshold=2, window_minutes=60),
        Alert("BTCEUR", "fast_move", "fast_move", -1, 62310.0, "EUR", 0, change_pct=-3.4, threshold=3, window_minutes=60),
        Alert("BTCEUR", "day_move", "day_move:down:5", -1, 62310.0, "EUR", 0, change_pct=-5.2, threshold=5),
    ]
    text = alert_message("Market Monitor", ASSETS, alerts, NOW, ROME)
    assert text == (
        "<b>Market Monitor</b> · 14:30\n"
        "\n"
        "▲ Eni (ENI.MI)  14,52 €\n"
        "   +2,3% in 1 ora · +3,1% oggi (gradino 3%)\n"
        "\n"
        "▼ Bitcoin (BTCEUR)  62.310 €\n"
        f"   {MINUS}3,4% in 1 ora · {MINUS}5,2% in 24 ore (gradino 5%)\n"
        "\n"
        "Dati con ritardo fino a 15 minuti."
    )


def test_message_overflow_and_urgent():
    alerts = [Alert("ENI.MI", "day_move", "day_move:down:8", -1, 13.0, "EUR", 0, change_pct=-8.4, threshold=8, urgent=True)]
    text = alert_message("Market Monitor", ASSETS, alerts, NOW, ROME, more=3)
    assert "‼️ ▼ Eni (ENI.MI)" in text
    assert "+3 altri alert, al prossimo ciclo." in text


def test_fragments():
    gap = Alert("ENI.MI", "open_gap", "open_gap", -1, 13.7, "EUR", 0, change_pct=-2.6, threshold=2)
    assert alert_fragment(STOCK, gap) == f"apertura {MINUS}2,6% sulla chiusura precedente"
    level = Alert("ENI.MI", "level_above", "level_above:16", 1, 16.02, "EUR", 0, threshold=16.0)
    assert alert_fragment(STOCK, level) == "sopra il livello 16,00 €"
    below = Alert("BTCEUR", "level_below", "level_below:50000", -1, 49900, "EUR", 0, threshold=50000)
    assert alert_fragment(BTC, below) == "sotto il livello 50.000 €"


def test_html_escaped_in_names():
    asset = Asset("X", "A&B <Co>", "stock", "IT", "yahoo", True, PriceLevels(), STOCK.rules)
    alert = Alert("X", "day_move", "day_move:up:3", 1, 1.0, "EUR", 0, change_pct=3.0, threshold=3)
    assert "A&amp;B &lt;Co&gt; (X)" in alert_message("T", {"X": asset}, [alert], NOW, ROME)


def test_window_label():
    assert [window_label(m) for m in (60, 120, 30)] == ["1 ora", "2 ore", "30 min"]
