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
* **Impact.** With ``impact=`` a fill never happens at the printed open: the price is
  degraded by :class:`honba.backtest.impact.MarketImpact` (square-root law, past sessions
  only) and costs are charged on that price. Implemented on the Python backend, which is
  what an ``impact=`` run selects.
* **Opening auction.** With ``auction=`` every fill at a printed open pays the adverse
  spread buffer of :class:`honba.backtest.opening_auction.OpeningAuction` (``spread_bps``,
  stacking with ``impact=``) and an order waits ``delay_bars`` extra driving bars after
  submission before it may fill — the post-open turbulence window of Balch pitfall #8.
  Also Python-backend only.
* **Cash.** Integer ``Money`` (ADR 0011), notional ``Money.mul_qty`` like the ledger. A
  buy debits ``notional + cost`` at once; a sell credits ``notional - cost`` at once but
  the proceeds only become *available* ``settlement_days`` sessions later.
* **Funding.** A buy that available cash cannot cover waits up to ``settlement_days``
  sessions for sale proceeds to settle, then is cut to the whole units cash allows; the
  rest is reported as a ``Rejected`` event (``"insufficient_funds"``), after the fill.
* **Long only** (default). A sell is capped at the position held; the rest is rejected
  (``"no_position"``).
* **Order types.** Market orders only; any other type is rejected
  (``"unsupported_order_type"``).
* **Cancel.** ``cancel(order_id, now)`` drops a working order and reports it as a ``Cancelled``
  event stamped ``now``.
* **Events.** ``drain_events()`` is the one ordered stream (ADR 0019): a ``Fill`` precedes the
  ``Rejected`` of its remainder. ``drain_fills()`` / ``drain_rejections()`` are the buffered
  legacy shim over it (removed in 0.3.0).

The settlement cycle and the cost schedule are market rules and are injected:
:func:`make_simulator` takes them from ``honba.markets.india``
(``settlement_days_for(exchange, as_of=...)``: NSE/BSE equities settle T+2 before
2023-01-27 and T+1 from then; an explicit ``settlement_days`` always wins). A session is
one bar, so the cycle counts bars: that equals trading days only for daily-or-longer
timeframes, and :func:`make_simulator` therefore requires an explicit ``settlement_days``
for intraday timeframes. Pure: no I/O and no wall clock.

**Backends.** :class:`NextOpenExecution` runs on the native Rust simulator
(``honba._honba.NextOpenSimulator``) when the extension is usable and on the pure-Python
reference otherwise; the two are interchangeable (shared conformance vectors, seeded parity
test). Pick one with ``backend="python" | "native" | "auto"`` or ``HONBA_SIM_BACKEND``
(ADR 0016, chunk 3b).
"""

from __future__ import annotations

import dataclasses
import math
import os
import re
from collections.abc import Callable, Iterator, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from honba import _native
from honba.backtest.impact import MarketImpact
from honba.backtest.opening_auction import OpeningAuction
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderType
from honba.entities.trade import Trade
from honba.strategies.execution import (
    BaseExecutionPort,
    Cancelled,
    ExecutionEvent,
    ExecutionPortLike,
    Fill,
    Rejected,
    events_from_native,
)

__all__ = [
    "BACKEND_ENV",
    "FillCostFn",
    "FillModel",
    "MarketImpact",
    "NextOpenExecution",
    "OpeningAuction",
    "SessionOpen",
    "fill_costs_from_model",
    "group_sessions",
    "is_intraday",
    "make_simulator",
    "resolve_backend",
    "resolve_fill_costs",
    "session_date",
    "zero_costs",
]

FillModel = Literal["bar_close", "next_open"]

Backend = Literal["python", "native"]
"""The implementation behind a :class:`NextOpenExecution`."""

BackendChoice = Literal["auto", "python", "native"]
"""``backend=`` values: ``auto`` takes native when the extension is usable, else Python."""

BACKEND_ENV = "HONBA_SIM_BACKEND"
"""Environment variable giving the default :data:`BackendChoice` (``auto`` when unset)."""

FillCostFn = Callable[[OrderSide, float, float], Money]
"""``(side, quantity, price) -> cost`` of one fill, never negative, in the port's currency."""


def zero_costs(side: OrderSide, quantity: float, price: float) -> Money:
    """No transaction costs."""
    return Money.zero(Currency.INR)


zero_costs.native_cost_pack = "none"  # type: ignore[attr-defined]
# ^ Cost functions that equal a named native cost pack carry its name in ``native_cost_pack``;
# the native backend then costs fills inside Rust instead of calling back into Python (only
# for INR ports: the packs charge rupees). See ``markets.india.costs`` for the India packs.


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


class _PythonSim:
    """The pure-Python next-open simulator: the reference implementation (ADR 0016).

    Behind :class:`NextOpenExecution` when the native extension is unavailable or the
    ``python`` backend is forced; the conformance vectors and the backend parity test keep it
    and the native simulator identical.
    """

    def __init__(
        self,
        *,
        cash: Money,
        settlement_days: int = 0,
        costs: FillCostFn = zero_costs,
        long_only: bool = True,
        lot_sizes: Mapping[InstrumentId, float] | None = None,
        impact: MarketImpact | None = None,
        auction: OpeningAuction | None = None,
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
        self._impact = impact
        self._auction = auction
        self._lot_sizes: dict[InstrumentId, float] = {}
        for iid, lot in (lot_sizes or {}).items():
            self.set_lot_size(iid, lot)
        self._long_only = long_only
        self._session = -1
        self._session_ts: int | None = None
        self._from_open = False  # the current session came from a SessionOpen event
        self._opened: set[InstrumentId] = set()
        self._working: list[_Working] = []
        self._receivables: list[tuple[int, Money]] = []  # (available from session, amount)
        self._events: list[ExecutionEvent] = []

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
        if intent.side not in (OrderSide.BUY, OrderSide.SELL):
            raise ValueError(f"order {order_id} has no side: it must be buy or sell")
        if any(w.order_id == order_id for w in self._working):
            raise ValueError(f"order id {order_id} is already working")
        if intent.order_type is not OrderType.MARKET:
            self._reject(order_id, intent, "unsupported_order_type", ts)
            return
        self._working.append(_Working(order_id, intent, self._session))

    def drain_events(self) -> list[ExecutionEvent]:
        out, self._events = self._events, []
        return out

    def cancel(self, order_id: str, now: int) -> None:
        for w in self._working:
            if w.order_id == order_id:
                self._working.remove(w)
                self._events.append(Cancelled(order_id, w.intent, now))
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

    def set_lot_size(self, instrument_id: InstrumentId, lot_size: float) -> None:
        """Quantity step a funding cut floors to for ``instrument_id`` (default 1)."""
        if not (math.isfinite(lot_size) and lot_size > 0):
            raise ValueError(f"lot_size must be positive, got {lot_size}")
        self._lot_sizes[instrument_id] = lot_size

    def _costs(self, side: OrderSide, qty: float, px: float) -> Money:
        cost = self._cost_fn(side, qty, px)
        if cost.amount == 0:
            return Money.zero(self._currency)  # a zero schedule is currency-neutral
        if cost.amount < 0:
            raise ValueError(f"fill cost must not be negative, got {cost}")
        if cost.currency is not self._currency:
            # Checked here, before an order is dequeued, so a failing cost leaves the order
            # working and the state unchanged (a sell used to fail only at the proceeds sum).
            raise ValueError(f"currency mismatch: fill cost {cost}, port {self._currency}")
        return cost

    def _now(self) -> int:
        return self._session_ts or 0

    def _fill_at(self, bars: Sequence[Bar]) -> None:
        opens: dict[InstrumentId, Bar] = {}
        for b in bars:
            if b.instrument_id not in self._opened and _usable_open(b.open):
                opens.setdefault(b.instrument_id, b)
        self._opened.update(opens)
        # The post-open delay holds every order back for ``delay_bars`` extra sessions.
        delay = self._auction.delay_bars if self._auction is not None else 0
        eligible = [
            w
            for w in self._working
            if w.session + delay < self._session and w.intent.instrument_id in opens
        ]
        for w in eligible:
            if w.intent.side is OrderSide.SELL:
                self._fill_sell(w, opens[w.intent.instrument_id])
        for w in eligible:
            if w.intent.side is OrderSide.BUY:
                self._fill_buy(w, opens[w.intent.instrument_id])
        # After the fills: a session's own volume/close only ever informs *later* fills.
        if self._impact is not None:
            for b in bars:
                self._impact.observe(b.instrument_id, volume=b.volume, close=b.close)

    def _exec_price(self, side: OrderSide, bar: Bar, quantity: float) -> float:
        """The fill price: the printed open adverse of the auction buffer and impact, if any."""
        fraction = 0.0
        if self._auction is not None:
            fraction += self._auction.fraction  # the opening auction is crossed whatever the size
        if self._impact is not None:
            fraction += self._impact.fraction(bar.instrument_id, quantity)
        if fraction <= 0.0:
            return bar.open
        px = bar.open * (1.0 + fraction) if side is OrderSide.BUY else bar.open * (1.0 - fraction)
        return px if _usable_open(px) else bar.open

    def _fill_sell(self, w: _Working, bar: Bar) -> None:
        want = w.intent.quantity
        qty = want
        if self._long_only:
            held = self.positions.get(w.intent.instrument_id, 0.0)
            qty = max(0.0, min(want, held))
            if 0 < qty < want and want - qty <= _QTY_EPS:
                want = qty  # a hair over the position is float residue: sell it all, no reject
        if qty > 0:  # compute before dequeuing: a failure leaves the order working
            px = self._exec_price(OrderSide.SELL, bar, qty)
            notional = Money.mul_qty(qty, px, self._currency)
            cost = self._costs(OrderSide.SELL, qty, px)
        self._working.remove(w)
        if qty > 0:  # the fill, then the remainder's rejection: one queue, in that order
            proceeds = notional - cost
            self.cash = self.cash + proceeds
            self._receivables.append((self._session + self.settlement_days, proceeds))
            self._book(w, bar, qty, notional, cost, qty >= want, price=px)
        if qty < want:
            self._reject_part(w, want - qty, "no_position")

    def _fill_buy(self, w: _Working, bar: Bar) -> None:
        if w.first_try is None:
            w.first_try = self._session
        want = w.intent.quantity
        px = self._exec_price(OrderSide.BUY, bar, want)
        available = self.available_cash
        if self._buy_cost(want, px).amount <= available.amount:
            qty = want
        elif self.unsettled.amount > 0 and self._session - w.first_try < self.settlement_days:
            return  # wait for pending sale proceeds to settle
        else:
            qty = self._affordable(
                want, px, available, self._lot_sizes.get(w.intent.instrument_id, 1.0)
            )
        if qty > 0:  # compute before dequeuing: a failure leaves the order working
            if qty != want:  # a funding cut shrinks the order, so reprice the impact
                px = self._exec_price(OrderSide.BUY, bar, qty)
            notional = Money.mul_qty(qty, px, self._currency)
            cost = self._costs(OrderSide.BUY, qty, px)
        self._working.remove(w)
        if qty > 0:  # the fill, then the remainder's rejection: one queue, in that order
            self.cash = self.cash - (notional + cost)
            self._book(w, bar, qty, notional, cost, qty >= want, price=px)
        if qty < want:
            self._reject_part(w, want - qty, "insufficient_funds")

    def _buy_cost(self, qty: float, px: float) -> Money:
        return Money.mul_qty(qty, px, self._currency) + self._costs(OrderSide.BUY, qty, px)

    def _affordable(self, want: float, px: float, available: Money, lot: float = 1.0) -> float:
        """Largest whole number of lots (<= ``want``) whose notional plus cost fits ``available``.

        Costs are assumed non-decreasing in quantity, so the answer is found by bisection
        between zero and the cost-free bound instead of stepping down one lot at a time.
        """
        if px <= 0 or available.amount <= 0:
            return 0.0
        hi = min(
            math.floor(want / lot + _QTY_EPS),
            math.floor(available.to_major() / px / lot + _QTY_EPS),
        )
        lo = 0
        while lo < hi:  # invariant: lo lots are affordable (0 trivially), hi + 1 are not
            mid = (lo + hi + 1) // 2
            if self._buy_cost(mid * lot, px).amount <= available.amount:
                lo = mid
            else:
                hi = mid - 1
        return lo * lot

    def _book(
        self,
        w: _Working,
        bar: Bar,
        qty: float,
        notional: Money,
        cost: Money,
        complete: bool,
        *,
        price: float,
    ) -> None:
        iid = w.intent.instrument_id
        signed = qty if w.intent.side is OrderSide.BUY else -qty
        held = self.positions.get(iid, 0.0) + signed
        if abs(held) <= _QTY_EPS:
            self.positions.pop(iid, None)
        else:
            self.positions[iid] = held
        self.fees = self.fees + cost
        self.traded_notional = self.traded_notional + notional
        trade = Trade(iid, w.intent.side, qty, price, bar.ts, w.order_id, costs=cost)
        # One fill per order: its cumulative quantity is its own.
        self._events.append(Fill(trade, qty, complete))

    def _reject_part(self, w: _Working, qty: float, reason: str) -> None:
        self._reject(w.order_id, dataclasses.replace(w.intent, quantity=qty), reason, self._now())

    def _reject(self, order_id: str, intent: OrderIntent, reason: str, ts: int) -> None:
        self._events.append(Rejected(order_id, intent, reason, ts))


_QTY_EPS = 1e-9  # quantities closer than this are equal (float residue from fractional fills)


def _usable_open(price: float) -> bool:
    return math.isfinite(price) and price > 0


# -- backend selection ---------------------------------------------------------------------------
_NATIVE_METHODS = ("set_position", "set_lot_size", "on_session_open")
_NATIVE_PROPERTIES = ("session_ts", "available_cash", "unsettled")


def _native_simulator() -> Any:
    """The native ``NextOpenSimulator`` class, or ``ImportError`` / ``RuntimeError`` if unusable."""
    cls = _native.native_attr("NextOpenSimulator")
    missing = [n for n in (*_NATIVE_METHODS, *_NATIVE_PROPERTIES) if not hasattr(cls, n)]
    if missing:
        raise RuntimeError(
            f"honba._honba.NextOpenSimulator lacks {missing}: the compiled extension is stale, "
            "rebuild it (maturin develop)"
        )
    return cls


def resolve_backend(requested: BackendChoice | None = None) -> Backend:
    """The backend a :class:`NextOpenExecution` built now would run on.

    ``requested`` (``"auto"``, ``"python"``, ``"native"``) wins over the ``HONBA_SIM_BACKEND``
    environment variable, which wins over the default ``"auto"``. ``auto`` is ``native`` when
    ``honba._honba`` is importable and current, else ``python``; ``native`` raises the
    ``ImportError`` / ``RuntimeError`` of the unusable extension, with the backend named.
    """
    if requested is None:
        raw = os.environ.get(BACKEND_ENV, "").strip().lower() or "auto"
        origin = f"{BACKEND_ENV}={os.environ.get(BACKEND_ENV)!r}"
    else:
        raw, origin = str(requested).strip().lower(), f"backend={requested!r}"
    if raw not in ("auto", "python", "native"):
        raise ValueError(f"{origin}: backend must be 'auto', 'python' or 'native'")
    if raw == "python":
        return "python"
    try:
        _native_simulator()
    except ImportError as exc:
        if raw == "native":
            raise ImportError(f"the native simulator backend was requested: {exc}") from exc
        return "python"
    except RuntimeError as exc:
        if raw == "native":
            raise RuntimeError(f"the native simulator backend was requested: {exc}") from exc
        return "python"
    return "native"


class _NativePositions(MutableMapping[InstrumentId, float]):
    """Live view of the native simulator's positions with the ``dict`` the Python port exposes.

    Reading takes a snapshot; ``positions[iid] = qty`` seeds a holding (zero removes it) and
    ``del positions[iid]`` flattens one.
    """

    def __init__(self, sim: Any) -> None:
        self._sim = sim

    def _snapshot(self) -> dict[InstrumentId, float]:
        return {InstrumentId(s, e): q for s, e, q in self._sim.positions}

    def __getitem__(self, key: InstrumentId) -> float:
        return self._snapshot()[key]

    def __setitem__(self, key: InstrumentId, value: float) -> None:
        self._sim.set_position(key.symbol, float(value), key.exchange)

    def __delitem__(self, key: InstrumentId) -> None:
        if key not in self._snapshot():
            raise KeyError(key)
        self._sim.set_position(key.symbol, 0.0, key.exchange)

    def __iter__(self) -> Iterator[InstrumentId]:
        return iter(self._snapshot())

    def __len__(self) -> int:
        return len(self._sim.positions)

    def __repr__(self) -> str:
        return repr(self._snapshot())


def _native_costs(costs: FillCostFn, currency: Currency) -> Any:
    """The ``costs=`` argument of the native class for a Python ``FillCostFn``.

    A function marked with ``native_cost_pack`` goes in by name (INR only). Any other callable
    is bridged: Rust passes ``(side, quantity, price)`` and takes minor units back; the bridge
    keeps :meth:`_PythonSim._costs` rules: a zero cost is currency-neutral, a negative one is
    refused by the simulator, a non-zero cost in another currency is a ``ValueError``.
    """
    pack = getattr(costs, "native_cost_pack", None)
    if pack is not None and currency is Currency.INR:
        return pack

    def bridge(side: str, quantity: float, price: float) -> int:
        cost = costs(OrderSide.BUY if side == "buy" else OrderSide.SELL, quantity, price)
        if cost.amount == 0:
            return 0
        if cost.amount > 0 and cost.currency is not currency:
            raise ValueError(f"currency mismatch: fill cost {cost}, port {currency}")
        return cost.amount  # a negative amount is a ValueError in the simulator

    return bridge


def _user_id(native_id: str) -> str:
    """The caller's order id from a native id ``"<seq>:<order_id>"``."""
    return native_id.partition(":")[2]


def _bar_dict(bar: Bar) -> dict[str, Any]:
    iid = bar.instrument_id
    return {
        "symbol": iid.symbol,
        "exchange": iid.exchange,
        "ts": bar.ts,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }


class _NativeSim:
    """The :class:`_PythonSim` surface on top of ``honba._honba.NextOpenSimulator``.

    Converts ``Money`` to and from integer minor units, ``Bar`` / ``OrderIntent`` to the
    binding's dicts and its drained dicts back to ``Trade`` / ``OrderRejection``. Rebuilding a
    rejection's intent needs the original (order type, price, trigger). An id may be reused once
    its order is done while that order's rejection is still undrained, so every accepted order
    gets a unique native id ``"<seq>:<order_id>"``, and the intent is kept under it until the
    rejections are drained.
    """

    def __init__(
        self,
        *,
        cash: Money,
        settlement_days: int,
        costs: FillCostFn,
        long_only: bool,
        lot_sizes: Mapping[InstrumentId, float] | None,
    ) -> None:
        if settlement_days < 0:
            raise ValueError("settlement_days must be >= 0")
        if cash.amount < 0:
            raise ValueError("cash must be >= 0")
        self._currency = cash.currency
        self._sim = _native_simulator()(
            cash.amount,
            cash.currency.value,
            settlement_days=settlement_days,
            long_only=long_only,
            costs=_native_costs(costs, cash.currency),
        )
        self._intents: dict[str, OrderIntent] = {}  # by native id
        self._seq = 0
        self.positions: MutableMapping[InstrumentId, float] = _NativePositions(self._sim)
        for iid, lot in (lot_sizes or {}).items():
            self.set_lot_size(iid, lot)

    def _money(self, minor: int) -> Money:
        return Money.from_minor(minor, self._currency)

    @property
    def settlement_days(self) -> int:
        return int(self._sim.settlement_days)

    @property
    def cash(self) -> Money:
        return self._money(self._sim.cash)

    @property
    def fees(self) -> Money:
        return self._money(self._sim.fees)

    @property
    def traded_notional(self) -> Money:
        return self._money(self._sim.traded_notional)

    @property
    def unsettled(self) -> Money:
        return self._money(self._sim.unsettled)

    @property
    def available_cash(self) -> Money:
        return self._money(self._sim.available_cash)

    @property
    def working_orders(self) -> list[str]:
        return [_user_id(w) for w in self._sim.working_orders]

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        if intent.side not in (OrderSide.BUY, OrderSide.SELL):
            raise ValueError(f"order {order_id} has no side: it must be buy or sell")
        if any(_user_id(w) == order_id for w in self._sim.working_orders):
            raise ValueError(f"order id {order_id} is already working")
        iid = intent.instrument_id
        native_id = f"{self._seq}:{order_id}"
        self._sim.submit(
            {
                "id": native_id,
                "symbol": iid.symbol,
                "exchange": iid.exchange,
                "side": intent.side.value,
                "type": intent.order_type.value,
                "qty": intent.quantity,
                "price": intent.price,
                "trigger": intent.trigger_price,
                "ts": ts,
            }
        )
        self._seq += 1
        self._intents[native_id] = intent

    def cancel(self, order_id: str, now: int) -> None:
        for native_id in self._sim.working_orders:
            if _user_id(native_id) == order_id:
                self._sim.cancel(native_id, now)
                return

    def drain_events(self) -> list[ExecutionEvent]:
        out: list[ExecutionEvent] = []
        for raw in self._sim.drain_events():
            native_id = raw["order_id"]

            def intent_of(_: str, default: OrderIntent, native_id: str = native_id) -> OrderIntent:
                # The remembered intent (order type, prices) with the event's quantity.
                intent = self._intents.get(native_id)
                if intent is None:  # pragma: no cover - every accepted order is remembered
                    return default
                if intent.quantity != default.quantity:
                    intent = dataclasses.replace(intent, quantity=default.quantity)
                return intent

            out.extend(
                events_from_native(
                    [raw], currency=self._currency, user_id=_user_id, intent_of=intent_of
                )
            )
        working = set(self._sim.working_orders)
        self._intents = {k: v for k, v in self._intents.items() if k in working}
        return out

    def on_event(self, event: Any, ts_init: int) -> None:
        if isinstance(event, SessionOpen):
            self._sim.on_session_open(event.ts, [_bar_dict(b) for b in event.bars])
        elif isinstance(event, Bar):
            self._sim.on_bar(_bar_dict(event))

    def open_session(self, ts: int, bars: Sequence[Bar]) -> None:
        self._sim.open_session(ts, [_bar_dict(b) for b in bars])

    def set_settlement_days(self, settlement_days: int) -> None:
        self._sim.set_settlement_days(settlement_days)

    def set_lot_size(self, instrument_id: InstrumentId, lot_size: float) -> None:
        if not (math.isfinite(lot_size) and lot_size > 0):
            raise ValueError(f"lot_size must be positive, got {lot_size}")
        self._sim.set_lot_size(instrument_id.symbol, lot_size, instrument_id.exchange)


class NextOpenExecution(BaseExecutionPort):
    """``ExecutionPort`` filling market orders at the next session's open (see module docs).

    Runs on the native ``honba._honba.NextOpenSimulator`` when the extension is usable and on
    the pure-Python reference otherwise; both give identical results (ADR 0016, shared
    conformance vectors and a seeded parity test). ``backend`` (``"auto"`` by default, or the
    ``HONBA_SIM_BACKEND`` environment variable) forces one; :attr:`backend` reports the choice.
    """

    def __init__(
        self,
        *,
        cash: Money,
        settlement_days: int = 0,
        costs: FillCostFn = zero_costs,
        long_only: bool = True,
        lot_sizes: Mapping[InstrumentId, float] | None = None,
        backend: BackendChoice | None = None,
        impact: MarketImpact | None = None,
        auction: OpeningAuction | None = None,
    ) -> None:
        if impact is not None and backend == "native":
            raise ValueError(
                "impact= needs the Python backend (the Rust simulator has no market-impact "
                "model yet): drop backend='native'"
            )
        if auction is not None and backend == "native":
            raise ValueError(
                "auction= needs the Python backend (the Rust simulator has no opening-auction "
                "model yet): drop backend='native'"
            )
        # ``auto`` with impact/auction resolves to python: both are only implemented there today.
        self._backend: Backend = (
            "python" if (impact is not None or auction is not None) else resolve_backend(backend)
        )
        if self._backend == "native":
            self._impl: _PythonSim | _NativeSim = _NativeSim(
                cash=cash,
                settlement_days=settlement_days,
                costs=costs,
                long_only=long_only,
                lot_sizes=lot_sizes,
            )
        else:
            self._impl = _PythonSim(
                cash=cash,
                settlement_days=settlement_days,
                costs=costs,
                long_only=long_only,
                lot_sizes=lot_sizes,
                impact=impact,
                auction=auction,
            )

    @property
    def backend(self) -> Backend:
        """``"native"`` or ``"python"``: the implementation this port runs on."""
        return self._backend

    # -- state ---------------------------------------------------------------------------
    @property
    def settlement_days(self) -> int:
        """Sessions after a sale before its proceeds are available."""
        return self._impl.settlement_days

    @property
    def cash(self) -> Money:
        """Booked cash (sale proceeds count at once, see :attr:`available_cash`)."""
        return self._impl.cash

    @property
    def fees(self) -> Money:
        """Transaction costs paid so far."""
        return self._impl.fees

    @property
    def traded_notional(self) -> Money:
        """Sum of the notional of every fill."""
        return self._impl.traded_notional

    @property
    def positions(self) -> MutableMapping[InstrumentId, float]:
        """Net quantity per instrument; assigning an entry seeds a holding."""
        return self._impl.positions

    @property
    def unsettled(self) -> Money:
        """Sale proceeds booked but not yet available."""
        return self._impl.unsettled

    @property
    def available_cash(self) -> Money:
        """Cash that may fund a buy now: booked cash less unsettled sale proceeds."""
        return self._impl.available_cash

    @property
    def working_orders(self) -> list[str]:
        """Ids of orders not yet filled, rejected or cancelled, in submission order."""
        return self._impl.working_orders

    # -- ExecutionPort -------------------------------------------------------------------
    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        self._impl.submit(order_id, intent, ts)

    def drain_events(self) -> list[ExecutionEvent]:
        """Return and clear the ordered execution events (a fill precedes its remainder's reject).

        ``drain_fills()`` / ``drain_rejections()`` are the buffered legacy split of this stream.
        """
        return self._impl.drain_events()

    def cancel(self, order_id: str, now: int) -> None:
        """Cancel a working order; the ``Cancelled`` event is stamped ``now``."""
        self._impl.cancel(order_id, now)

    # -- session driver ------------------------------------------------------------------
    def on_event(self, event: Any, ts_init: int) -> None:
        """Observe the runner's event stream (called before the strategy sees it)."""
        self._impl.on_event(event, ts_init)

    def open_session(self, ts: int, bars: Sequence[Bar]) -> None:
        """Start session ``ts``: settle due proceeds, then fill eligible orders at the opens."""
        self._impl.open_session(ts, bars)

    def set_settlement_days(self, settlement_days: int) -> None:
        """Change the settlement cycle before the first session opens."""
        self._impl.set_settlement_days(settlement_days)

    def set_lot_size(self, instrument_id: InstrumentId, lot_size: float) -> None:
        """Quantity step a funding cut floors to for ``instrument_id`` (default 1)."""
        self._impl.set_lot_size(instrument_id, lot_size)


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


def fill_costs_from_model(model: Any, *, currency: Currency = Currency.INR) -> FillCostFn:
    """Adapt a post-hoc ``CostModel`` (``apply(trade) -> trade``) to a :data:`FillCostFn`.

    The model sees a zero-cost :class:`Trade` for the fill and its returned ``costs`` is the
    fill's cost; the simulator then charges it to cash, so fills, ledger, equity and metrics
    agree. Only ``costs`` is read (a model cannot change price or quantity here). The trade
    carries a placeholder instrument, so a model must not depend on the symbol; use a
    ``FillCostFn`` for that.
    """
    placeholder = InstrumentId("_", "_")

    def cost(side: OrderSide, quantity: float, price: float) -> Money:
        return model.apply(
            Trade(placeholder, side, quantity, price, costs=Money.zero(currency))
        ).costs

    return cost


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
    lot_sizes: Mapping[InstrumentId, float] | None = None,
    impact: MarketImpact | None = None,
    auction: OpeningAuction | None = None,
) -> ExecutionPortLike:
    """Build the simulated port for ``fill``.

    * ``"next_open"``: :class:`NextOpenExecution`; ``settlement_days`` defaults to the
      market pack's cycle for ``exchange`` on ``as_of`` (today's cycle when None;
      ``honba.markets.india.settlement_days_for``). Intraday ``timeframe`` values need an
      explicit ``settlement_days`` because the port counts bars, not trading days.
      ``auction.delay_bars`` (Balch pitfall #8) also counts driving bars, so it needs an
      intraday ``timeframe``.
    * ``"bar_close"``: the single-price conformance simulator
      ``honba.strategies.testing.BarCloseFills`` (fills at the decision bar's close; costs
      and cash rules do not apply). Kept for the cross-language conformance suite.
    """
    if fill == "next_open":
        if auction is not None and auction.delay_bars > 0 and not is_intraday(timeframe):
            raise ValueError(
                f"auction.delay_bars={auction.delay_bars} counts driving bars, and timeframe "
                f"{timeframe!r} is not intraday: the post-open delay only means anything "
                "sub-daily (e.g. timeframe='1m' with delay_bars=5..15)"
            )
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
            cash=cash,
            settlement_days=settlement_days,
            costs=cost_fn,
            long_only=long_only,
            lot_sizes=lot_sizes,
            impact=impact,
            auction=auction,
        )
    if fill == "bar_close":
        if impact is not None:
            raise ValueError(
                "impact= requires fill='next_open': the bar-close conformance simulator has "
                "no market-impact model"
            )
        if auction is not None:
            raise ValueError(
                "auction= requires fill='next_open': the bar-close conformance simulator has "
                "no opening-auction model"
            )
        from honba.strategies.testing import BarCloseFills

        return BarCloseFills(currency=cash.currency)
    raise ValueError(f"unknown fill model {fill!r}; expected 'next_open' or 'bar_close'")
