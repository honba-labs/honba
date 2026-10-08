"""Walk-forward validation driven through real ``Honba.backtest`` sessions.

End-to-end coverage for Balch pitfall #1: the fold windows are what the data
provider is actually queried with, every fold gets a fresh strategy instance,
and the out-of-sample gates are verdicts over real fills — next-open execution,
Indian costs and settlement included. No network, no clock, no fixtures on disk.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from typing import ClassVar

from honba.algo_analytics import (
    MIN_OOS_IS_RATIO,
    WalkForwardGates,
    passes_oos_is_gate,
    train_test_split,
    walk_forward,
)
from honba.domain.instrument import Instrument, InstrumentKind
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent
from honba.strategies.base import Strategy

X = InstrumentId("XYZ", "NSE")
DAY_NS = 86_400 * 10**9
T0 = int(dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc).timestamp()) * 10**9
N_DAYS = 1096  # 2020-01-01 .. 2022-12-31 inclusive (leap year 2020)


def _bar_prices() -> list[float]:
    """A steady uptrend with a deterministic wiggle, so every window has a Sharpe."""
    return [100.0 * (1.002**i) * (1.0 + 0.01 * math.sin(i * 0.7)) for i in range(N_DAYS)]


def _bars() -> list[Bar]:
    out = []
    for i, price in enumerate(_bar_prices()):
        out.append(
            Bar(
                X,
                T0 + i * DAY_NS,
                price,
                price * 1.01,
                price * 0.99,
                price * 1.005,
                1_000_000.0,
            )
        )
    return out


def _to_ns(value: dt.datetime) -> int:
    """Session bounds arrive naive; bars are UTC-stamped — compare on one clock."""
    return int(value.replace(tzinfo=dt.timezone.utc).timestamp() * 10**9)


class RecordingProvider:
    """In-memory bars that records the exact windows the session asked for."""

    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars
        self.queries: list[tuple[dt.date, dt.date]] = []

    def bars(self, instrument_id, *, timeframe, start, end) -> Sequence[Bar]:
        lo, hi = _to_ns(start), _to_ns(end)
        self.queries.append((start.date(), end.date()))
        return [
            b
            for b in self._bars
            if b.instrument_id == instrument_id and lo <= b.ts < hi  # [start, end)
        ]

    def instrument(self, instrument_id) -> Instrument:
        return Instrument(instrument_id, InstrumentKind.EQUITY, 1.0, 0.05)


class BuyAndHold(Strategy):
    name = "buy_and_hold"

    def on_bar(self, bar: Bar) -> None:
        if self.ctx.position(X) == 0 and not self.ctx.busy(X):
            self.ctx.submit(OrderIntent.market_buy(X, 900))


class NeverTrades(Strategy):
    name = "never_trades"


class CountsStarts(Strategy):
    """Proves fold independence: a reused instance would start above zero."""

    name = "counts_starts"
    seen: ClassVar[list[int]] = []  # class-level: survives the per-fold copies

    def __init__(self) -> None:
        self.n_starts = 0

    def on_start(self) -> None:
        self.n_starts += 1
        CountsStarts.seen.append(self.n_starts)


def _walk(strategy, provider: RecordingProvider, **kwargs):
    return walk_forward(
        strategy,
        symbol="XYZ",
        start="2020-01-01",
        end="2023-01-01",
        data=provider,
        train_months=12,
        test_months=6,
        n_folds=2,
        cash=100_000.0,
        **kwargs,
    )


def test_walk_forward_queries_the_provider_with_the_planned_fold_windows() -> None:
    provider = RecordingProvider(_bars())
    wf = _walk(BuyAndHold(), provider)

    assert provider.queries == [
        (dt.date(2020, 1, 1), dt.date(2021, 1, 1)),  # fold 0 train
        (dt.date(2021, 1, 1), dt.date(2021, 7, 1)),  # fold 0 test (out-of-sample)
        (dt.date(2020, 1, 1), dt.date(2021, 7, 1)),  # fold 1 train (expanding)
        (dt.date(2021, 7, 1), dt.date(2022, 1, 1)),  # fold 1 test
    ]
    assert len(wf.folds) == 2
    fold = wf.folds[0]
    assert (fold.train_start, fold.train_end, fold.test_start, fold.test_end) == (
        dt.date(2020, 1, 1),
        dt.date(2021, 1, 1),
        dt.date(2021, 1, 1),
        dt.date(2021, 7, 1),
    )
    # Both windows actually traded (real fills through the next-open simulator).
    assert fold.in_sample.n_fills >= 1
    assert fold.out_of_sample.n_fills >= 1


def test_every_fold_starts_from_a_fresh_strategy_instance() -> None:
    CountsStarts.seen = []
    template = CountsStarts()
    _walk(template, RecordingProvider(_bars()))

    assert template.n_starts == 0  # the caller's instance is never mutated
    assert CountsStarts.seen == [1, 1, 1, 1]  # each of the 4 runs started from scratch


def test_walk_forward_passes_for_a_strategy_that_earns_in_every_fold() -> None:
    wf = _walk(BuyAndHold(), RecordingProvider(_bars()))

    assert wf.profitable_folds == len(wf.folds)
    assert wf.mean_oos_sharpe > 0.8
    assert wf.oos_sharpe_std < 0.5
    assert wf.passed is True
    assert wf.summary().endswith("RESULT: PASSED")


def test_walk_forward_fails_the_gates_for_a_strategy_that_never_trades() -> None:
    wf = _walk(NeverTrades(), RecordingProvider(_bars()))

    assert wf.oos_sharpes == [0.0, 0.0]
    assert wf.checks == {
        "mean_oos_sharpe": False,  # 0.0 does not clear the 0.8 bar
        "oos_sharpe_std": True,  # a flat line has no spread
        "profitable_folds": False,  # and no fold is profitable
    }
    assert wf.passed is False
    assert "RESULT: FAILED (2 of 3 checks failed)" in wf.summary()


def test_walk_forward_accepts_a_strategy_class_and_custom_gates() -> None:
    provider = RecordingProvider(_bars())
    wf = _walk(
        NeverTrades,  # a class: instantiated fresh inside every fold
        provider,
        gates=WalkForwardGates(min_mean_oos_sharpe=-0.5, min_profitable_fraction=0.0),
    )
    assert wf.passed is True  # zero-bar strategy, but the caller moved the bars
    assert wf.gates.min_mean_oos_sharpe == -0.5


def test_walk_forward_rejects_a_range_too_short_for_the_requested_folds() -> None:
    provider = RecordingProvider(_bars())
    try:
        walk_forward(
            BuyAndHold(),
            symbol="XYZ",
            start="2020-01-01",
            end="2020-09-01",  # train 12m alone does not fit
            data=provider,
            train_months=12,
            test_months=6,
            n_folds=2,
        )
    except ValueError as e:
        assert "data range" in str(e)
    else:
        raise AssertionError("walk_forward accepted a range it cannot cut into folds")
    assert provider.queries == []  # failed before any backtest ran


def test_train_test_split_judges_the_held_back_window_and_applies_the_half_rule() -> None:
    provider = RecordingProvider(_bars())
    is_stats, oos_stats = train_test_split(
        BuyAndHold(),
        symbol="XYZ",
        start="2020-01-01",
        train_end="2021-07-01",
        end="2023-01-01",
        data=provider,
        cash=100_000.0,
    )
    assert provider.queries == [
        (dt.date(2020, 1, 1), dt.date(2021, 7, 1)),
        (dt.date(2021, 7, 1), dt.date(2023, 1, 1)),
    ]
    assert is_stats.sharpe > 0.0
    assert oos_stats.sharpe > 0.0
    assert passes_oos_is_gate(is_stats, oos_stats) is True
    assert MIN_OOS_IS_RATIO == 0.5

    # A strategy with no edge has nothing in-sample to keep half of: the gate
    # fails closed (NaN ratio), it does not pass by default.
    is_flat, oos_flat = train_test_split(
        NeverTrades(),
        symbol="XYZ",
        start="2020-01-01",
        train_end="2021-07-01",
        end="2023-01-01",
        data=RecordingProvider(_bars()),
        cash=100_000.0,
    )
    assert passes_oos_is_gate(is_flat, oos_flat) is False
