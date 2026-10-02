"""Instrument identity and metadata (mirrors honba_messages::InstrumentId, honba_entities::Instrument)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """A symbol on a venue, e.g. ``InstrumentId("NIFTY50", "NSE")``."""

    symbol: str
    venue: str = "NSE"

    def __str__(self) -> str:
        return f"{self.symbol}.{self.venue}"


class InstrumentKind(Enum):
    """The kind of instrument (mirrors ``honba_entities::InstrumentKind``)."""

    EQUITY = "equity"
    FUTURE = "future"
    OPTION = "option"
    FX = "fx"
    INDEX = "index"
    MUTUAL_FUND = "mutual_fund"


@dataclass(frozen=True, slots=True)
class Instrument:
    """Static metadata for a tradable instrument (mirrors ``honba_entities::Instrument``).

    ``lot_size`` is the minimum tradable quantity and ``tick_size`` the minimum price
    increment; both must be finite and > 0. ``currency`` is the ISO settlement currency.
    """

    instrument_id: InstrumentId
    kind: InstrumentKind
    lot_size: float
    tick_size: float
    currency: str = "INR"

    def __post_init__(self) -> None:
        for name in ("lot_size", "tick_size"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"{name} must be positive, got {value}")
