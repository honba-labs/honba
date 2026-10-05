"""Settlement cycle for Indian markets.

The clearing cycle is a country + exchange property, not a global constant: NSE/BSE
equity delivery settles T+2, other markets settle T+1. The value is owned by the
Rust market pack (``IndiaMarketProfile::equity_settlement_days`` in
``crates/honba-market/src/india/profile.rs``) and surfaced through
``honba._honba.nse_equity_settlement_days``. When the native extension is not built,
the fallback below mirrors that Rust default so the Python SDK stays usable in a
source checkout without maturin.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "INDIA_EXCHANGES",
    "INSTRUMENT_KINDS",
    "nse_equity_settlement_days",
    "settlement_days_for",
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


def nse_equity_settlement_days() -> int:
    """Settlement cycle for NSE/BSE equity delivery (T+2)."""
    if _native_nse_equity_settlement_days is not None:
        return _native_nse_equity_settlement_days()
    return 2


def settlement_days_for(exchange: str, *, kind: str = "equity") -> int:
    """Settlement cycle in days for ``exchange`` and instrument ``kind``.

    ``exchange`` is a case-insensitive exchange code: Indian venues (NSE, BSE) report
    the T+2 equity delivery cycle from the Rust market pack, every other venue reports
    T+1. ``kind`` is one of :data:`INSTRUMENT_KINDS`; the India pack currently models a
    single delivery cycle per venue, so it does not vary by kind yet.

    Raises:
        ValueError: if ``kind`` is not a known instrument kind.
    """
    normalized = kind.strip().lower()
    if normalized not in INSTRUMENT_KINDS:
        raise ValueError(
            f"unknown instrument kind {kind!r}; expected one of {sorted(INSTRUMENT_KINDS)}"
        )
    if exchange.strip().upper() in INDIA_EXCHANGES:
        return nse_equity_settlement_days()
    return _DEFAULT_SETTLEMENT_DAYS
