"""Selection port: narrow the universe to the names to hold."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Protocol, runtime_checkable

from honba.entities.instrument import InstrumentId
from honba.strategies.portfolio.view import MarketView


@runtime_checkable
class Selector(Protocol):
    """Pick a deterministic subset (any order) of ``members`` using ``view``."""

    def select(
        self, members: Sequence[InstrumentId], view: MarketView
    ) -> Sequence[InstrumentId]: ...


class SelectAll:
    """Keep every member."""

    def select(self, members: Sequence[InstrumentId], view: MarketView) -> Sequence[InstrumentId]:
        return list(members)


class TopN:
    """Keep the ``n`` best-scoring members.

    ``score(instrument_id, view)`` returns a float or ``None``; ``None`` and NaN scores are
    dropped (so fewer than ``n`` may be returned). ``descending=True`` keeps the highest
    scores, ``False`` the lowest. Ties break by ``(symbol, exchange)`` ascending, so the
    result is deterministic whatever the input order. See ``scoring`` for ready-made scores.
    """

    def __init__(
        self,
        n: int,
        score: Callable[[InstrumentId, MarketView], float | None],
        descending: bool = True,
    ) -> None:
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError(f"n must be an int >= 1, got {n!r}")
        self.n = n
        self.score = score
        self.descending = descending

    def select(self, members: Sequence[InstrumentId], view: MarketView) -> Sequence[InstrumentId]:
        sign = -1.0 if self.descending else 1.0
        ranked: list[tuple[float, str, str, InstrumentId]] = []
        for iid in dict.fromkeys(members):
            value = self.score(iid, view)
            if value is None or math.isnan(value):
                continue
            ranked.append((sign * value, iid.symbol, iid.exchange, iid))
        ranked.sort(key=lambda r: r[:3])
        return [r[3] for r in ranked[: self.n]]
