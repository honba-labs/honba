"""Jesse/Backtrader-style declarative layers on top of the event-driven ``Strategy``.

Two facades live here: ``DeclarativeStrategy`` (per-instrument rules) and
``TargetWeightStrategy`` (portfolio level: declare universe + weights; framework
rebalances, see the end of this docstring).

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

TargetWeightStrategy
--------------------
Declare *what the portfolio should look like* (``universe()`` and optionally
``target_weights()``); the framework does the order mechanics. On a rebalance bar it
values the portfolio (cash + open positions at the last close), sizes each name to
``value * allocation * weight`` in whole shares and submits market orders: first exits
(held names with weight 0), then trims (sells), then top-ups / entries (buys), each
group in symbol order. Busy or unpriced names are skipped and differences under one
share are ignored. ``should_rebalance`` defaults to "first bar with prices for the
whole universe (or the next calendar day), then every ``rebalance_days`` trading
days"; days come from bar event time (UTC), never the wall clock. Long-only for now.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import ClassVar

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.sizing import whole_shares

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


_NS_PER_DAY = 86_400 * 10**9


def _symbol_key(iid: InstrumentId) -> tuple[str, str]:
    return (iid.symbol, iid.exchange)


class TargetWeightStrategy(Strategy):
    """Declare the universe and target weights; the framework rebalances (see module docs).

    Override ``universe`` (required) and, to deviate from equal weight, ``target_weights``.
    ``rebalance_days`` and ``allocation`` may be overridden as class attributes or set in
    ``__init__``; subclasses need not call ``super().__init__()``.
    """

    rebalance_days: ClassVar[int] = 15
    allocation: ClassVar[float] = 0.98

    # -- declare (override) -----------------------------------------------------
    def universe(self) -> Iterable[InstrumentId]:
        """Current members; called at every rebalance so membership may change."""
        raise NotImplementedError(f"{type(self).__name__} must override universe()")

    def target_weights(self) -> Mapping[InstrumentId, float]:
        """Target weight per instrument (long-only, sum <= 1). Default: equal weight."""
        members = list(self.universe())
        return {iid: 1.0 / len(members) for iid in members}

    def should_rebalance(self, bar: Bar) -> bool:
        """Default cadence: first complete-price (or next-day) bar, then every N days."""
        if not self._tw.initial_done:
            members = set(self.universe())
            return members <= self._tw.prices.keys() or self._tw.day_changed
        return self._tw.days_since >= self.rebalance_days

    # -- facade -----------------------------------------------------------------
    @property
    def _tw(self) -> _TargetWeightState:
        try:
            return self.__tw
        except AttributeError:
            self.__tw = _TargetWeightState()
            return self.__tw

    @property
    def _days_since_rebalance(self) -> int:
        return self._tw.days_since

    def on_bar(self, bar: Bar) -> None:
        st = self._tw
        st.prices[bar.instrument_id] = float(bar.close)
        day = bar.ts // _NS_PER_DAY
        st.day_changed = st.last_day is not None and day > st.last_day
        if st.day_changed:
            st.days_since += 1
        if st.last_day is None or day > st.last_day:
            st.last_day = day
        if self.should_rebalance(bar):
            self._rebalance()
            st.initial_done = True
            st.days_since = 0

    def _rebalance(self) -> None:
        st = self._tw
        members = set(self.universe())
        weights = self._checked_weights(members)
        self._log_membership(members)
        st.members = members
        value = self._portfolio_value()
        if value <= 0:
            return
        held = {iid: q for iid, q in self.ctx.positions().items() if q != 0}
        exits: list[InstrumentId] = []
        trims: list[tuple[InstrumentId, int]] = []
        buys: list[tuple[InstrumentId, int]] = []
        for iid in sorted(held.keys() | members, key=_symbol_key):
            if self.busy(iid):
                continue
            weight = weights.get(iid, 0.0)
            if weight == 0.0:
                if iid in held:
                    exits.append(iid)
                continue
            price = st.prices.get(iid)
            if price is None or price <= 0:
                continue
            diff = whole_shares(value * self.allocation * weight, 1.0, price) - held.get(iid, 0.0)
            if diff >= 1:
                buys.append((iid, int(diff)))
            elif diff <= -1:
                trims.append((iid, int(-diff)))
        for iid in exits:
            qty = held[iid]
            (self.sell if qty > 0 else self.buy)(iid, abs(qty), reason="exit")
        for iid, qty in trims:
            self.sell(iid, qty, reason="rebalance")
        for iid, qty in buys:
            self.buy(iid, qty)

    def _checked_weights(self, members: set[InstrumentId]) -> dict[InstrumentId, float]:
        weights = dict(self.target_weights())
        for iid, w in weights.items():
            if not math.isfinite(w):
                raise ValueError(f"weight for {iid.symbol} must be finite, got {w}")
            if w < 0:
                raise ValueError(
                    f"weight for {iid.symbol} is negative ({w}); shorts are not supported yet"
                )
        outside = sorted(i.symbol for i in weights.keys() - members)
        if outside:
            raise ValueError(f"weights given for names outside the universe: {outside}")
        total = sum(weights.values())
        if total > 1.0 + _EPSILON:
            raise ValueError(f"weights sum to {total}, which exceeds 1.0")
        return weights

    def _log_membership(self, members: set[InstrumentId]) -> None:
        previous = self._tw.members
        for event, names in (
            ("EVENT_MEMBERSHIP_ADD", members - previous),
            ("EVENT_MEMBERSHIP_DEL", previous - members),
        ):
            if names:
                self.log_event(event, symbols=[i.symbol for i in sorted(names, key=_symbol_key)])

    def _portfolio_value(self) -> float:
        """Cash plus open positions marked at the last close (unpriced ones are skipped)."""
        value = self.ctx.cash().to_major()
        for iid, qty in self.ctx.positions().items():
            price = self._tw.prices.get(iid)
            if price is not None and price > 0:
                value += qty * price
        return value


class _TargetWeightState:
    __slots__ = ("day_changed", "days_since", "initial_done", "last_day", "members", "prices")

    def __init__(self) -> None:
        self.prices: dict[InstrumentId, float] = {}
        self.members: set[InstrumentId] = set()
        self.last_day: int | None = None
        self.days_since = 0
        self.day_changed = False
        self.initial_done = False
