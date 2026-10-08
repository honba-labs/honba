"""The opening auction at the fill (Balch pitfall #8).

An order filled at a printed open pays the adverse spread buffer — buys above
the print, sells below it, costs charged on the buffered price — and an
intraday order waits ``delay_bars`` extra driving bars before it may fill at
all. The model itself is ``tests/unit/test_opening_auction.py``; the end-to-end
ledger accounting is ``tests/integration/test_backtest_opening_auction.py``.
"""

from __future__ import annotations

import math
import statistics
from itertools import pairwise

import pytest

from honba.backtest.impact import MarketImpact
from honba.backtest.opening_auction import OpeningAuction
from honba.backtest.simulated import NextOpenExecution, make_simulator
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.strategies.testing import BarCloseFills

A = InstrumentId("AAA", "NSE")
INR = Currency.INR
BUFFER_BPS = 25.0
BUFFER = BUFFER_BPS / 10_000.0
VOLUME = 1_000.0
OPENS = [100.0, 101.0, 99.0, 102.0, 100.0, 103.0, 101.0]  # sessions 0..6
CLOSES = [o + 0.5 for o in OPENS]
QUANTITY = 100.0


def _bar(i: int) -> Bar:
    open_ = OPENS[i]
    close = CLOSES[i]
    return Bar(A, i, open_, max(open_, close), min(open_, close), close, VOLUME)


def _port(**kw) -> NextOpenExecution:
    kw.setdefault("auction", OpeningAuction(spread_bps=BUFFER_BPS))
    return NextOpenExecution(cash=Money.from_major(1_000_000.0, INR), **kw)


def _history(p: NextOpenExecution) -> None:
    """Sessions 0..4 with no orders working, so the order is submitted at session 4."""
    for i in range(5):
        p.open_session(i, [_bar(i)])
    p.submit("o-0", OrderIntent.market_buy(A, QUANTITY), 4)


def test_a_buy_pays_the_buffered_open_not_the_printed_one() -> None:
    p = _port()
    _history(p)
    p.open_session(5, [_bar(5)])
    (fill,) = p.drain_fills()

    assert fill.price == pytest.approx(OPENS[5] * (1.0 + BUFFER))
    assert fill.price > OPENS[5]
    assert p.cash.to_major() == pytest.approx(1_000_000.0 - QUANTITY * fill.price, abs=0.01)


def test_a_sell_receives_the_buffered_open_not_the_printed_one() -> None:
    p = _port()
    for i in range(5):
        p.open_session(i, [_bar(i)])
    p.positions[A] = QUANTITY
    p.submit("o-1", OrderIntent.market_sell(A, QUANTITY), 4)
    p.open_session(5, [_bar(5)])
    (fill,) = p.drain_fills()

    assert fill.price == pytest.approx(OPENS[5] * (1.0 - BUFFER))
    assert fill.price < OPENS[5]
    assert p.cash.to_major() == pytest.approx(1_000_000.0 + QUANTITY * fill.price, abs=0.01)


def test_without_an_auction_the_printed_open_is_the_fill_price() -> None:
    p = NextOpenExecution(cash=Money.from_major(1_000_000.0, INR), backend="python")
    _history(p)
    p.open_session(5, [_bar(5)])
    (fill,) = p.drain_fills()
    assert fill.price == OPENS[5]


def test_the_buffer_and_market_impact_compose_on_one_fill() -> None:
    # Two independent frictions on the same print: the auction spread and the
    # size-driven impact, both adverse to the taker.
    p = _port(impact=MarketImpact(kappa=1.0, window=20))
    _history(p)
    p.open_session(5, [_bar(5)])
    (fill,) = p.drain_fills()

    returns = [b / a - 1.0 for a, b in pairwise(CLOSES[:5])]
    impact = 1.0 * statistics.stdev(returns) * math.sqrt(QUANTITY / VOLUME)
    assert fill.price == pytest.approx(OPENS[5] * (1.0 + BUFFER + impact))


def test_costs_are_charged_on_the_buffered_price() -> None:
    seen: list[tuple[OrderSide, float, float]] = []

    def costs(side: OrderSide, quantity: float, price: float) -> Money:
        seen.append((side, quantity, price))
        return Money.zero(INR)

    p = _port(costs=costs)
    _history(p)
    p.open_session(5, [_bar(5)])
    p.drain_fills()

    expected = OPENS[5] * (1.0 + BUFFER)
    # The simulator probes the cost function for affordability too; every call sees
    # the same buffered price, never the printed open.
    assert seen
    assert all(
        side is OrderSide.BUY and quantity == QUANTITY and price == pytest.approx(expected)
        for side, quantity, price in seen
    )


def test_an_order_waits_delay_bars_past_the_natural_next_session() -> None:
    p = NextOpenExecution(
        cash=Money.from_major(1_000_000.0, INR),
        auction=OpeningAuction(delay_bars=2),
    )
    _history(p)  # submitted at session 4: naturally eligible at session 5

    p.open_session(5, [_bar(5)])
    assert p.working_orders == ["o-0"]  # held back by the delay
    p.open_session(6, [_bar(6)])
    assert p.working_orders == ["o-0"]
    open_7 = Bar(A, 7, 104.0, 104.5, 103.5, 104.5, VOLUME)
    p.open_session(7, [open_7])  # 4 + 1 (natural) + 2 (delay) = 7
    (fill,) = p.drain_fills()
    assert fill.price == 104.0  # a delay alone changes when, not what, is paid
    assert p.working_orders == []


def test_a_zero_delay_fills_at_the_natural_next_session() -> None:
    p = _port(auction=OpeningAuction(spread_bps=BUFFER_BPS, delay_bars=0))
    _history(p)
    p.open_session(5, [_bar(5)])
    (fill,) = p.drain_fills()
    assert fill.price == pytest.approx(OPENS[5] * (1.0 + BUFFER))


def test_an_auction_forces_the_python_backend() -> None:
    forced = NextOpenExecution(
        cash=Money.from_major(1_000.0, INR), auction=OpeningAuction(spread_bps=1.0)
    )
    assert forced.backend == "python"  # even when the native extension is usable
    with pytest.raises(ValueError, match="Python backend"):
        NextOpenExecution(
            cash=Money.from_major(1_000.0, INR),
            backend="native",
            auction=OpeningAuction(spread_bps=1.0),
        )


def test_make_simulator_forwards_the_auction_and_refuses_it_for_bar_close() -> None:
    sim = make_simulator(
        fill="next_open",
        cash=Money.from_major(1_000.0, INR),
        costs="none",
        auction=OpeningAuction(spread_bps=BUFFER_BPS),
    )
    assert isinstance(sim, NextOpenExecution)
    assert sim.backend == "python"

    with pytest.raises(ValueError, match="auction"):
        make_simulator(
            fill="bar_close",
            cash=Money.from_major(1_000.0, INR),
            auction=OpeningAuction(spread_bps=BUFFER_BPS),
        )
    assert isinstance(
        make_simulator(fill="bar_close", cash=Money.from_major(1_000.0, INR)), BarCloseFills
    )


def test_a_post_open_delay_needs_an_intraday_timeframe() -> None:
    # The delay counts driving bars, and a daily bar is a whole session: waiting
    # "5 minutes past the open" is only meaningful sub-daily.
    with pytest.raises(ValueError, match="delay_bars"):
        make_simulator(
            fill="next_open",
            cash=Money.from_major(1_000.0, INR),
            timeframe="1d",
            settlement_days=0,
            auction=OpeningAuction(delay_bars=5),
        )
    sim = make_simulator(
        fill="next_open",
        cash=Money.from_major(1_000.0, INR),
        timeframe="5m",
        settlement_days=0,
        auction=OpeningAuction(delay_bars=5),
    )
    assert isinstance(sim, NextOpenExecution)
