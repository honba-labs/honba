"""Unit tests for replay() fill simulation semantics."""

from __future__ import annotations

from typing import ClassVar

from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext
from honba.strategies.testing import replay

NSE = "NSE"
INR = Currency.INR


class RebalanceStrategy(Strategy):
    """Strategy that on bar 2 emits a SELL for inst A and a BUY for inst B, in that order or reverse."""

    name: ClassVar[str] = "rebalance_test"

    def __init__(self, submit_buy_first: bool = False) -> None:
        self.submit_buy_first = submit_buy_first
        self.bar_count = 0

    def on_bar(self, bar: Bar) -> None:
        self.bar_count += 1
        if self.bar_count == 3:
            iid_a = InstrumentId("STOCK_A", NSE)
            iid_b = InstrumentId("STOCK_B", NSE)
            # Strategy has stock A worth 1000. Cash is 0.
            # Sell 10 shares of A @ 100 -> cash +1000.
            # Buy 5 shares of B @ 200 -> cost 1000.
            sell_intent = OrderIntent.market_sell(iid_a, 10.0)
            buy_intent = OrderIntent.market_buy(iid_b, 5.0)
            if self.submit_buy_first:
                self.submit(buy_intent)
                self.submit(sell_intent)
            else:
                self.submit(sell_intent)
                self.submit(buy_intent)


def test_replay_sells_before_buys_and_uses_per_instrument_close() -> None:
    iid_a = InstrumentId("STOCK_A", NSE)
    iid_b = InstrumentId("STOCK_B", NSE)

    # Initial cash is 0, but holding 10 of STOCK_A.
    ctx = LedgerContext(cash=Money.zero(INR))
    ctx._positions[iid_a] = 10.0

    strat = RebalanceStrategy(submit_buy_first=True)
    strat._ctx = ctx

    bars = [
        # Bar 1: STOCK_A close=100
        Bar(iid_a, 1000, 100.0, 105.0, 95.0, 100.0, 1000.0),
        # Bar 2: STOCK_B close=200
        Bar(iid_b, 1000, 200.0, 205.0, 195.0, 200.0, 1000.0),
        # Bar 3: STOCK_A close=102 triggers rebalance. Emits BUY B then SELL A.
        Bar(iid_a, 2000, 102.0, 105.0, 99.0, 102.0, 1000.0),
    ]

    result = replay(strat, bars)

    assert len(result.intents) == 2
    # Submission order had BUY first
    assert result.intents[0].side == OrderSide.BUY
    assert result.intents[1].side == OrderSide.SELL

    # But fills MUST execute SELL first so cash is funded!
    assert len(result.fills) == 2
    assert result.fills[0].side == OrderSide.SELL
    assert result.fills[0].instrument_id == iid_a
    assert result.fills[0].price == 102.0  # STOCK_A's close

    assert result.fills[1].side == OrderSide.BUY
    assert result.fills[1].instrument_id == iid_b
    assert result.fills[1].price == 200.0  # STOCK_B's last close! Not STOCK_A's close!

    # Final cash should be 10*102 - 5*200 = 1020 - 1000 = 20 INR
    assert ctx.cash() == Money.from_major(20.0, INR)


def test_replay_delayed_fills_sells_before_buys_and_uses_per_instrument_open() -> None:
    iid_a = InstrumentId("STOCK_A", NSE)
    iid_b = InstrumentId("STOCK_B", NSE)

    ctx = LedgerContext(cash=Money.zero(INR))
    ctx._positions[iid_a] = 10.0

    strat = RebalanceStrategy(submit_buy_first=True)
    strat._ctx = ctx

    bars = [
        # Bar 1: STOCK_A open=98, close=100
        Bar(iid_a, 1000, 98.0, 105.0, 95.0, 100.0, 1000.0),
        # Bar 2: STOCK_B open=190, close=200
        Bar(iid_b, 1000, 190.0, 205.0, 195.0, 200.0, 1000.0),
        # Bar 3: STOCK_A triggers rebalance at bar 3 (idx 2)
        Bar(iid_a, 2000, 102.0, 105.0, 99.0, 102.0, 1000.0),
        # Bar 4: STOCK_A at next session (idx 3 = 2 + fill_delay 1), open=104
        Bar(iid_a, 3000, 104.0, 106.0, 101.0, 105.0, 1000.0),
    ]

    result = replay(strat, bars, fill_delay=1)

    assert len(result.intents) == 2
    assert len(result.fills) == 2

    # SELL executes first
    assert result.fills[0].side == OrderSide.SELL
    assert result.fills[0].instrument_id == iid_a
    assert result.fills[0].price == 104.0  # Bar 4 open for STOCK_A

    # BUY executes second, at STOCK_B's last open (190.0)
    assert result.fills[1].side == OrderSide.BUY
    assert result.fills[1].instrument_id == iid_b
    assert result.fills[1].price == 190.0
