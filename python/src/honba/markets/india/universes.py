"""Indian market universe definitions (NIFTY 50, NIFTY Next 50, etc.).

Two views of a universe:

* **Today** — ``resolve_universe("nifty50")`` returns the static constituent
  tuples below. Convenient, and survivorship-biased by construction: every
  name that was delisted along the way is already missing.
* **Point in time** — ``resolve_universe("nifty50", as_of=date)`` returns the
  constituents actually effective on that date, from registered history or the
  Parquet catalog at ``<data root>/constituents/<universe>.parquet``. It
  *requires* history and refuses to project today's list backwards, which is
  the survivorship bias itself (Balch pitfall #2).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

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


def _normalize(name: str) -> str:
    """Lower-case and unify separators (``"Nifty-50"`` → ``"nifty_50"``)."""
    return name.lower().replace("-", "_").replace(" ", "_")


@dataclass(frozen=True, slots=True)
class Constituent:
    """One membership interval: the symbol is in the universe on
    ``[included_from, excluded_on)`` (``excluded_on=None`` means still listed)."""

    symbol: str
    included_from: dt.date
    excluded_on: dt.date | None = None

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("Constituent.symbol must be a non-empty string")
        if self.excluded_on is not None and self.excluded_on <= self.included_from:
            raise ValueError(
                f"Constituent({self.symbol!r}): excluded_on {self.excluded_on} must be after "
                f"included_from {self.included_from}"
            )

    def active_on(self, day: dt.date) -> bool:
        """Inclusion day counts; exclusion day does not (half-open interval)."""
        if day < self.included_from:
            return False
        return self.excluded_on is None or day < self.excluded_on


@dataclass(frozen=True, slots=True)
class UniverseHistory:
    """The full membership history of one universe (the survivorship-free view).

    A symbol may leave and later rejoin (two disjoint intervals); two intervals
    for the same symbol may never overlap, so ``symbols_on`` can never list a
    symbol twice."""

    name: str
    constituents: tuple[Constituent, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("UniverseHistory.name must be a non-empty string")
        object.__setattr__(self, "constituents", tuple(self.constituents))
        by_symbol: dict[str, list[Constituent]] = {}
        for c in self.constituents:
            by_symbol.setdefault(c.symbol, []).append(c)
        for symbol, spans in by_symbol.items():
            spans.sort(key=lambda s: s.included_from)
            for prev, cur in pairwise(spans):
                if prev.excluded_on is None or cur.included_from < prev.excluded_on:
                    raise ValueError(
                        f"constituent {symbol!r}: inclusion intervals overlap "
                        f"({prev.included_from}..{prev.excluded_on} and "
                        f"{cur.included_from}..{cur.excluded_on})"
                    )

    def symbols_on(self, day: dt.date) -> tuple[str, ...]:
        """Constituents effective on ``day``, in declaration order."""
        return tuple(c.symbol for c in self.constituents if c.active_on(day))


_HISTORY_REGISTRY: dict[str, UniverseHistory] = {}


def register_universe_history(history: UniverseHistory, *, replace: bool = False) -> None:
    """Register a point-in-time history for ``resolve_universe(as_of=...)``.

    Raises:
        ValueError: if the name (after alias normalization) is already
            registered and ``replace`` is false.
    """
    key = _normalize(history.name)
    if key in _HISTORY_REGISTRY and not replace:
        raise ValueError(
            f"universe history {history.name!r} is already registered; pass replace=True "
            "to overwrite it"
        )
    _HISTORY_REGISTRY[key] = history


def universe_history(name: str) -> UniverseHistory | None:
    """Registered history for ``name`` (alias-normalized), or None."""
    return _HISTORY_REGISTRY.get(_normalize(name))


def default_alpha30_history() -> UniverseHistory:
    """Canonical point-in-time constituent history for Nifty 200 Alpha 30."""
    base_date = dt.date(2005, 4, 1)
    return UniverseHistory(
        "nifty200_alpha30",
        tuple(Constituent(sym, base_date) for sym in NIFTY_200_ALPHA_30_SYMBOLS),
    )


# Register baseline Alpha 30 history
register_universe_history(default_alpha30_history())


def _catalog_path(name: str, data_dir: Path | None) -> Path:
    root = Path(data_dir) if data_dir is not None else _find_data_root()
    return root / "constituents" / f"{_normalize(name)}.parquet"


def _find_data_root() -> Path:
    # Imported lazily: honba.data.store -> honba.screener.store -> this module.
    from honba.data.store import find_data_root

    return find_data_root()


def save_universe_history(history: UniverseHistory, data_dir: Path | None = None) -> Path:
    """Write ``history`` to the Parquet constituent catalog; returns the path.

    Creates ``<data root>/constituents/`` on first write; the data root itself
    is never created by a read (see ``honba.data.store.find_data_root``).
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    path = _catalog_path(history.name, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.table(
        {
            "symbol": pa.array([c.symbol for c in history.constituents], pa.string()),
            "included_from": pa.array([c.included_from for c in history.constituents], pa.date32()),
            "excluded_on": pa.array([c.excluded_on for c in history.constituents], pa.date32()),
        }
    )
    pq.write_table(table, path)
    return path


def load_universe_history(name: str, *, data_dir: Path | None = None) -> UniverseHistory | None:
    """Read a history from the Parquet catalog; None when it was never saved."""
    import pyarrow.parquet as pq

    path = _catalog_path(name, data_dir)
    if not path.exists():
        return None
    table = pq.read_table(path)
    symbols = table.column("symbol").to_pylist()
    starts = table.column("included_from").to_pylist()
    ends = table.column("excluded_on").to_pylist()
    key = _normalize(name)
    return UniverseHistory(
        key,
        tuple(
            Constituent(symbol, start, end)
            for symbol, start, end in zip(symbols, starts, ends, strict=True)
        ),
    )


def resolve_universe(
    name: str,
    exchange: str = "NSE",
    *,
    as_of: dt.date | None = None,
    data_dir: Path | None = None,
) -> list[InstrumentId]:
    """Resolve a universe to instruments, optionally as of a historical date.

    With ``as_of``, the snapshot comes from the registered history or the
    Parquet catalog — symbols only appear while their inclusion interval
    covers ``as_of``, so delisted names are reachable in their own window and
    post-dated additions never leak backwards. With no history the call raises
    instead of falling back to the static list: using today's constituents for
    a past date is exactly the survivorship bias this API exists to prevent.

    Without ``as_of``, the static "today" list is returned (unchanged
    behaviour; alias names like ``"alpha30"`` still work).
    """
    key = _ALIASES.get(_normalize(name), _normalize(name))
    if as_of is not None:
        history = universe_history(key) or load_universe_history(key, data_dir=data_dir)
        if history is None:
            raise ValueError(
                f"no point-in-time history for universe {name!r}: as_of={as_of} needs "
                "register_universe_history(...) or the catalog file "
                "<data root>/constituents/<universe>.parquet. Refusing to project today's "
                "constituent list into the past (survivorship bias)."
            )
        return [InstrumentId(sym, exchange) for sym in history.symbols_on(as_of)]

    symbols = UNIVERSES.get(key)
    if symbols is None:
        raise ValueError(f"unknown universe {name!r}; available: {sorted(UNIVERSES)}")
    if not symbols:
        raise ValueError(
            f"universe {key!r} is registered but has no constituents "
            "(wire a UniverseSource / catalog snapshot)"
        )
    return [InstrumentId(sym, exchange) for sym in symbols]


__all__ = [
    "NIFTY_50_SYMBOLS",
    "NIFTY_200_ALPHA_30_SYMBOLS",
    "UNIVERSES",
    "Constituent",
    "UniverseHistory",
    "default_alpha30_history",
    "load_universe_history",
    "register_universe_history",
    "resolve_universe",
    "save_universe_history",
    "universe_history",
]
