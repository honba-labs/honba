"""Settlement cycle for Indian markets.

The clearing cycle is a country + exchange property, not a global constant: NSE/BSE
equity delivery settled T+2 until 2023-01-26 and settles T+1 from 2023-01-27, other
markets settle T+1. The schedule is owned by the Rust market pack
(``IndiaMarketProfile::equity_settlement_days_as_of`` in
``crates/honba-market/src/india/profile.rs``) and surfaced through
``honba._honba.nse_equity_settlement_days``; ask for a date with ``as_of``, and leave it
out for today's cycle. When the native extension is not built,
the fallback below mirrors that Rust default so the Python SDK stays usable in a
source checkout without maturin.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Final

__all__ = [
    "INDIA_EXCHANGES",
    "INSTRUMENT_KINDS",
    "nse_equity_settlement_days",
    "settlement_days_for",
    "to_iso_date",
]

try:  # pragma: no cover - the native module is absent in a source-only checkout
    from honba._honba import nse_equity_settlement_days as _native_nse_equity_settlement_days
except ImportError:  # pragma: no cover
    _native_nse_equity_settlement_days = None

# Exchange codes served by the India market pack (`nse_bse`).
INDIA_EXCHANGES: Final[frozenset[str]] = frozenset({"NSE", "BSE"})

# Accepted `kind` values; mirrors the non-index variants of `honba_entities::InstrumentKind`.
INSTRUMENT_KINDS: Final[frozenset[str]] = frozenset(
    {"equity", "etf", "bond", "ipo", "future", "option", "fx", "index", "mf"}
)

# T+1 is the market default everywhere outside India's equity delivery cycle.
_DEFAULT_SETTLEMENT_DAYS: Final[int] = 1


# First date every NSE/BSE equity settles T+1; mirrors the Rust schedule (fallback only).
_FALLBACK_T_PLUS_1_FROM: Final[date] = date(2023, 1, 27)


def to_iso_date(as_of: date | datetime | str) -> str:
    """Normalize ``as_of`` (date, datetime or ISO string) to ``YYYY-MM-DD``."""
    if isinstance(as_of, datetime):
        return as_of.date().isoformat()
    if isinstance(as_of, date):
        return as_of.isoformat()
    return date.fromisoformat(as_of.strip()).isoformat()


def nse_equity_settlement_days(as_of: date | datetime | str | None = None) -> int:
    """Settlement cycle for NSE/BSE equity delivery.

    T+2 before 2023-01-27 and T+1 from then; ``as_of=None`` is the cycle in force today.
    """
    iso = None if as_of is None else to_iso_date(as_of)
    if _native_nse_equity_settlement_days is not None:
        return _native_nse_equity_settlement_days(iso)
    if iso is not None and date.fromisoformat(iso) < _FALLBACK_T_PLUS_1_FROM:
        return 2
    return 1


def settlement_days_for(
    exchange: str, *, kind: str = "equity", as_of: date | datetime | str | None = None
) -> int:
    """Settlement cycle in days for ``exchange`` and instrument ``kind``, as of a date.

    ``exchange`` is a case-insensitive exchange code: Indian venues (NSE, BSE) follow the
    Rust market pack schedule (T+2 before 2023-01-27, T+1 from then; today's cycle when
    ``as_of`` is None), every other venue reports T+1. ``kind`` is one of
    :data:`INSTRUMENT_KINDS`; the India pack models a single delivery cycle per venue, so
    it does not vary by kind yet.

    Raises:
        ValueError: if ``kind`` is not a known instrument kind or ``as_of`` is malformed.
    """
    normalized = kind.strip().lower()
    if normalized not in INSTRUMENT_KINDS:
        raise ValueError(
            f"unknown instrument kind {kind!r}; expected one of {sorted(INSTRUMENT_KINDS)}"
        )
    if exchange.strip().upper() in INDIA_EXCHANGES:
        return nse_equity_settlement_days(as_of)
    return _DEFAULT_SETTLEMENT_DAYS
