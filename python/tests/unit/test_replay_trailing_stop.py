"""Unit tests for trailing stop execution in replay() and BarCloseFills."""

from __future__ import annotations

import pytest

from typing import ClassVar

from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderType
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext
from honba.strategies.testing import BarCloseFills, replay

NSE = "NSE"
INFY = InstrumentId("INFY", NSE)


class TrailingSellStrategy(Strategy):
    name: ClassVar[str] = "trailing_sell_test"

    def __init__(self, trail_amount: float | None = None, trail_percent: float | None = None) -> None:
        self.trail_amount = trail_amount
        self.trail_percent = trail_percent
        self.placed = False
        self.received_fills: list[Trade] = []

    def on_bar(self, bar: Bar) -> None:
        if not self.placed:
            self.placed = True
            intent = OrderIntent.trailing_stop_sell(
                bar.instrument_id,
                10.0,
                trail_amount=self.trail_amount,
                trail_percent=self.trail_percent,
            )
            self.submit(intent)

    def on_fill(self, trade: Trade) -> None:
        self.received_fills.append(trade)


class TrailingBuyStrategy(Strategy):
    name: ClassVar[str] = "trailing_buy_test"

    def __init__(self, trail_amount: float | None = None, trail_percent: float | None = None) -> None:
        self.trail_amount = trail_amount
        self.trail_percent = trail_percent
        self.placed = False
        self.received_fills: list[Trade] = []

    def on_bar(self, bar: Bar) -> None:
        if not self.placed:
            self.placed = True
            intent = OrderIntent.trailing_stop_buy(
                bar.instrument_id,
                5.0,
                trail_amount=self.trail_amount,
                trail_percent=self.trail_percent,
            )
            self.submit(intent)

    def on_fill(self, trade: Trade) -> None:
        self.received_fills.append(trade)


def test_replay_trailing_stop_sell_ratchets_and_triggers_on_breach() -> None:
    strat = TrailingSellStrategy(trail_amount=5.0)

    bars = [
        # Bar 1: close = 100. Strategy places SELL trailing stop with trail_amount=5.0 (stop = 95.0)
        Bar(INFY, 1000, 100.0, 102.0, 99.0, 100.0, 1000.0),
        # Bar 2: high rises to 110.0! Peak becomes 110.0, stop ratchets up to 105.0. Low 106.0 does not breach.
        Bar(INFY, 2000, 102.0, 110.0, 106.0, 108.0, 1000.0),
        # Bar 3: drops to 104.0, breaching stop at 105.0!
        Bar(INFY, 3000, 107.0, 107.0, 104.0, 105.0, 1000.0),
    ]

    result = replay(strat, bars)

    assert len(result.intents) == 1
    assert result.intents[0].order_type == OrderType.TRAILING_STOP
    assert len(result.fills) == 1
    fill = result.fills[0]
    assert fill.side == OrderSide.SELL
    assert fill.quantity == 10.0
    assert fill.price == 105.0
    assert fill.ts == 3000
    assert strat.received_fills == [fill]


def test_replay_trailing_stop_buy_ratchets_and_triggers_on_breach() -> None:
    # 2.5% trail
    strat = TrailingBuyStrategy(trail_percent=2.5)

    bars = [
        # Bar 1: close = 200.0. Trailing buy placed with trail_percent=2.5%
        Bar(INFY, 1000, 200.0, 201.0, 199.0, 200.0, 1000.0),
        # Bar 2: dips to 190.0! Trough becomes 190.0, stop ratchets down to 190 * 1.025 = 194.75. High 194.0 does not breach.
        Bar(INFY, 2000, 198.0, 194.0, 190.0, 192.0, 1000.0),
        # Bar 3: bounces to 196.0, breaching 194.75!
        Bar(INFY, 3000, 193.0, 196.0, 192.0, 195.0, 1000.0),
    ]

    result = replay(strat, bars)

    assert len(result.fills) == 1
    fill = result.fills[0]
    assert fill.side == OrderSide.BUY
    assert fill.quantity == 5.0
    assert fill.price == pytest.approx(194.75)
    assert fill.ts == 3000


def test_replay_trailing_stop_gap_slippage() -> None:
    strat = TrailingSellStrategy(trail_amount=5.0)

    bars = [
        # Bar 1: close = 100.0. Peak = 100.0, stop = 95.0.
        Bar(INFY, 1000, 100.0, 102.0, 99.0, 100.0, 1000.0),
        # Bar 2: Gaps down at open to 93.0 (below stop 95.0)!
        Bar(INFY, 2000, 93.0, 94.0, 91.0, 92.0, 1000.0),
    ]

    result = replay(strat, bars)

    assert len(result.fills) == 1
    # Fills at the open price 93.0 due to gap down
    assert result.fills[0].price == 93.0


def test_bar_close_fills_trailing_stop() -> None:
    bcf = BarCloseFills()
    bcf.on_event(Bar(INFY, 1000, 100.0, 102.0, 99.0, 100.0, 1000.0), 1000)

    intent = OrderIntent.trailing_stop_sell(INFY, 10.0, trail_amount=5.0)
    bcf.submit("O-1", intent, 1000)
    assert bcf.drain_fills() == []

    # Bar 2: peak rises to 110.0, stop becomes 105.0. Low 106.0 -> no fill
    bcf.on_event(Bar(INFY, 2000, 102.0, 110.0, 106.0, 108.0, 1000.0), 2000)
    assert bcf.drain_fills() == []

    # Bar 3: drops to 104.0 -> triggered and filled at 105.0
    bcf.on_event(Bar(INFY, 3000, 107.0, 107.0, 104.0, 105.0, 1000.0), 3000)
    fills = bcf.drain_fills()
    assert len(fills) == 1
    assert fills[0].price == 105.0
    assert fills[0].quantity == 10.0
    assert fills[0].side == OrderSide.SELL
    assert fills[0].order_id == "O-1"
