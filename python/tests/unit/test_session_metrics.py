"""``honba.session._compute_metrics``: equity curve per session, drawdown, round trips."""

from __future__ import annotations

import math

import pytest

from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderSide
from honba.entities.trade import Trade
from honba.session import _compute_metrics

A = InstrumentId("AAA", "NSE")
B = InstrumentId("BBB", "NSE")
INR = Currency.INR


def bar(iid: InstrumentId, ts: int, close: float) -> Bar:
    return Bar(iid, ts, close, close, close, close, 1.0)


def trade(iid, side, qty, px, ts, cost=0.0) -> Trade:
    return Trade(iid, side, qty, px, ts, costs=Money.from_major(cost, INR))


class Ctx:
    def __init__(self, cash: float, positions: dict[InstrumentId, float]) -> None:
        self._cash, self._positions = Money.from_major(cash, INR), positions

    def cash(self) -> Money:
        return self._cash

    def position(self, iid: InstrumentId) -> float:
        return self._positions.get(iid, 0.0)

    def positions(self) -> dict[InstrumentId, float]:
        return dict(self._positions)


def test_equity_curve_has_one_point_per_session_across_all_instruments() -> None:
    bars = [
        bar(A, 1, 100.0),
        bar(A, 2, 110.0),
        bar(B, 2, 50.0),
        bar(A, 3, 120.0),  # B does not print: it keeps its last close
        bar(B, 4, 40.0),
        bar(A, 4, 120.0),
    ]
    fills = [
        trade(A, OrderSide.BUY, 10, 100.0, 1),  # cash 1000 -> 0 at the session-1 open
        trade(B, OrderSide.BUY, 20, 50.0, 2, cost=5.0),
    ]
    # Cash: 10_000 - 1_000 - (1_000 + 5) = 7_995; final A 10 @ 120, B 20 @ 40.
    metrics, curve = _compute_metrics(
        fills=fills,
        bars=bars,
        initial_cash=10_000.0,
        ctx=Ctx(7_995.0, {A: 10.0, B: 20.0}),
    )
    assert [ts for ts, _ in curve] == [1, 2, 3, 4]
    assert [eq for _, eq in curve] == pytest.approx(
        [
            9_000.0 + 10 * 100.0,  # 10_000
            8_000.0 - 5.0 + 10 * 110.0 + 20 * 50.0,
            7_995.0 + 10 * 120.0 + 20 * 50.0,
            7_995.0 + 10 * 120.0 + 20 * 40.0,
        ]
    )
    assert metrics["final_equity"] == pytest.approx(curve[-1][1])
    assert metrics["final_equity"] == pytest.approx(7_995.0 + 1_200.0 + 800.0)


def test_max_drawdown_is_peak_to_trough_percent_of_the_peak() -> None:
    bars = [bar(A, t, c) for t, c in [(1, 100.0), (2, 120.0), (3, 90.0), (4, 110.0)]]
    fills = [trade(A, OrderSide.BUY, 10, 100.0, 1)]
    metrics, _ = _compute_metrics(
        fills=fills, bars=bars, initial_cash=1_000.0, ctx=Ctx(0.0, {A: 10.0})
    )
    assert metrics["max_drawdown_pct"] == pytest.approx((1_200.0 - 900.0) / 1_200.0 * 100)


def test_short_runs_have_zero_drawdown_not_nan() -> None:
    metrics, curve = _compute_metrics(
        fills=[], bars=[bar(A, 1, 100.0)], initial_cash=1_000.0, ctx=Ctx(1_000.0, {})
    )
    assert metrics["max_drawdown_pct"] == 0.0
    assert curve == [(1, 1_000.0)]
    metrics, curve = _compute_metrics(fills=[], bars=[], initial_cash=1_000.0, ctx=Ctx(1_000.0, {}))
    assert metrics["max_drawdown_pct"] == 0.0 and curve == []
    assert not math.isnan(metrics["total_return_pct"])


def test_a_rising_curve_has_zero_drawdown() -> None:
    bars = [bar(A, t, c) for t, c in [(1, 100.0), (2, 101.0), (3, 102.0)]]
    metrics, _ = _compute_metrics(
        fills=[trade(A, OrderSide.BUY, 1, 100.0, 1)],
        bars=bars,
        initial_cash=1_000.0,
        ctx=Ctx(900.0, {A: 1.0}),
    )
    assert metrics["max_drawdown_pct"] == 0.0


def test_n_trades_counts_round_trips_and_n_fills_counts_fills() -> None:
    bars = [bar(A, t, 100.0) for t in range(1, 8)]
    fills = [
        trade(A, OrderSide.BUY, 5, 100.0, 1),
        trade(A, OrderSide.BUY, 5, 100.0, 2),  # scaling in is still one trade
        trade(A, OrderSide.SELL, 4, 100.0, 3),
        trade(A, OrderSide.SELL, 6, 100.0, 4),  # flat: round trip 1
        trade(A, OrderSide.BUY, 3, 100.0, 5),
        trade(A, OrderSide.SELL, 3, 100.0, 6),  # flat: round trip 2
        trade(A, OrderSide.BUY, 2, 100.0, 7),  # still open: not counted
    ]
    metrics, _ = _compute_metrics(
        fills=fills, bars=bars, initial_cash=10_000.0, ctx=Ctx(9_800.0, {A: 2.0})
    )
    assert metrics["n_fills"] == 7.0
    assert metrics["n_trades"] == 2.0


def test_float_residue_still_closes_the_round_trip() -> None:
    fills = [trade(A, OrderSide.BUY, 0.1, 10.0, t) for t in (1, 2, 3)]
    fills.append(trade(A, OrderSide.SELL, 0.3, 10.0, 4))
    metrics, _ = _compute_metrics(
        fills=fills,
        bars=[bar(A, t, 10.0) for t in range(1, 5)],
        initial_cash=100.0,
        ctx=Ctx(100.0, {}),
    )
    assert metrics["n_trades"] == 1.0
