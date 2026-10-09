"""Selection port: narrow the universe to the names to hold."""

from __future__ import annotations

from collections.abc import Sequence
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
