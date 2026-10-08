"""Start-date Monte Carlo through real `Honba.backtest` sessions (Balch pitfall #7).

The arithmetic and gates are unit-tested; this proves the driver asks the data
provider for the shifted windows, runs each offset on a fresh strategy copy,
and turns real fills into the dispersion verdict.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence

from honba.algo_analytics.monte_carlo import monte_carlo
from honba.domain.instrument import InstrumentKind
from honba.entities.bar import Bar
from honba.entities.instrument import Instrument, InstrumentId
from honba.entities.order import OrderIntent
from honba.strategies.base import Strategy

X = InstrumentId("XYZ", "NSE")
DAY_NS = 86_400 * 10**9
T0 = int(dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc).timestamp()) * 10**9


def _bars(n: int) -> list[Bar]:
    return [
        Bar(
            X,
            T0 + i * DAY_NS,
            100.0 + i,
            101.0 + i,
            99.0 + i,
            100.5 + i,
            1_000_000.0,
        )
        for i in range(n)
    ]


def _ns(day: dt.date) -> int:
    noon = dt.datetime.combine(day, dt.time(12), tzinfo=dt.timezone.utc)
    return int(noon.timestamp() * 10**9)


class RecordingProvider:
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars
        self.queries: list[tuple[dt.date, dt.date]] = []

    def bars(self, instrument_id, *, timeframe, start, end) -> Sequence[Bar]:
        self.queries.append((start.date(), end.date()))
        lo, hi = _ns(start.date()), _ns(end.date())
        return [b for b in self._bars if b.instrument_id == instrument_id and lo <= b.ts < hi]

    def instrument(self, instrument_id) -> Instrument:
        return Instrument(instrument_id, InstrumentKind.EQUITY, 1.0, 0.05)


class BuyOnce(Strategy):
    name = "buy_once"

    def __init__(self) -> None:
        self.starts = 0

    def on_start(self) -> None:
        self.starts += 1

    def on_bar(self, bar: Bar) -> None:
        if self.ctx.position(X) == 0 and not self.ctx.busy(X):
            self.ctx.submit(OrderIntent.market_buy(X, 10))


def test_monte_carlo_runs_every_offset_on_a_fresh_strategy() -> None:
    provider = RecordingProvider(_bars(90))
    template = BuyOnce()

    result = monte_carlo(
        template,
        symbol="XYZ",
        start="2021-01-01",
        end="2021-02-01",
        offsets=(0, 7, 14),
        data=provider,
        cash=100_000.0,
    )

    assert result.offsets == (0, 7, 14)
    assert len(result.final_equities) == 3
    # The whole window shifts forward by the offset; the span stays put.
    exp = [
        (dt.date(2021, 1, 1), dt.date(2021, 2, 1)),
        (dt.date(2021, 1, 8), dt.date(2021, 2, 8)),
        (dt.date(2021, 1, 15), dt.date(2021, 2, 15)),
    ]
    assert provider.queries == exp
    # The caller's instance is never mutated; every offset started from scratch.
    assert template.starts == 0
    # A steady climber earns the same everywhere: dispersion is tiny and it passes.
    assert result.passed is True
    assert result.cv_final_equity < 0.05
    assert "Start-date Monte Carlo: 3 offsets" in result.summary()


def test_monte_carlo_rejects_reserved_kwargs_before_any_backtest() -> None:
    provider = RecordingProvider(_bars(90))
    with __import__("pytest").raises(TypeError):
        monte_carlo(
            BuyOnce(),
            symbol="XYZ",
            start="2021-01-01",
            end="2021-02-01",
            offsets=(0,),
            data=provider,
            start_x="x",
        )
    assert provider.queries == []
