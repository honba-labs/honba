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

# Canonical Nifty 200 Alpha 30 constituent symbols (NSE primary listing)
NIFTY_200_ALPHA_30_SYMBOLS: tuple[str, ...] = (
    "ABCAPITAL",
    "ADANIENSOL",
    "ADANIGREEN",
    "ADANIPOWER",
    "ASHOKLEY",
    "AUBANK",
    "BHARATFORG",
    "BHEL",
    "BSE",
    "CUMMINSIND",
    "EICHERMOT",
    "FEDERALBNK",
    "FORTIS",
    "GLENMARK",
    "GVT&D",
    "HINDALCO",
    "IDEA",
    "INDIANB",
    "LAURUSLABS",
    "LTF",
    "MCX",
    "MUTHOOTFIN",
    "NATIONALUM",
    "NYKAA",
    "PAYTM",
    "POLYCAB",
    "POWERINDIA",
    "SAIL",
    "SHRIRAMFIN",
    "VEDL",
)

UNIVERSES: dict[str, tuple[str, ...]] = {
    "nifty50": NIFTY_50_SYMBOLS,
    "nifty200_alpha30": NIFTY_200_ALPHA_30_SYMBOLS,
}

# optional aliases → canonical key
_ALIASES: dict[str, str] = {
    "nifty_50": "nifty50",
    "nifty_200_alpha_30": "nifty200_alpha30",
    "nifty200_alpha_30": "nifty200_alpha30",
    "nifty200alpha30": "nifty200_alpha30",
    "nifty200_alpha30": "nifty200_alpha30",
    "alpha30": "nifty200_alpha30",
}


def resolve_universe(name: str, venue: str = "NSE") -> list[InstrumentId]:
    norm = name.lower().replace("-", "_").replace(" ", "_")
    key = _ALIASES.get(norm, norm)
    symbols = UNIVERSES.get(key)
    if symbols is None:
        raise ValueError(f"unknown universe {name!r}; available: {sorted(UNIVERSES)}")
    if not symbols:
        raise ValueError(
            f"universe {key!r} is registered but has no constituents "
            "(wire a UniverseSource / catalog snapshot)"
        )
    return [InstrumentId(sym, venue) for sym in symbols]