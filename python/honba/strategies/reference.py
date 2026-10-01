"""Reference strategies, mirrored one-to-one by ``honba_strategy`` in Rust (ADR 008).

They are the strategies of the shared conformance fixture
(``schema/conformance/strategy_contract.json``): the same scripted events must give
the same intents, fills and context observations in both languages. Change one
side only together with the other and the fixture.
"""

from __future__ import annotations

from typing import Any

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.tick import QuoteTick, TradeTick
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.indicators import Sma


class BuyAndHold(Strategy):
    """Buys ``quantity`` once on the first bar, then does nothing."""

    name = "buy_and_hold"

    def __init__(self, instrument_id: InstrumentId, quantity: float) -> None:
        self.instrument_id = instrument_id
        self.quantity = quantity
        self.bought = False

    def on_bar(self, bar: Bar) -> None:
        if not self.bought:
            self.ctx.submit(OrderIntent.market_buy(self.instrument_id, self.quantity))
            self.bought = True


class SmaCrossover(Strategy):
    """Buys when the fast SMA crosses above the slow SMA, sells when it crosses back below.

    Tracks crosses, not the position: one buy per upward cross and one sell per
    downward cross.
    """

    name = "sma_crossover"

    def __init__(self, instrument_id: InstrumentId, fast: int, slow: int, quantity: float) -> None:
        if not 0 < fast < slow:
            raise ValueError("SMA periods must be positive and fast < slow")
        self.instrument_id = instrument_id
        self.quantity = quantity
        self._fast, self._slow = Sma(fast), Sma(slow)
        self._prev_above: bool | None = None

    def on_bar(self, bar: Bar) -> None:
        f, s = self._fast.update(bar.close), self._slow.update(bar.close)
        if f is None or s is None:
            return
        above = f > s
        if self._prev_above is not None and self._prev_above != above:
            make = OrderIntent.market_buy if above else OrderIntent.market_sell
            self.ctx.submit(make(self.instrument_id, self.quantity))
        self._prev_above = above


class ContractProbe(Strategy):
    """Exercises every hook, every context capability and all four order types.

    Each hook first records an observation of the context (clock, position, cash,
    busy, number of open positions). Quantities use the instrument's lot size and
    prices its tick size (1.0 and 0.01 when the instrument is unknown).

    - ``on_start``: market buy one lot (processed with the first event).
    - ``on_bar``: if not busy, market buy one lot when flat, or sell the position
      when the close is below the previous close.
    - ``on_quote``: if long and not busy, limit sell one lot at the ask.
    - ``on_trade``: if not busy, stop buy one lot at ``price + tick`` when flat, or
      stop-limit sell one lot (trigger ``price - tick``, limit ``price - 2 * tick``) when long.
    - ``on_fill``: after the first buy fill, stop sell its quantity at ``price - 10 * tick``
      (processed with the next event).
    - ``on_stop``: sell the position (never executed: the run is over).
    """

    name = "contract_probe"

    def __init__(self, instrument_id: InstrumentId) -> None:
        self.instrument_id = instrument_id
        self.observations: list[dict[str, Any]] = []
        self._last_close: float | None = None
        self._protected = False

    def _lot(self) -> float:
        instrument = self.ctx.instrument(self.instrument_id)
        return instrument.lot_size if instrument is not None else 1.0

    def _tick(self) -> float:
        instrument = self.ctx.instrument(self.instrument_id)
        return instrument.tick_size if instrument is not None else 0.01

    def _observe(self, hook: str) -> None:
        ctx, iid = self.ctx, self.instrument_id
        self.observations.append(
            {
                "hook": hook,
                "now": ctx.now(),
                "position": ctx.position(iid),
                "cash": ctx.cash(),
                "busy": ctx.busy(iid),
                "open_positions": len(ctx.positions()),
            }
        )

    def on_start(self) -> None:
        self._observe("on_start")
        self.ctx.submit(OrderIntent.market_buy(self.instrument_id, self._lot()))

    def on_bar(self, bar: Bar) -> None:
        self._observe("on_bar")
        prev, self._last_close = self._last_close, bar.close
        if self.ctx.busy(self.instrument_id):
            return
        pos = self.ctx.position(self.instrument_id)
        if pos == 0:
            self.ctx.submit(OrderIntent.market_buy(self.instrument_id, self._lot()))
        elif pos > 0 and prev is not None and bar.close < prev:
            self.ctx.submit(OrderIntent.market_sell(self.instrument_id, pos))

    def on_quote(self, quote: QuoteTick) -> None:
        self._observe("on_quote")
        if not self.ctx.busy(self.instrument_id) and self.ctx.position(self.instrument_id) > 0:
            self.ctx.submit(
                OrderIntent.limit_sell(self.instrument_id, self._lot(), quote.ask_price)
            )

    def on_trade(self, trade: TradeTick) -> None:
        self._observe("on_trade")
        if self.ctx.busy(self.instrument_id):
            return
        pos, tick = self.ctx.position(self.instrument_id), self._tick()
        if pos == 0:
            self.ctx.submit(
                OrderIntent.stop_buy(self.instrument_id, self._lot(), trade.price + tick)
            )
        elif pos > 0:
            self.ctx.submit(
                OrderIntent.stop_limit_sell(
                    self.instrument_id, self._lot(), trade.price - tick, trade.price - 2 * tick
                )
            )

    def on_fill(self, fill: Trade) -> None:
        self._observe("on_fill")
        if not self._protected and fill.side is OrderSide.BUY:
            self._protected = True
            self.ctx.submit(
                OrderIntent.stop_sell(
                    self.instrument_id, fill.quantity, fill.price - 10 * self._tick()
                )
            )

    def on_stop(self) -> None:
        self._observe("on_stop")
        pos = self.ctx.position(self.instrument_id)
        if pos > 0:
            self.ctx.submit(OrderIntent.market_sell(self.instrument_id, pos))
