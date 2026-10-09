"""Weighting port: how to split capital across the selected names."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from honba.entities.instrument import InstrumentId
from honba.strategies.portfolio.view import MarketView


@runtime_checkable
class WeightingScheme(Protocol):
    """Long-only target weights for ``selected`` (non-negative, sum <= 1, keys within
    ``selected``). The strategy applies ``allocation`` on top."""

    def weights(
        self, selected: Sequence[InstrumentId], view: MarketView
    ) -> Mapping[InstrumentId, float]: ...


class EqualWeight:
    """1/N across distinct selected names; an empty selection gives no weights."""

    def weights(
        self, selected: Sequence[InstrumentId], view: MarketView
    ) -> Mapping[InstrumentId, float]:
        names = list(dict.fromkeys(selected))
        return {iid: 1.0 / len(names) for iid in names}
