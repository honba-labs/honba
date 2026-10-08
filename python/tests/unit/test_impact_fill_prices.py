"""Market impact at the fill (Balch pitfall #4).

The printed open is degraded by the square-root law before any money moves:
buys pay more, sells receive less, costs are charged on the impacted price, and
a fill never prices itself off its own bar. The model itself is
``tests/unit/test_market_impact.py``; the end-to-end ledger accounting is
``tests/integration/test_backtest_market_impact.py``.
"""

from __future__ import annotations

import math
import statistics
from itertools import pairwise

import pytest

from honba.backtest.impact import MarketImpact
from honba.backtest.simulated import NextOpenExecution, make_simulator
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.strategies.testing import BarCloseFills

A = InstrumentId("AAA", "NSE")
INR = Currency.INR
KAPPA = 1.0
VOLUME = 1_000.0
OPENS = [100.0, 101.0, 99.0, 102.0, 100.0, 103.0, 101.0]  # sessions 0..6
CLOSES = [o + 0.5 for o in OPENS]
QUANTITY = 100.0


def _bar(i: int, *, close: float | None = None) -> Bar:
    open_ = OPENS[i]
    close = CLOSES[i] if close is None else close
    return Bar(A, i, open_, max(open_, close), min(open_, close), close, VOLUME)


def _port(**kw) -> NextOpenExecution:
    kw.setdefault("impact", MarketImpact(kappa=KAPPA, window=20))
    return NextOpenExecution(cash=Money.from_major(1_000_000.0, INR), **kw)


def _history(p: NextOpenExecution, *, close_at_session_6: float | None = None) -> None:
    """Sessions 0..5 with no orders working, so the fill session is 6."""
    for i in range(6):
        p.open_session(i, [_bar(i)])
    p.submit("o-0", OrderIntent.market_buy(A, QUANTITY), 5)
    p.open_session(6, [_bar(6, close=close_at_session_6)])


def _expected_fraction(*, history_closes: list[float] = CLOSES[:6]) -> float:
    returns = [b / a - 1.0 for a, b in pairwise(history_closes)]
    return KAPPA * statistics.stdev(returns) * math.sqrt(QUANTITY / VOLUME)


def test_a_buy_pays_the_impacted_open_not_the_printed_one() -> None:
    p = _port()
    _history(p)
    (fill,) = p.drain_fills()

    expected = _expected_fraction()
    assert expected > 0.0
    assert fill.price == pytest.approx(OPENS[6] * (1.0 + expected))
    assert fill.price > OPENS[6]
    assert p.cash.to_major() == pytest.approx(1_000_000.0 - QUANTITY * fill.price, abs=0.01)


def test_a_sell_receives_the_impacted_open_not_the_printed_one() -> None:
    p = _port()
    for i in range(6):
        p.open_session(i, [_bar(i)])
    p.positions[A] = QUANTITY
    p.submit("o-1", OrderIntent.market_sell(A, QUANTITY), 5)
    p.open_session(6, [_bar(6)])
    (fill,) = p.drain_fills()

    expected = _expected_fraction()
    assert fill.price == pytest.approx(OPENS[6] * (1.0 - expected))
    assert fill.price < OPENS[6]
    assert p.cash.to_major() == pytest.approx(1_000_000.0 + QUANTITY * fill.price, abs=0.01)


def test_a_fill_never_prices_itself_off_its_own_bar() -> None:
    # The fill session's close is an extreme outlier: it may not reach back into
    # the fill price (it happens after the open, so it cannot be known there).
    plain, wild = _port(), _port()
    _history(plain)
    _history(wild, close_at_session_6=10_000.0)
    (plain_fill,) = plain.drain_fills()
    (wild_fill,) = wild.drain_fills()
    assert (
        wild_fill.price
        == plain_fill.price
        == pytest.approx(OPENS[6] * (1.0 + _expected_fraction()))
    )


def test_without_an_impact_model_the_printed_open_is_the_fill_price() -> None:
    p = NextOpenExecution(cash=Money.from_major(1_000_000.0, INR), backend="python")
    _history(p)
    (fill,) = p.drain_fills()
    assert fill.price == OPENS[6]


def test_costs_are_charged_on_the_impacted_price() -> None:
    seen: list[tuple[OrderSide, float, float]] = []

    def costs(side: OrderSide, quantity: float, price: float) -> Money:
        seen.append((side, quantity, price))
        return Money.zero(INR)

    p = _port(costs=costs)
    _history(p)
    p.drain_fills()

    expected = OPENS[6] * (1.0 + _expected_fraction())
    # The simulator probes the cost function for affordability too; every call sees
    # the same impacted price, never the printed open.
    assert seen
    assert all(
        side is OrderSide.BUY and quantity == QUANTITY and price == pytest.approx(expected)
        for side, quantity, price in seen
    )
    expected = OPENS[6] * (1.0 + _expected_fraction())


def test_impact_forces_the_python_backend() -> None:
    forced = NextOpenExecution(cash=Money.from_major(1_000.0, INR), impact=MarketImpact())
    assert forced.backend == "python"  # even when the native extension is usable
    with pytest.raises(ValueError, match="Python backend"):
        NextOpenExecution(
            cash=Money.from_major(1_000.0, INR),
            backend="native",
            impact=MarketImpact(),
        )


def test_make_simulator_forwards_impact_and_refuses_it_for_bar_close() -> None:
    sim = make_simulator(
        fill="next_open",
        cash=Money.from_major(1_000.0, INR),
        costs="none",
        impact=MarketImpact(kappa=0.5),
    )
    assert isinstance(sim, NextOpenExecution)
    assert sim.backend == "python"
    assert make_simulator(fill="next_open", cash=Money.from_major(1_000.0, INR)) is not None

    with pytest.raises(ValueError, match="impact"):
        make_simulator(
            fill="bar_close",
            cash=Money.from_major(1_000.0, INR),
            impact=MarketImpact(),
        )
    assert isinstance(
        make_simulator(fill="bar_close", cash=Money.from_major(1_000.0, INR)), BarCloseFills
    )
