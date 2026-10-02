"""OHLCV bar (mirrors honba_messages::Bar)."""

from __future__ import annotations

from dataclasses import dataclass

from honba.domain.instrument import InstrumentId


@dataclass(frozen=True, slots=True)
class Bar:
    instrument_id: InstrumentId
    ts: int  # unix nanoseconds
    open: float
    high: float
    low: float
    close: float
    volume: float
