"""Formatting utilities for different locales."""
from __future__ import annotations
from typing import Any

CURRENCY_SYMBOLS = {
    "INR": "₹",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "CNY": "¥",
    "AUD": "A$",
    "CAD": "C$",
    "SGD": "S$",
}


def format_currency(amount: Any, currency: str = "INR") -> str:
    """Format currency amount based on currency code."""
    try:
        f = float(amount)
    except Exception:
        return str(amount)
    c = currency.upper()
    sym = CURRENCY_SYMBOLS.get(c, c + " ")
    if c == "INR":
        if f < 0:
            sign = "-"
            f_abs = abs(f)
        else:
            sign = ""
            f_abs = f
        if f_abs >= 10_000_000:
            return f"{sign}{sym}{f_abs / 10_000_000:.2f} Cr"
        if f_abs >= 100_000:
            return f"{sign}{sym}{f_abs / 100_000:.2f}L"
        if f_abs >= 1_000:
            return f"{sign}{sym}{f_abs:,.0f}"
        return f"{sign}{sym}{f_abs:.2f}"
    else:
        if f < 0:
            sign = "-"
            f_abs = abs(f)
        else:
            sign = ""
            f_abs = f
        if f_abs >= 1_000_000_000:
            return f"{sign}{sym}{f_abs / 1_000_000_000:.2f}B"
        if f_abs >= 1_000_000:
            return f"{sign}{sym}{f_abs / 1_000_000:.2f}M"
        if f_abs >= 1_000:
            return f"{sign}{sym}{f_abs:,.2f}"
        return f"{sign}{sym}{f_abs:.2f}"


def format_inr(amount: Any) -> str:
    return format_currency(amount, "INR")


def format_usd(amount: Any) -> str:
    return format_currency(amount, "USD")


def format_eur(amount: Any) -> str:
    return format_currency(amount, "EUR")


def format_date_indian(dt: Any) -> str:
    """Format date in Indian style DD-MM-YYYY."""
    try:
        from datetime import datetime, date
        if isinstance(dt, (datetime, date)):
            return dt.strftime("%d-%m-%Y")
        if hasattr(dt, "strftime"):
            return dt.strftime("%d-%m-%Y")
        if isinstance(dt, str):
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
                try:
                    return datetime.strptime(dt, fmt).strftime("%d-%m-%Y")
                except Exception:
                    pass
        return str(dt)
    except Exception:
        return str(dt)


def format_date_us(dt: Any) -> str:
    """Format date in US style MM-DD-YYYY."""
    try:
        from datetime import datetime, date
        if isinstance(dt, (datetime, date)):
            return dt.strftime("%m-%d-%Y")
        if hasattr(dt, "strftime"):
            return dt.strftime("%m-%d-%Y")
        if isinstance(dt, str):
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
                try:
                    return datetime.strptime(dt, fmt).strftime("%m-%d-%Y")
                except Exception:
                    pass
        return str(dt)
    except Exception:
        return str(dt)


def format_date_iso(dt: Any) -> str:
    try:
        from datetime import datetime, date
        if isinstance(dt, (datetime, date)):
            return dt.strftime("%Y-%m-%d")
        return str(dt)
    except Exception:
        return str(dt)
