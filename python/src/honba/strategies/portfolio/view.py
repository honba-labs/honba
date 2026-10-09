"""Read-only market snapshot handed to selectors and weighting schemes."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from honba.entities.instrument import InstrumentId


def _frozen(mapping: Mapping) -> Mapping:
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True, slots=True)
class MarketView:
    """What the strategy knows at decision time (event-time data only).

    ``last_prices`` maps instrument -> latest close seen. ``closes`` maps instrument ->
    the most recent closes, oldest first, bounded by the strategy's ``history_len``.
    Both mappings are read-only.
    """

    last_prices: Mapping[InstrumentId, float]
    closes: Mapping[InstrumentId, tuple[float, ...]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "last_prices", _frozen(self.last_prices))
        object.__setattr__(self, "closes", _frozen(self.closes))

    def price(self, instrument_id: InstrumentId) -> float | None:
        """Latest close, or ``None`` if the instrument has not been priced yet."""
        return self.last_prices.get(instrument_id)

    def recent_closes(self, instrument_id: InstrumentId) -> tuple[float, ...]:
        """Recent closes, oldest first (empty if unseen)."""
        return self.closes.get(instrument_id, ())


class PriceHistory:
    """Per-instrument ring buffer of the last ``maxlen`` closes; builds ``MarketView`` s."""

    def __init__(self, maxlen: int = 64) -> None:
        if maxlen < 1:
            raise ValueError(f"maxlen must be >= 1, got {maxlen}")
        self.maxlen = maxlen
        self._closes: dict[InstrumentId, deque[float]] = {}

    def record(self, instrument_id: InstrumentId, close: float) -> None:
        """Append a close, dropping the oldest beyond ``maxlen``."""
        buf = self._closes.get(instrument_id)
        if buf is None:
            buf = self._closes[instrument_id] = deque(maxlen=self.maxlen)
        buf.append(float(close))

    def view(self, last_prices: Mapping[InstrumentId, float]) -> MarketView:
        """Immutable snapshot (later ``record`` calls do not affect it)."""
        return MarketView(last_prices, {i: tuple(b) for i, b in self._closes.items()})
