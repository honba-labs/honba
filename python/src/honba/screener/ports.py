"""Ports for BarStore and MarketDataProvider, plus bar invariant validation."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from typing import Any, Protocol

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.screener import ScreenerScanRequest, ScreenerScanResponse
from honba.screener.coverage import CoverageRecord, DateInterval


def validate_bar(bar: Bar) -> bool:
    """Validate bar invariants (finite prices/volume, low <= open/close <= high, volume >= 0)."""
    for val, name in (
        (bar.open, "open"),
        (bar.high, "high"),
        (bar.low, "low"),
        (bar.close, "close"),
        (bar.volume, "volume"),
    ):
        if not math.isfinite(val):
            raise ValueError(f"bar {name} must be finite, got {val}")

    if bar.low > bar.high:
        raise ValueError(f"high must be >= low, got high={bar.high}, low={bar.low}")

    if not (bar.low <= bar.open <= bar.high):
        raise ValueError(
            f"open must be between low and high, got open={bar.open}, high={bar.high}, low={bar.low}"
        )

    if not (bar.low <= bar.close <= bar.high):
        raise ValueError(
            f"close must be between low and high, got close={bar.close}, high={bar.high}, low={bar.low}"
        )

    if bar.volume < 0:
        raise ValueError(f"volume must be >= 0, got {bar.volume}")

    return True


class BarStore(Protocol):
    """Port for bar and coverage ledger persistence."""

    def coverage(self, instrument: InstrumentId, timeframe: str) -> list[CoverageRecord]:
        """Return the list of covered intervals for an instrument and timeframe."""
        ...

    def read(self, instrument: InstrumentId, timeframe: str, interval: DateInterval) -> list[Bar]:
        """Read deduplicated bars within the given half-open interval."""
        ...

    def append(self, record: CoverageRecord, bars: Sequence[Bar]) -> None:
        """Atomically append bars and update the coverage record in the ledger."""
        ...


class ScreenerSource(Protocol):
    """Port for executing a screener scan request and returning ScreenerScanResponse."""

    def scan(self, request: ScreenerScanRequest) -> ScreenerScanResponse: ...


class MarketDataProvider(Protocol):
    """Port for fetching market data bars from a provider."""

    @property
    def name(self) -> str: ...

    def fetch(
        self,
        instrument: InstrumentId,
        timeframe: str,
        interval: DateInterval,
        progress_callback: Any = None,
    ) -> list[Bar]:
        """Fetch bars for the instrument and timeframe in [interval.start, interval.end)."""
        ...


class InMemoryBarStore:
    """In-memory implementation of BarStore for testing and offline evaluation."""

    def __init__(self) -> None:
        # Key: (exchange, symbol, timeframe)
        self._coverage: dict[tuple[str, str, str], list[CoverageRecord]] = {}
        # Key: (exchange, symbol, timeframe, ts_event)
        self._bars: dict[tuple[str, str, str, int], Bar] = {}

    def coverage(self, instrument: InstrumentId, timeframe: str) -> list[CoverageRecord]:
        key = (instrument.exchange, instrument.symbol, timeframe)
        return list(self._coverage.get(key, []))

    def read(self, instrument: InstrumentId, timeframe: str, interval: DateInterval) -> list[Bar]:
        key_prefix = (instrument.exchange, instrument.symbol, timeframe)
        # Convert date interval to nanoseconds range [start_ns, end_ns)
        start_ns = int(dt.datetime.combine(interval.start, dt.time.min).timestamp() * 1e9)
        end_ns = int(dt.datetime.combine(interval.end, dt.time.min).timestamp() * 1e9)

        matched = [
            bar
            for (v, s, tf, ts), bar in self._bars.items()
            if (v, s, tf) == key_prefix and start_ns <= ts < end_ns
        ]
        return sorted(matched, key=lambda b: b.ts)

    def append(self, record: CoverageRecord, bars: Sequence[Bar]) -> None:
        for bar in bars:
            validate_bar(bar)
            key = (record.exchange, record.symbol, record.timeframe, bar.ts)
            self._bars[key] = bar

        key_prefix = (record.exchange, record.symbol, record.timeframe)
        records = self._coverage.setdefault(key_prefix, [])
        records.append(record)


class InMemoryMarketDataProvider:
    """In-memory mock provider for testing gap fetching."""

    def __init__(self, name: str = "mock_provider") -> None:
        self._name = name
        # Key: (exchange, symbol, timeframe, ts_event)
        self._bars: dict[tuple[str, str, str, int], Bar] = {}

    @property
    def name(self) -> str:
        return self._name

    def add_bars(self, instrument: InstrumentId, timeframe: str, bars: Sequence[Bar]) -> None:
        for bar in bars:
            key = (instrument.exchange, instrument.symbol, timeframe, bar.ts)
            self._bars[key] = bar

    def fetch(
        self,
        instrument: InstrumentId,
        timeframe: str,
        interval: DateInterval,
        progress_callback: Any = None,
    ) -> list[Bar]:
        start_ns = int(dt.datetime.combine(interval.start, dt.time.min).timestamp() * 1e9)
        end_ns = int(dt.datetime.combine(interval.end, dt.time.min).timestamp() * 1e9)

        matched = [
            bar
            for (v, s, tf, ts), bar in self._bars.items()
            if v == instrument.exchange
            and s == instrument.symbol
            and tf == timeframe
            and start_ns <= ts < end_ns
        ]
        return sorted(matched, key=lambda b: b.ts)
