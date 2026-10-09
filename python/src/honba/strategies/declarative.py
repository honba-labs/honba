"""Jesse-style declarative rules on top of the event-driven ``Strategy``.

``DeclarativeStrategy`` lets a strategy be written as per-instrument rules instead of
an ``on_bar`` body, so humans and LLM agents can express an idea in a few small,
testable methods. It is a pure facade: it only implements ``on_bar`` and ``on_fill``
and submits ordinary ``OrderIntent`` s through ``self.ctx`` (ADR 008), so it runs
unchanged in backtest, paper and live.

Jesse -> Honba mapping
----------------------
=============================  ==========================================================
Jesse                          Honba
=============================  ==========================================================
``should_long()``              ``should_long(bar)`` (default ``False``)
``should_short()``             ``should_short(bar)`` (default ``False``)
``go_long()`` (sets buy/stop)  ``go_long(bar) -> Entry(quantity, stop_loss, take_profit)``
``go_short()``                 ``go_short(bar) -> Entry(...)``
``update_position()``          ``should_exit(bar)`` (flatten with a market order)
``should_cancel_entry()``      not provided: gate on ``self.busy(instrument_id)``
``self.price`` / ``self.close``  the ``bar`` argument
``self.stop_loss`` / ``take_profit``  ``Entry.stop_loss`` / ``Entry.take_profit``
=============================  ==========================================================

Evaluation order per bar, for the bar's instrument only (a multi-instrument strategy
gets one call per instrument bar; no state is shared between instruments):

1. busy (an unfilled order exists) -> nothing happens;
2. flat -> ``should_long`` then ``should_short`` (long wins ties); on ``True`` the
   matching ``go_*`` is called and a market entry is submitted via ``buy`` / ``sell``;
3. in a position -> ``should_exit`` ``True`` submits a market order that flattens it.

Protective orders are placed in ``on_fill`` after the *entry fill*, sized to the filled
quantity (so partial fills are protected as they arrive): for a long a stop-market sell
at ``stop_loss`` and a limit sell at ``take_profit``; for a short a stop-market buy and
a limit buy. Levels are validated against the bar close when the entry is decided
(long: ``stop_loss < close < take_profit``; short: reversed) and a ``ValueError`` is
raised otherwise. Stored per-instrument state is dropped when a fill returns the
position to flat.

Notes and caveats
-----------------
* Warmup is the runner's job (``Strategy.warmup_bars``); this class does not repeat it.
* Overriding ``on_bar`` bypasses the facade entirely. Overriding ``on_fill`` is fine
  but must call ``super().on_fill(fill)`` or protective orders are not placed.
* Resting protective orders count as unfilled orders, so ``busy()`` is true while they
  rest and ``should_exit`` is not consulted until they are gone; exits then come from
  the stop / target themselves.
* TODO(OCO): the stop and the target are independent orders. When one fills the other
  must be cancelled by the engine / broker (OCO); this class does not cancel it.
* Trailing stops (``OrderType.TRAILING_STOP``) are not used.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.trade import Trade
from honba.strategies.base import Strategy

_EPSILON = 1e-9


def _check_level(name: str, value: float | None) -> None:
    if value is not None and not (math.isfinite(value) and value > 0):
        raise ValueError(f"{name} must be finite and > 0 when given, got {value}")


@dataclass(frozen=True, slots=True)
class Entry:
    """What to trade when a rule fires: size plus optional protective price levels."""

    quantity: float
    stop_loss: float | None = None
    take_profit: float | None = None

    def __post_init__(self) -> None:
        if not (math.isfinite(self.quantity) and self.quantity > 0):
            raise ValueError(f"quantity must be finite and > 0, got {self.quantity}")
        _check_level("stop_loss", self.stop_loss)
        _check_level("take_profit", self.take_profit)


class DeclarativeStrategy(Strategy):
    """Subclass, set ``name``, override the rule hooks you need (see the module docstring)."""

    # -- rules (override) -------------------------------------------------------
    def should_long(self, bar: Bar) -> bool:
        """Open a long when flat and idle. Default ``False``."""
        return False

    def should_short(self, bar: Bar) -> bool:
        """Open a short when flat and idle. Default ``False``."""
        return False

    def go_long(self, bar: Bar) -> Entry:
        """Size and levels of the long entry; called only after ``should_long`` is ``True``."""
        raise NotImplementedError(
            f"{type(self).__name__}.should_long returned True but go_long is not implemented"
        )

    def go_short(self, bar: Bar) -> Entry:
        """Size and levels of the short entry; called only after ``should_short`` is ``True``."""
        raise NotImplementedError(
            f"{type(self).__name__}.should_short returned True but go_short is not implemented"
        )

    def should_exit(self, bar: Bar) -> bool:
        """Flatten the open position at market. Default ``False``."""
        return False

    # -- facade -----------------------------------------------------------------
    @property
    def _entries(self) -> dict[InstrumentId, tuple[OrderSide, Entry]]:
        try:
            return self.__entries
        except AttributeError:
            self.__entries: dict[InstrumentId, tuple[OrderSide, Entry]] = {}
            return self.__entries

    def on_bar(self, bar: Bar) -> None:
        iid = bar.instrument_id
        if self.busy(iid):
            return
        position = self.position(iid)
        if abs(position) <= _EPSILON:
            if self.should_long(bar):
                entry = self.go_long(bar)
                self._validate_levels(entry, bar.close, long=True)
                self._entries[iid] = (OrderSide.BUY, entry)
                self.buy(iid, entry.quantity)
            elif self.should_short(bar):
                entry = self.go_short(bar)
                self._validate_levels(entry, bar.close, long=False)
                self._entries[iid] = (OrderSide.SELL, entry)
                self.sell(iid, entry.quantity)
        elif self.should_exit(bar):
            if position > 0:
                self.sell(iid, position)
            else:
                self.buy(iid, -position)

    def on_fill(self, fill: Trade) -> None:
        """Place protective orders after an entry fill. Subclasses must call ``super()``."""
        iid = fill.instrument_id
        state = self._entries.get(iid)
        if state is not None:
            side, entry = state
            if fill.side is side:
                self._protect(iid, side, fill.quantity, entry)
        if abs(self.position(iid)) <= _EPSILON:
            self._entries.pop(iid, None)

    def _protect(self, iid: InstrumentId, side: OrderSide, qty: float, entry: Entry) -> None:
        long = side is OrderSide.BUY
        if entry.stop_loss is not None:
            stop = OrderIntent.stop_sell if long else OrderIntent.stop_buy
            self.submit(stop(iid, qty, entry.stop_loss))
        if entry.take_profit is not None:
            take = OrderIntent.limit_sell if long else OrderIntent.limit_buy
            self.submit(take(iid, qty, entry.take_profit))

    @staticmethod
    def _validate_levels(entry: Entry, close: float, *, long: bool) -> None:
        side = "long" if long else "short"
        sign = 1 if long else -1
        if entry.stop_loss is not None and not sign * (entry.stop_loss - close) < 0:
            where = "below" if long else "above"
            raise ValueError(f"{side} stop_loss {entry.stop_loss} must be {where} close {close}")
        if entry.take_profit is not None and not sign * (entry.take_profit - close) > 0:
            where = "above" if long else "below"
            raise ValueError(
                f"{side} take_profit {entry.take_profit} must be {where} close {close}"
            )
