"""Parameter plateau over a real parameter grid (Balch pitfall #9).

The flatness math and the gate verdict are unit-tested in
``tests/unit/test_parameter_plateau.py``; this proves the public ``plateau()``
driver runs every point of a Cartesian parameter grid through a full
``Honba.backtest`` (fresh strategy copy per point, equity-curve Sharpe as the
score) and turns the surface into the plateau verdict — a param-insensitive
strategy is a perfect plateau, one sharp parameter dependence rejects it.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

import pytest

from honba.algo_analytics import plateau
from honba.domain.instrument import InstrumentKind
from honba.entities.bar import Bar
from honba.entities.instrument import Instrument, InstrumentId
from honba.entities.order import OrderIntent
from honba.strategies.base import Strategy

X = InstrumentId("XYZ", "NSE")
DAY_NS = 86_400 * 10**9
T0 = int(dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc).timestamp()) * 10**9
N_BARS = 20
QUANTITY = 10.0
GRID = {"fast": [2, 3, 4], "slow": [10, 11]}


def _bars() -> list[Bar]:
    # Flat for five sessions, then a steady uptrend: a buy-and-hold edge with a
    # positive, well-defined Sharpe; no fills at all gives a flat curve (Sharpe 0).
    out = []
    for i in range(N_BARS):
        open_ = 100.0 + max(0, i - 4) * 2.0
        out.append(Bar(X, T0 + i * DAY_NS, open_, open_ + 1.0, open_ - 1.0, open_ + 0.5, 1_000.0))
    return out


def _to_ns(value: dt.datetime) -> int:
    return int(value.replace(tzinfo=dt.timezone.utc).timestamp() * 10**9)


class Provider:
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    def bars(self, instrument_id, *, timeframe, start, end) -> Sequence[Bar]:
        lo, hi = _to_ns(start), _to_ns(end)
        return [b for b in self._bars if b.instrument_id == instrument_id and lo <= b.ts < hi]

    def instrument(self, instrument_id) -> Instrument:
        return Instrument(instrument_id, InstrumentKind.EQUITY, 1.0, 0.05)


class Stable(Strategy):
    """Has both knobs but its behaviour does not depend on them: every grid point ties."""

    name = "stable"

    def __init__(self) -> None:
        self.fast = 2
        self.slow = 10
        self.bought = False

    def on_bar(self, bar: Bar) -> None:
        if not self.bought and not self.ctx.busy(X):
            self.bought = True
            self.ctx.submit(OrderIntent.market_buy(X, QUANTITY))


class Cliff(Strategy):
    """Trades only for ``fast == 3``: a single-integer parameter cliff in the surface."""

    name = "cliff"

    def __init__(self) -> None:
        self.fast = 2
        self.slow = 10
        self.bought = False

    def on_bar(self, bar: Bar) -> None:
        if not self.bought and not self.ctx.busy(X):
            self.bought = True
            if self.fast == 3:
                self.ctx.submit(OrderIntent.market_buy(X, QUANTITY))


class NoKnobs(Strategy):
    name = "no_knobs"


def _plateau(strategy, params=GRID, **kw):
    return plateau(
        strategy,
        params=params,
        symbol="XYZ",
        start="2024-01-01",
        end="2024-02-01",
        data=Provider(_bars()),
        costs="none",
        **kw,
    )


def test_a_param_insensitive_strategy_is_a_perfect_plateau() -> None:
    verdict = _plateau(Stable())

    assert verdict.points == 6 and verdict.steps == 7  # 3x2 grid, index-adjacent pairs
    assert verdict.flatness == 1.0  # every run produced the identical score
    assert verdict.passed
    assert verdict.summary().endswith("RESULT: PASSED")


def test_a_single_parameter_cliff_rejects_the_surface() -> None:
    verdict = _plateau(Cliff())

    assert not verdict.passed
    assert verdict.flatness == 0.0  # trading (Sharpe > 0) next to not trading (0)
    assert verdict.summary().endswith("RESULT: FAILED (1 of 1 checks failed)")
    (a, b) = verdict.worst_step
    assert sorted([a[0], b[0]]) in ([2, 3], [3, 4])  # the cliff straddles fast == 3
    assert a[1] == b[1]  # on one row of the slow axis


def test_every_grid_point_is_run_once_with_a_fresh_strategy_copy() -> None:
    seen: list[int] = []
    verdict = _plateau(Stable(), score=lambda result: seen.append(1) or 1.0)

    assert len(seen) == 6
    assert verdict.flatness == 1.0  # the custom score reached the gate


def test_the_driver_validates_the_grid_and_strategy_before_running() -> None:
    with pytest.raises(ValueError, match="adjacent"):
        _plateau(Stable(), params={"fast": [3]})  # one point: no adjacent step exists
    with pytest.raises(ValueError, match="fast"):
        _plateau(NoKnobs())  # the strategy does not expose the parameter
    with pytest.raises(TypeError, match="instance"):
        plateau(Stable, params=GRID, symbol="XYZ", start="2024-01-01", end="2024-01-05")
