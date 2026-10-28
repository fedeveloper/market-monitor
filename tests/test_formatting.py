import pytest

from market_monitor.formatting import MINUS, escape, format_number, format_pct, format_price


@pytest.mark.parametrize(
    "value,decimals,expected",
    [
        (0, 2, "0,00"),
        (14.52, 2, "14,52"),
        (1234567.891, 2, "1.234.567,89"),
        (62310.4, 0, "62.310"),
        (999.995, 2, "1.000,00"),
        (-1234.5, 1, "-1.234,5"),
        (-0.001, 2, "0,00"),
    ],
)
def test_format_number(value, decimals, expected):
    assert format_number(value, decimals) == expected


@pytest.mark.parametrize(
    "value,currency,expected",
    [
        (14.52, "EUR", "14,52 €"),
        (62310.4, "EUR", "62.310 €"),
        (190.123, "USD", "190,12 $"),
        (0.5432, "USDT", "0,5432 USDT"),
        (0.000123, None, "0,000123"),
    ],
)
def test_format_price(value, currency, expected):
    assert format_price(value, currency) == expected


@pytest.mark.parametrize(
    "value,expected",
    [(2.345, "+2,3%"), (-3.4, f"{MINUS}3,4%"), (0.04, "0,0%"), (-0.04, "0,0%"), (12, "+12,0%")],
)
def test_format_pct_signed(value, expected):
    assert format_pct(value) == expected


def test_format_pct_unsigned():
    assert format_pct(3, signed=False) == "3%"
    assert format_pct(1.2, signed=False) == "1,2%"


def test_escape():
    assert escape("A&B <b>") == "A&amp;B &lt;b&gt;"
