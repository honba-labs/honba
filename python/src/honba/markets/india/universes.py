"""Indian market universe definitions (NIFTY 50, NIFTY Next 50, etc.)."""

from __future__ import annotations

from honba.entities.instrument import InstrumentId

# Canonical Nifty 50 constituent symbols (NSE primary listing)
NIFTY_50_SYMBOLS: tuple[str, ...] = (
    "ADANIENT",
    "ADANIPORTS",
    "APOLLOHOSP",
    "ASIANPAINT",
    "AXISBANK",
    "BAJAJ-AUTO",
    "BAJFINANCE",
    "BAJAJFINSV",
    "BEL",
    "BPCL",
    "BHARTIARTL",
    "BRITANNIA",
    "CIPLA",
    "COALINDIA",
    "DRREDDY",
    "EICHERMOT",
    "GRASIM",
    "HCLTECH",
    "HDFCBANK",
    "HDFCLIFE",
    "HEROMOTOCO",
    "HINDALCO",
    "HINDUNILVR",
    "ICICIBANK",
    "ITC",
    "INDUSINDBK",
    "INFY",
    "JSWSTEEL",
    "KOTAKBANK",
    "LT",
    "M&M",
    "MARUTI",
    "NTPC",
    "NESTLEIND",
    "ONGC",
    "POWERGRID",
    "RELIANCE",
    "SBILIFE",
    "SHRIRAMFIN",
    "SBIN",
    "SUNPHARMA",
    "TCS",
    "TATACONSUM",
    "TATAMOTORS",
    "TATASTEEL",
    "TECHM",
    "TITAN",
    "TRENT",
    "ULTRACEMCO",
    "WIPRO",
)

UNIVERSES: dict[str, tuple[str, ...]] = {
    "nifty50": NIFTY_50_SYMBOLS,
    "nifty_50": NIFTY_50_SYMBOLS,
}


def resolve_universe(name: str, venue: str = "NSE") -> list[InstrumentId]:
    """Resolve a named universe like 'nifty50' to a list of InstrumentIds."""
    norm = name.lower().replace("-", "_").replace(" ", "_")
    symbols = UNIVERSES.get(norm)
    if symbols is None:
        raise ValueError(f"unknown universe {name!r}; available: {list(UNIVERSES)}")
    return [InstrumentId(sym, venue) for sym in symbols]
