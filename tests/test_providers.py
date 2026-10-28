import pytest
import requests

from conftest import FakeResponse, FakeSession, fixture_json
from market_monitor.config import Asset, AssetRules, DayMoveRule, FastMoveRule, OpenGapRule, PriceLevels, ProviderConfig
from market_monitor.providers.base import HttpClient, HttpStatusError, ProviderError, to_float
from market_monitor.providers.binance import BinanceProvider, pair_currency, parse_ticker_24hr
from market_monitor.providers.yahoo import YahooChartProvider, parse_chart

RULES = AssetRules(FastMoveRule(False), DayMoveRule(False), OpenGapRule(False))


def make_asset(symbol, provider="yahoo", asset_class="stock", market="IT"):
    return Asset(symbol, symbol, asset_class, market, provider, True, PriceLevels(), RULES)


def make_client(session, retries=2, delay_ms=0, sleeps=None, name="yahoo"):
    cfg = ProviderConfig(name, "https://example.test", 5, retries, delay_ms, "market-monitor-test")
    recorded = sleeps if sleeps is not None else []
    return HttpClient(cfg, session=session, sleep=recorded.append, clock=lambda: 0.0)


# --- Yahoo -------------------------------------------------------------------


def test_parse_yahoo_chart():
    quote = parse_chart("ENI.MI", fixture_json("yahoo_chart_ENI.MI.json"))
    assert quote.symbol == "ENI.MI"
    assert quote.price == 14.52
    assert quote.prev_close == 14.08
    assert quote.day_open == 14.12
    assert quote.currency == "EUR"
    assert quote.volume == 3359600
    assert quote.ts == 1793174400 + 11 * 900 + 300
    assert quote.change_pct == pytest.approx(3.125)


def test_parse_yahoo_falls_back_to_last_non_null_candle():
    data = fixture_json("yahoo_chart_ENI.MI.json")
    meta = data["chart"]["result"][0]["meta"]
    del meta["regularMarketPrice"], meta["previousClose"]
    data["chart"]["result"][0]["indicators"]["quote"][0]["close"][-1] = None
    quote = parse_chart("ENI.MI", data)
    assert quote.price == 14.49
    assert quote.ts == 1793174400 + 10 * 900
    assert quote.prev_close == 14.08  # chartPreviousClose


def test_parse_yahoo_all_null():
    data = fixture_json("yahoo_chart_ENI.MI.json")
    result = data["chart"]["result"][0]
    del result["meta"]["regularMarketPrice"]
    q = result["indicators"]["quote"][0]
    q["close"] = [None] * len(q["close"])
    with pytest.raises(ProviderError, match="no price"):
        parse_chart("ENI.MI", data)


@pytest.mark.parametrize("data", [{}, None, {"chart": {"result": [], "error": None}}])
def test_parse_yahoo_malformed(data):
    with pytest.raises(ProviderError):
        parse_chart("X", data)


def test_yahoo_unknown_symbol_404():
    session = FakeSession(FakeResponse(404, fixture_json("yahoo_chart_not_found.json")))
    provider = YahooChartProvider(make_client(session))
    with pytest.raises(ProviderError, match="symbol may be delisted"):
        provider.fetch(make_asset("NOPE.MI"))


def test_yahoo_request_shape():
    session = FakeSession(FakeResponse(200, fixture_json("yahoo_chart_ENI.MI.json")))
    YahooChartProvider(make_client(session)).fetch(make_asset("ENI.MI"))
    method, url, kwargs = session.calls[0]
    assert url == "https://example.test/v8/finance/chart/ENI.MI"
    assert kwargs["params"] == {"interval": "15m", "range": "1d"}
    assert kwargs["timeout"] == 5
    assert session.headers["User-Agent"] == "market-monitor-test"


# --- Binance -----------------------------------------------------------------


def test_parse_binance_ticker():
    data = fixture_json("binance_ticker_24hr_BTCEUR.json")
    quote = parse_ticker_24hr("BTCEUR", data)
    assert quote.price == float(data["lastPrice"])
    assert quote.prev_close == float(data["openPrice"])
    assert quote.ts == data["closeTime"] // 1000
    assert quote.currency == "EUR"
    assert quote.day_open is None
    assert round(quote.change_pct, 2) == round(float(data["priceChangePercent"]), 2)


def test_binance_invalid_symbol():
    session = FakeSession(FakeResponse(400, {"code": -1121, "msg": "Invalid symbol."}))
    provider = BinanceProvider(make_client(session, name="binance"))
    with pytest.raises(ProviderError, match="Invalid symbol"):
        provider.fetch(make_asset("FOOBAR", "binance", "crypto", "CRYPTO"))
    assert len(session.calls) == 1  # 4xx is not retried


def test_parse_binance_missing_price():
    with pytest.raises(ProviderError):
        parse_ticker_24hr("BTCEUR", {"lastPrice": None, "closeTime": 1})


@pytest.mark.parametrize("symbol,currency", [("BTCEUR", "EUR"), ("ETHUSDT", "USDT"), ("SOLBTC", "BTC"), ("EUR", None)])
def test_pair_currency(symbol, currency):
    assert pair_currency(symbol) == currency


# --- HTTP client ---------------------------------------------------------------


def test_retry_on_5xx_then_success():
    sleeps = []
    session = FakeSession(FakeResponse(502, None, text="bad gateway"), FakeResponse(200, {"ok": 1}))
    assert make_client(session, sleeps=sleeps).get_json("/x") == {"ok": 1}
    assert sleeps == [1.0]


def test_retry_on_network_error_with_backoff():
    sleeps = []
    session = FakeSession(requests.ConnectionError("down"), requests.Timeout("slow"), FakeResponse(200, [1]))
    assert make_client(session, sleeps=sleeps).get_json("/x") == [1]
    assert sleeps == [1.0, 2.0]


def test_network_error_exhausts_retries():
    session = FakeSession(*[requests.ConnectionError("down")] * 3)
    with pytest.raises(ProviderError, match="network error"):
        make_client(session).get_json("/x")
    assert len(session.calls) == 3


def test_429_honours_retry_after_capped():
    sleeps = []
    session = FakeSession(
        FakeResponse(429, None, headers={"Retry-After": "7"}),
        FakeResponse(429, None, headers={"Retry-After": "999"}),
        FakeResponse(200, {}),
    )
    make_client(session, sleeps=sleeps).get_json("/x")
    assert sleeps == [7.0, 30.0]


def test_429_on_last_attempt_raises():
    session = FakeSession(FakeResponse(429, None), FakeResponse(429, None))
    with pytest.raises(HttpStatusError) as exc:
        make_client(session, retries=1).get_json("/x")
    assert exc.value.status == 429


def test_invalid_json():
    session = FakeSession(FakeResponse(200, None, text="<html>"))
    with pytest.raises(ProviderError, match="invalid JSON"):
        make_client(session).get_json("/x")


def test_delay_between_requests():
    sleeps = []
    cfg = ProviderConfig("p", "https://example.test", 5, 0, 300, None)
    times = iter([0.0, 0.1, 0.4])
    client = HttpClient(cfg, session=FakeSession(FakeResponse(200, {}), FakeResponse(200, {})),
                        sleep=sleeps.append, clock=lambda: next(times))
    client.get_json("/a")
    client.get_json("/b")
    assert sleeps == [pytest.approx(0.2)]


@pytest.mark.parametrize("value,expected", [(None, None), ("1.5", 1.5), (2, 2.0), ("x", None), (True, None), (float("nan"), None)])
def test_to_float(value, expected):
    assert to_float(value) == expected
