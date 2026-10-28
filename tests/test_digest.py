from datetime import datetime, time
from zoneinfo import ZoneInfo

from market_monitor.config import Asset, AssetRules, DayMoveRule, DigestConfig, FastMoveRule, OpenGapRule, PriceLevels
from market_monitor.digest import DigestData, build_digest, digest_due
from market_monitor.formatting import MINUS
from market_monitor.models import Quote
from market_monitor.storage import ProviderHealth, SentAlert

ROME = ZoneInfo("Europe/Rome")
CFG = DigestConfig(True, time(18, 0), ROME, True, 3)
STOCK_RULES = AssetRules(FastMoveRule(True, 60, 2, 1), DayMoveRule(True, (3, 5, 8), "prev_close", 1), OpenGapRule(True, 2, 45))
CRYPTO_RULES = AssetRules(FastMoveRule(True, 60, 3, 1.5), DayMoveRule(True, (5, 8, 12), "rolling_24h", 1.5), OpenGapRule(False))


def at(day, h, m=0):
    return datetime(2026, 10, day, h, m, tzinfo=ROME).timestamp()


def stock(symbol, name, cls="stock"):
    return Asset(symbol, name, cls, "IT", "yahoo", True, PriceLevels(), STOCK_RULES)


ENI, ENEL, SWDA = stock("ENI.MI", "Eni"), stock("ENEL.MI", "Enel"), stock("SWDA.MI", "iShares World", "etf")
BTC = Asset("BTCEUR", "Bitcoin", "crypto", "CRYPTO", "binance", True, PriceLevels(), CRYPTO_RULES)


def q(symbol, price, prev, ts):
    return Quote(symbol, int(ts), price, prev_close=prev, currency="EUR")


def data(ts, alerts=(), health=None):
    return DigestData(
        rows=[
            (ENI, q("ENI.MI", 14.52, 14.08, ts)),
            (ENEL, q("ENEL.MI", 6.9, 7.0, ts)),
            (SWDA, None),
            (BTC, q("BTCEUR", 62310, 65728, ts)),
        ],
        alerts_today=list(alerts),
        health=health or {"yahoo": ProviderHealth(), "binance": ProviderHealth()},
    )


def test_digest_due_once_per_day():
    assert digest_due(at(28, 17, 59), CFG, None) is None
    assert digest_due(at(28, 18, 0), CFG, None) == "2026-10-28"
    assert digest_due(at(28, 18, 15), CFG, "2026-10-28") is None
    assert digest_due(at(28, 21, 0), CFG, "2026-10-27") == "2026-10-28"  # late, still within the window
    assert digest_due(at(28, 23, 0), CFG, "2026-10-27") is None  # too late: skipped
    assert digest_due(at(28, 18, 0), DigestConfig(False, time(18, 0), ROME, True, 3), None) is None


def test_weekday_digest_content():
    now = at(28, 18)
    alerts = [SentAlert(int(now) - 3600, "ENI.MI", "level_above", "sopra il livello 14,50 €"),
              SentAlert(int(now) - 7200, "ENI.MI", "day_move", "+3,1% oggi (gradino 3%)")]
    text = build_digest(data(now - 1800, alerts), now, CFG)
    assert text.startswith("<b>Market Monitor · Riepilogo</b> · mercoledì 28/10 18:00")
    assert "<b>Azioni</b>\n▲ Eni (ENI.MI)  14,52 €  +3,1% oggi\n▼ Enel (ENEL.MI)  6,90 €  " in text
    assert "• iShares World (SWDA.MI)  nessun dato" in text
    assert f"▼ Bitcoin (BTCEUR)  62.310 €  {MINUS}5,2% in 24 ore" in text
    assert "Maggiori rialzi: Eni +3,1%" in text
    assert f"Maggiori ribassi: Bitcoin {MINUS}5,2%, Enel {MINUS}1,4%" in text
    assert "<b>Livelli raggiunti oggi</b>\n• Eni: sopra il livello 14,50 €" in text
    assert "Alert inviati oggi: 2" in text
    assert "Stato: tutti i provider funzionano." in text


def test_weekend_digest_crypto_only():
    now = at(31, 18)  # Saturday
    text = build_digest(data(now), now, CFG)
    assert "Weekend: solo crypto." in text
    assert "Eni" not in text and "Bitcoin" in text


def test_old_quote_is_dated_and_provider_problems_shown():
    now = at(28, 18)
    health = {"yahoo": ProviderHealth(4, "HTTP 429", True), "binance": ProviderHealth()}
    text = build_digest(data(at(27, 17, 30), health=health), now, CFG)
    assert "(dato del 27/10)" in text
    assert "Stato: yahoo in errore da 4 cicli (HTTP 429)" in text
