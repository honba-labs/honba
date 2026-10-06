"""Simulated execution ports for backtests (the Python side of ``honba-sim``).

:class:`NextOpenExecution` is a deterministic multi-instrument ``ExecutionPort``
(``honba.strategies.execution``) that fills market orders at the next session's open.
Rules:

* **Sessions.** A session is one point on the driving clock, identified by an
  increasing integer (the bars' ``ts`` by default). Open one explicitly with
  :meth:`NextOpenExecution.open_session` or a :class:`SessionOpen` event (all of the
  session's bars at once), or let plain ``Bar`` events open it: the first bar with a new
  ``ts`` opens a session, later bars with that ``ts`` open their own instruments.
* **Timing.** An order submitted during session *k* fills at the open of the
  instrument's first bar in a later session; it never sees the close that produced it.
  If the instrument does not print, the order waits.
* **Order within a session.** For the instruments opened together, sells fill before
  buys; each side in submission order.
* **Cash.** Integer ``Money`` (ADR 0011), notional ``Money.mul_qty`` like the ledger. A
  buy debits ``notional + cost`` at once; a sell credits ``notional - cost`` at once but
  the proceeds only become *available* ``settlement_days`` sessions later.
* **Funding.** A buy that available cash cannot cover waits up to ``settlement_days``
  sessions for sale proceeds to settle, then is cut to the whole units cash allows; the
  rest is reported as an ``OrderRejection`` (``"insufficient_funds"``).
* **Long only** (default). A sell is capped at the position held; the rest is rejected
  (``"no_position"``).
* **Order types.** Market orders only; any other type is rejected
  (``"unsupported_order_type"``).
* **Cancel.** ``cancel(order_id)`` drops a working order and reports it cancelled.

The settlement cycle and the cost schedule are market rules and are injected:
:func:`make_simulator` takes them from ``honba.markets.india``
(``settlement_days_for(exchange, as_of=...)``: NSE/BSE equities settle T+2 before
2023-01-27 and T+1 from then; an explicit ``settlement_days`` always wins). A session is
one bar, so the cycle counts bars: that equals trading days only for daily-or-longer
timeframes, and :func:`make_simulator` therefore requires an explicit ``settlement_days``
for intraday timeframes. Pure: no I/O and no wall clock.
"""

from __future__ import annotations

import dataclasses
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderType
from honba.entities.trade import Trade
from honba.strategies.execution import BaseExecutionPort, ExecutionPort, OrderRejection

__all__ = [
    "FillCostFn",
    "FillModel",
    "NextOpenExecution",
    "SessionOpen",
    "group_sessions",
    "is_intraday",
    "make_simulator",
    "resolve_fill_costs",
    "session_date",
    "zero_costs",
]

FillModel = Literal["bar_close", "next_open"]

FillCostFn = Callable[[OrderSide, float, float], Money]
"""``(side, quantity, price) -> cost`` of one fill, never negative, in the port's currency."""


def zero_costs(side: OrderSide, quantity: float, price: float) -> Money:
    """No transaction costs."""
    return Money.zero(Currency.INR)


@dataclass(frozen=True, slots=True)
class SessionOpen:
    """Opens session ``ts`` with every bar that opens it (one per instrument).

    Feed it to the runner before the session's bars (``group_sessions`` does) so sells
    of every instrument fill before buys of any. The runner dispatches it to no hook.
    """

    ts: int
    bars: tuple[Bar, ...]


@dataclass
class _Working:
    order_id: str
    intent: OrderIntent
    session: int  # index of the session it was submitted in
    first_try: int | None = None  # first session it was eligible and considered


class NextOpenExecution(BaseExecutionPort):
    """``ExecutionPort`` filling market orders at the next session's open (see module docs)."""

    def __init__(
        self,
        *,
        cash: Money,
        settlement_days: int = 0,
        costs: FillCostFn = zero_costs,
        long_only: bool = True,
    ) -> None:
        if settlement_days < 0:
            raise ValueError("settlement_days must be >= 0")
        if cash.amount < 0:
            raise ValueError("cash must be >= 0")
        self.settlement_days = settlement_days
        self.cash = cash
        self.fees = Money.zero(cash.currency)
        self.traded_notional = Money.zero(cash.currency)
        self.positions: dict[InstrumentId, float] = {}
        self._currency = cash.currency
        self._cost_fn = costs
        self._long_only = long_only
        self._session = -1
        self._session_ts: int | None = None
        self._from_open = False  # the current session came from a SessionOpen event
        self._opened: set[InstrumentId] = set()
        self._working: list[_Working] = []
        self._receivables: list[tuple[int, Money]] = []  # (available from session, amount)
        self._fills: list[Trade] = []
        self._rejections: list[OrderRejection] = []

    # -- cash ----------------------------------------------------------------------
    @property
    def unsettled(self) -> Money:
        """Sale proceeds booked but not yet available."""
        total = Money.zero(self._currency)
        for due, amount in self._receivables:
            if due > self._session:
                total = total + amount
        return total

    @property
    def available_cash(self) -> Money:
        """Cash that may fund a buy now: booked cash less unsettled sale proceeds."""
        return self.cash - self.unsettled

    @property
    def working_orders(self) -> list[str]:
        """Ids of orders not yet filled, rejected or cancelled, in submission order."""
        return [w.order_id for w in self._working]

    # -- ExecutionPort -------------------------------------------------------------
    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        if intent.order_type is not OrderType.MARKET:
            self._reject(order_id, intent, "unsupported_order_type", ts)
            return
        self._working.append(_Working(order_id, intent, self._session))

    def drain_fills(self) -> list[Trade]:
        fills, self._fills = self._fills, []
        return fills

    def drain_rejections(self) -> list[OrderRejection]:
        out, self._rejections = self._rejections, []
        return out

    def cancel(self, order_id: str) -> None:
        for w in self._working:
            if w.order_id == order_id:
                self._working.remove(w)
                self._rejections.append(
                    OrderRejection(order_id, w.intent, "cancelled", self._now(), cancelled=True)
                )
                return

    # -- session driver ------------------------------------------------------------
    def on_event(self, event: Any, ts_init: int) -> None:
        """Observe the runner's event stream (called before the strategy sees it)."""
        if isinstance(event, SessionOpen):
            self.open_session(event.ts, event.bars)
            self._from_open = True
        elif isinstance(event, Bar):
            if self._from_open:
                # The SessionOpen carried this session's bars and its key need not be a
                # timestamp, so a bar never opens or orders sessions here; it only
                # opens an instrument the SessionOpen left out, at the same ts.
                if event.ts == self._session_ts and event.instrument_id not in self._opened:
                    self._fill_at([event])
            elif self._session_ts is None or event.ts > self._session_ts:
                self.open_session(event.ts, [event])
                self._from_open = False
            elif event.ts < self._session_ts:
                raise ValueError(
                    f"non-monotonic bar: ts {event.ts} is before session {self._session_ts}"
                )
            elif event.instrument_id in self._opened:
                raise ValueError(
                    f"duplicate bar for {event.instrument_id} at ts {event.ts} in session "
                    f"{self._session_ts}"
                )
            else:
                self._fill_at([event])

    def open_session(self, ts: int, bars: Sequence[Bar]) -> None:
        """Start session ``ts``: settle due proceeds, then fill eligible orders at the opens."""
        if self._session_ts is not None and ts <= self._session_ts:
            raise ValueError(f"session {ts} does not follow session {self._session_ts}")
        self._session += 1
        self._session_ts = ts
        self._opened = set()
        self._receivables = [(d, a) for d, a in self._receivables if d > self._session]
        self._fill_at(bars)

    # -- internals -----------------------------------------------------------------
    def set_settlement_days(self, settlement_days: int) -> None:
        """Change the settlement cycle before the first session opens."""
        if settlement_days < 0:
            raise ValueError("settlement_days must be >= 0")
        if self._session_ts is not None:
            raise RuntimeError("settlement_days can only change before the first session")
        self.settlement_days = settlement_days

    def _costs(self, side: OrderSide, qty: float, px: float) -> Money:
        cost = self._cost_fn(side, qty, px)
        if cost.amount == 0:
            return Money.zero(self._currency)  # a zero schedule is currency-neutral
        if cost.amount < 0:
            raise ValueError(f"fill cost must not be negative, got {cost}")
        return cost

    def _now(self) -> int:
        return self._session_ts or 0

    def _fill_at(self, bars: Sequence[Bar]) -> None:
        opens: dict[InstrumentId, Bar] = {}
        for b in bars:
            if b.instrument_id not in self._opened and _usable_open(b.open):
                opens.setdefault(b.instrument_id, b)
        self._opened.update(opens)
        eligible = [
            w
            for w in self._working
            if w.session < self._session and w.intent.instrument_id in opens
        ]
        for w in eligible:
            if w.intent.side is OrderSide.SELL:
                self._fill_sell(w, opens[w.intent.instrument_id])
        for w in eligible:
            if w.intent.side is OrderSide.BUY:
                self._fill_buy(w, opens[w.intent.instrument_id])

    def _fill_sell(self, w: _Working, bar: Bar) -> None:
        want = w.intent.quantity
        qty = want
        if self._long_only:
            qty = max(0.0, min(want, self.positions.get(w.intent.instrument_id, 0.0)))
        if qty > 0:  # compute before dequeuing: a failure leaves the order working
            notional = Money.mul_qty(qty, bar.open, self._currency)
            cost = self._costs(OrderSide.SELL, qty, bar.open)
        self._working.remove(w)
        if qty < want:
            self._reject_part(w, want - qty, "no_position")
        if qty <= 0:
            return
        proceeds = notional - cost
        self.cash = self.cash + proceeds
        self._receivables.append((self._session + self.settlement_days, proceeds))
        self._book(w, bar, qty, notional, cost)

    def _fill_buy(self, w: _Working, bar: Bar) -> None:
        if w.first_try is None:
            w.first_try = self._session
        px, want = bar.open, w.intent.quantity
        available = self.available_cash
        if self._buy_cost(want, px).amount <= available.amount:
            qty = want
        elif self.unsettled.amount > 0 and self._session - w.first_try < self.settlement_days:
            return  # wait for pending sale proceeds to settle
        else:
            qty = self._affordable(want, px, available)
        if qty > 0:  # compute before dequeuing: a failure leaves the order working
            notional = Money.mul_qty(qty, px, self._currency)
            cost = self._costs(OrderSide.BUY, qty, px)
        self._working.remove(w)
        if qty < want:
            self._reject_part(w, want - qty, "insufficient_funds")
        if qty <= 0:
            return
        self.cash = self.cash - (notional + cost)
        self._book(w, bar, qty, notional, cost)

    def _buy_cost(self, qty: float, px: float) -> Money:
        return Money.mul_qty(qty, px, self._currency) + self._costs(OrderSide.BUY, qty, px)

    def _affordable(self, want: float, px: float, available: Money) -> float:
        if px <= 0 or available.amount <= 0:
            return 0.0
        qty = float(min(math.floor(want), math.floor(available.to_major() / px)))
        while qty > 0 and self._buy_cost(qty, px).amount > available.amount:
            qty -= 1
        return qty

    def _book(self, w: _Working, bar: Bar, qty: float, notional: Money, cost: Money) -> None:
        iid = w.intent.instrument_id
        signed = qty if w.intent.side is OrderSide.BUY else -qty
        held = self.positions.get(iid, 0.0) + signed
        if held == 0:
            self.positions.pop(iid, None)
        else:
            self.positions[iid] = held
        self.fees = self.fees + cost
        self.traded_notional = self.traded_notional + notional
        self._fills.append(Trade(iid, w.intent.side, qty, bar.open, bar.ts, w.order_id, costs=cost))

    def _reject_part(self, w: _Working, qty: float, reason: str) -> None:
        self._reject(w.order_id, dataclasses.replace(w.intent, quantity=qty), reason, self._now())

    def _reject(self, order_id: str, intent: OrderIntent, reason: str, ts: int) -> None:
        self._rejections.append(OrderRejection(order_id, intent, reason, ts))


def _usable_open(price: float) -> bool:
    return math.isfinite(price) and price > 0


def group_sessions(
    bars: Sequence[Bar], key: Callable[[Bar], int] | None = None
) -> list[tuple[Any, int]]:
    """Order ``bars`` into runner events: per session a :class:`SessionOpen`, then its bars.

    ``key`` maps a bar to its session (default: ``bar.ts``); sessions run in key order and
    bars within one by ``(ts, symbol, exchange)``. Each event's ``ts_init`` is the bar's
    ``ts`` (the ``SessionOpen`` takes its first bar's).
    """
    session_of = key or (lambda b: b.ts)
    ordered = sorted(
        bars, key=lambda b: (session_of(b), b.ts, b.instrument_id.symbol, b.instrument_id.exchange)
    )
    events: list[tuple[Any, int]] = []
    i = 0
    while i < len(ordered):
        k = session_of(ordered[i])
        j = i
        while j < len(ordered) and session_of(ordered[j]) == k:
            j += 1
        group = tuple(ordered[i:j])
        events.append((SessionOpen(k, group), group[0].ts))
        events.extend((b, b.ts) for b in group)
        i = j
    return events


_FILL_COSTS: dict[str, str] = {
    "none": "zero",
    "zero": "zero",
    "india.equity": "delivery",
    "india.equity.delivery": "delivery",
    "india.equity.intraday": "intraday",
}


def resolve_fill_costs(name: str) -> FillCostFn:
    """Map a cost-pack name to a fill-cost function (India schedules from ``markets.india``)."""
    kind = _FILL_COSTS.get(name.strip().lower())
    if kind is None:
        raise ValueError(f"unknown cost pack {name!r}; expected one of {sorted(_FILL_COSTS)}")
    if kind == "zero":
        return zero_costs
    from honba.markets.india.costs import (
        nse_equity_delivery_fill_cost,
        nse_equity_intraday_fill_cost,
    )

    return nse_equity_delivery_fill_cost if kind == "delivery" else nse_equity_intraday_fill_cost


_INTRADAY = re.compile(
    r"^\s*\d*\s*(s|sec|second|seconds|m|min|mins|minute|minutes|h|hr|hour|hours)\s*$", re.IGNORECASE
)
_IST_OFFSET = timedelta(hours=5, minutes=30)
_EPOCH = datetime(1970, 1, 1)  # noqa: DTZ001 - naive UTC arithmetic, shifted to IST above


def is_intraday(timeframe: str) -> bool:
    """True for sub-daily bar sizes ("1m", "5min", "1h", ...); "1d"/"1w" and unknowns are not."""
    return _INTRADAY.match(timeframe) is not None


def session_date(ts_ns: int) -> date:
    """Trading date (IST, the India market clock) of a unix-nanosecond timestamp."""
    return (_EPOCH + timedelta(seconds=ts_ns // 10**9) + _IST_OFFSET).date()


def make_simulator(
    *,
    fill: FillModel,
    cash: Money,
    costs: str | FillCostFn = "none",
    exchange: str = "NSE",
    settlement_days: int | None = None,
    long_only: bool = True,
    timeframe: str = "1d",
    as_of: date | None = None,
) -> ExecutionPort:
    """Build the simulated port for ``fill``.

    * ``"next_open"``: :class:`NextOpenExecution`; ``settlement_days`` defaults to the
      market pack's cycle for ``exchange`` on ``as_of`` (today's cycle when None;
      ``honba.markets.india.settlement_days_for``). Intraday ``timeframe`` values need an
      explicit ``settlement_days`` because the port counts bars, not trading days.
    * ``"bar_close"``: the single-price conformance simulator
      ``honba.strategies.testing.BarCloseFills`` (fills at the decision bar's close; costs
      and cash rules do not apply). Kept for the cross-language conformance suite.
    """
    if fill == "next_open":
        if settlement_days is None:
            if is_intraday(timeframe):
                raise ValueError(
                    f"timeframe {timeframe!r} is intraday: the simulator counts one session per "
                    "bar, not per trading day, so pass an explicit settlement_days"
                )
            from honba.markets.india.settlement import settlement_days_for

            settlement_days = settlement_days_for(exchange, as_of=as_of)
        cost_fn = resolve_fill_costs(costs) if isinstance(costs, str) else costs
        return NextOpenExecution(
            cash=cash, settlement_days=settlement_days, costs=cost_fn, long_only=long_only
        )
    if fill == "bar_close":
        from honba.strategies.testing import BarCloseFills

        return BarCloseFills(currency=cash.currency)
    raise ValueError(f"unknown fill model {fill!r}; expected 'next_open' or 'bar_close'")
