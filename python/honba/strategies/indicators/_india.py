"""Indian-market conventions shared by indicators."""

from __future__ import annotations

NS_PER_MIN = 60_000_000_000
NS_PER_DAY = 86_400_000_000_000
IST_OFFSET_NS = 5 * 3_600_000_000_000 + 30 * NS_PER_MIN  # UTC+05:30, no DST

TRADING_DAYS_PER_YEAR = 252  # default annualisation for NSE/BSE (not 365)
NSE_SESSION_OPEN_MIN = 9 * 60 + 15  # 09:15 IST
NSE_SESSION_CLOSE_MIN = 15 * 60 + 30  # 15:30 IST


def ist_session_day(ts_ns: int) -> int:
    """IST calendar day number (days since 1970-01-01) of a unix-ns timestamp.

    Session-anchored indicators (VWAP, pivots) reset when this changes.
    """
    return (ts_ns + IST_OFFSET_NS) // NS_PER_DAY


def ist_minute_of_day(ts_ns: int) -> int:
    """Minutes since IST midnight (09:15 -> 555, 15:30 -> 930)."""
    return ((ts_ns + IST_OFFSET_NS) % NS_PER_DAY) // NS_PER_MIN


def hhmm_to_minutes(text: str) -> int:
    """``"15:15"`` -> 915. Raises ValueError on anything but a valid 24h HH:MM."""
    try:
        h, m = text.split(":")
        hh, mm = int(h), int(m)
    except ValueError:
        raise ValueError(f"expected HH:MM, got {text!r}") from None
    if not (0 <= hh < 24 and 0 <= mm < 60 and len(m) == 2):
        raise ValueError(f"expected HH:MM, got {text!r}")
    return hh * 60 + mm
