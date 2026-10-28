"""Italian number formatting written by hand (no dependency on the system locale)."""

from __future__ import annotations

import html

MINUS = "−"  # typographic minus, as in the message examples

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥"}


def format_number(value: float, decimals: int = 2) -> str:
    """1234567.891 -> '1.234.567,89'. Negative numbers use '-'."""
    text = f"{abs(value):,.{decimals}f}"
    text = text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"-{text}" if value < 0 and text.strip("0.,") else text


def price_decimals(value: float) -> int:
    magnitude = abs(value)
    if magnitude >= 1000:
        return 0
    if magnitude >= 1:
        return 2
    if magnitude >= 0.01:
        return 4
    return 6


def format_price(value: float, currency: str | None = None) -> str:
    """14.52, 'EUR' -> '14,52 €'; 62310.4 -> '62.310 €'."""
    text = format_number(value, price_decimals(value))
    if not currency:
        return text
    return f"{text} {CURRENCY_SYMBOLS.get(currency, currency)}"


def format_pct(value: float, decimals: int = 1, signed: bool = True) -> str:
    """2.345 -> '+2,3%'; -3.4 -> '−3,4%'; 3 (unsigned) -> '3%' when it is a whole number."""
    if not signed:
        if float(value).is_integer():
            return f"{int(value)}%"
        return f"{format_number(value, decimals)}%"
    rounded = round(value, decimals)
    text = format_number(abs(rounded), decimals)
    if rounded > 0:
        return f"+{text}%"
    if rounded < 0:
        return f"{MINUS}{text}%"
    return f"{text}%"


def escape(text: str) -> str:
    """Escape text for Telegram parse_mode=HTML (only &, <, > need escaping)."""
    return html.escape(str(text), quote=False)
