"""Instrument identity (mirrors honba_messages::InstrumentId)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """A symbol on a venue, e.g. ``InstrumentId("NIFTY50", "NSE")``."""

    symbol: str
    venue: str = "NSE"

    def __str__(self) -> str:
        return f"{self.symbol}.{self.venue}"
