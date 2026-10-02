"""Ichimoku cloud (trend family)."""

from __future__ import annotations

from collections import deque

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._util import check as _check


@indicator(
    "ichimoku",
    "trend",
    inputs=("high", "low"),
    outputs=("span_a", "span_b"),
    warmup=lambda s: max(s._n) + s._spans.maxlen - 1,
)
class Ichimoku(Indicator):
    """Ichimoku cloud as plotted on the current bar: spans computed ``displacement - 1``
    bars earlier (the standard convention, matching Jesse). ``update`` returns
    ``(span_a, span_b)`` or ``None`` until warm."""

    def __init__(
        self, tenkan: int = 9, kijun: int = 26, senkou_b: int = 52, displacement: int = 26
    ) -> None:
        self._n = (_check(tenkan), _check(kijun), _check(senkou_b))
        self._h: deque[float] = deque(maxlen=max(self._n))
        self._l: deque[float] = deque(maxlen=max(self._n))
        self._spans: deque[tuple[float, float]] = deque(maxlen=_check(displacement))

    def _mid(self, n: int) -> float:
        return (max(list(self._h)[-n:]) + min(list(self._l)[-n:])) / 2

    def update(self, high: float, low: float) -> tuple[float, float] | None:
        self._h.append(high)
        self._l.append(low)
        if len(self._h) < self._h.maxlen:
            return None
        tenkan, kijun, senkou_b = (self._mid(n) for n in self._n)
        self._spans.append(((tenkan + kijun) / 2, senkou_b))
        return self._spans[0] if len(self._spans) == self._spans.maxlen else None
