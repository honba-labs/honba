"""Indian F&O (Futures & Options) symbol grammar and resolution."""

from __future__ import annotations

import datetime

from honba._honba import FnoContract as NativeFnoContract
from honba._honba import parse_fno_symbol as _native_parse_fno_symbol
from honba.domain.instrument import Instrument, InstrumentId, InstrumentKind, OptionType


def parse_fno_symbol(symbol: str) -> NativeFnoContract:
    """Parse an Indian F&O contract ticker symbol into structured contract metadata."""
    return _native_parse_fno_symbol(symbol)


def resolve_fno_instrument(symbol: str, exchange: str = "NSE") -> Instrument:
    """Resolve an F&O ticker symbol into a populated domain Instrument."""
    parsed = parse_fno_symbol(symbol)
    kind = InstrumentKind.FUTURE if parsed.kind == "future" else InstrumentKind.OPTION
    opt_type = OptionType.from_code(parsed.option_type) if parsed.option_type else None

    # Calculate expiry date
    if parsed.expiry_day is not None:
        expiry_date = datetime.date(parsed.expiry_year, parsed.expiry_month, parsed.expiry_day)
    else:
        # Last Thursday of month
        year = parsed.expiry_year
        month = parsed.expiry_month
        next_month = 1 if month == 12 else month + 1
        next_year = year + 1 if month == 12 else year
        d = datetime.date(next_year, next_month, 1) - datetime.timedelta(days=1)
        while d.weekday() != 3:
            d -= datetime.timedelta(days=1)
        expiry_date = d

    u = parsed.underlying.upper()
    lot_size = (
        25.0
        if u in ("NIFTY", "FINNIFTY")
        else (15.0 if u == "BANKNIFTY" else (50.0 if u == "MIDCPNIFTY" else 1.0))
    )

    return Instrument(
        instrument_id=InstrumentId(parsed.symbol, exchange),
        kind=kind,
        lot_size=lot_size,
        tick_size=0.05,
        currency="INR",
        expiry=expiry_date,
        strike=parsed.strike,
        option_type=opt_type,
        underlying=parsed.underlying,
    )
