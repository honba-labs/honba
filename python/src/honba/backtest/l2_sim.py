"""L2 top-of-book execution port (ADR 0030): the Python side of ``honba_sim::L2Sim``.

:class:`L2Execution` is an ``ExecutionPort`` (``honba.strategies.execution``) that matches orders
against a :class:`~honba.domain.tick.QuoteTick` / :class:`~honba.domain.tick.TradeTick` stream
at nanosecond event time; there is no wall clock. It is opt-in: the default simulator stays the
L1 :class:`~honba.backtest.simulated.NextOpenExecution` (ADR 0025). Rules (v1):

* **Market**: fills at the opposite touch (buy at the ask, sell at the bid), up to the touch
  size; a remainder keeps working and fills against later quotes (cancelled under IOC).
* **Marketable limit**: walks the touch only (one level) at the touch price; the remainder
  rests at its limit (cancelled under IOC). Consumed touch size is gone until the next quote.
* **Resting limit**: fills at its limit when a trade prints through it (buy: ``price <= limit``,
  sell: ``price >= limit``), FIFO by submission, sharing the print's size. A new quote that
  crosses a resting limit fills it at the touch.
* **Stop / stop-limit**: armed until a trade crosses the trigger (buy: ``price >= trigger``,
  sell: ``price <= trigger``), then behaves as a market / limit order. **Trailing stops** are
  refused (``"unsupported_order_type"``), as in L1.
* **IOC / FOK**: judged once, against the book at submit (or trigger) time; FOK is
  all-or-nothing against the touch size, else rejected ``"fok_unfillable"``.
* **Day**: expires at the session close (default 23:59:00 UTC time of day) with an ``Expired``
  at the close instant, driven by event time (:meth:`L2Execution.advance_to` or the next event)
  or an explicit :meth:`L2Execution.on_session_close`; ``GTC`` / ``GTD`` never expire.
* **Cancel**: ``cancel(order_id, now)`` emits ``CancelRequested`` and ``Cancelled``, both
  stamped ``now`` (ADR 0019 decision 6, L2 v1).
* **Cash.** Integer ``Money`` (ADR 0011); a fill moves cash at once (no settlement cycle). A buy
  that cash cannot cover is cut to the whole lots cash allows and the rest rejected
  (``"insufficient_funds"``); with ``long_only`` (default) a sell is capped at the position held
  (``"no_position"``). Costs follow L1.

**Backends.** Like L1 it runs on the native Rust simulator (``honba._honba.L2Simulator``) when
the extension is usable and on the pure-Python reference otherwise; both replay
``schema/conformance/l2_sim.json`` and a seeded parity test keeps them identical. Pick one with
``backend="python" | "native" | "auto"`` or ``HONBA_SIM_BACKEND``. Slippage, latency and margin
models of the Rust core are not exposed here.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterator, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any, NamedTuple

from honba import _native
from honba.backtest.simulated import (
    Backend,
    BackendChoice,
    FillCostFn,
    _native_costs,
    _resolve,
    _user_id,
    zero_costs,
)
from honba.domain.money import Money
from honba.domain.tick import AggressorSide, QuoteTick, TradeTick
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderType, TimeInForce
from honba.entities.trade import Trade
from honba.strategies.execution import (
    BaseExecutionPort,
    Cancelled,
    CancelRequested,
    ExecutionEvent,
    Expired,
    Fill,
    Rejected,
    events_from_native,
)

__all__ = ["L2Execution", "TopOfBook"]

_QTY_EPS = 1e-9  # quantities closer than this are equal (float residue from fractional fills)
_NS_PER_DAY = 86_400_000_000_000
_DEFAULT_CLOSE_NS = 86_340_000_000_000  # 23:59:00 UTC into the day
_NATIVE_METHODS = ("advance_to", "on_session_close", "on_quote", "on_trade", "set_position")


class TopOfBook(NamedTuple):
    """Best bid / ask with the size left at each, as last quoted and drawn down by fills."""

    bid: float
    ask: float
    bid_size: float
    ask_size: float


def _native_l2() -> Any:
    """The native ``L2Simulator`` class, or ``ImportError`` / ``RuntimeError`` if unusable."""
    cls = _native.native_attr("L2Simulator")
    missing = [n for n in _NATIVE_METHODS if not hasattr(cls, n)]
    if missing:
        raise RuntimeError(
            f"honba._honba.L2Simulator lacks {missing}: the compiled extension is stale, "
            "rebuild it (maturin develop)"
        )
    return cls


@dataclass
class _Working:
    order_id: str
    intent: OrderIntent
    remaining: float
    filled: float = 0.0
    limit: float | None = None
    armed: float | None = None  # stop price while the stop is armed
    expire_at: int | None = None  # Day expiry instant


class _RefPositions(MutableMapping[InstrumentId, float]):
    """The reference's positions with the native semantics: a flat entry is absent."""

    def __init__(self) -> None:
        self._d: dict[InstrumentId, float] = {}

    def __getitem__(self, key: InstrumentId) -> float:
        return self._d[key]

    def __setitem__(self, key: InstrumentId, value: float) -> None:
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"position must be finite, got {value}")
        if abs(value) <= _QTY_EPS:
            self._d.pop(key, None)
        else:
            self._d[key] = value

    def __delitem__(self, key: InstrumentId) -> None:
        del self._d[key]

    def __iter__(self) -> Iterator[InstrumentId]:
        return iter(self._d)

    def __len__(self) -> int:
        return len(self._d)

    def __repr__(self) -> str:
        return repr(self._d)


def _usable(price: float | None) -> bool:
    return price is not None and math.isfinite(price) and price > 0


class _PythonL2:
    """The pure-Python L2 simulator: the reference twin of ``honba_sim::L2Sim`` (ADR 0030)."""

    def __init__(
        self,
        *,
        cash: Money,
        costs: FillCostFn,
        long_only: bool,
        lot_sizes: Mapping[InstrumentId, float] | None,
    ) -> None:
        if cash.amount < 0:
            raise ValueError("cash must be >= 0")
        self._currency = cash.currency
        self._cash = cash.amount
        self._fees = 0
        self._traded = 0
        self._cost_fn = costs
        self._long_only = long_only
        self._lot_sizes: dict[InstrumentId, float] = {}
        for iid, lot in (lot_sizes or {}).items():
            self.set_lot_size(iid, lot)
        self.positions: MutableMapping[InstrumentId, float] = _RefPositions()
        self._books: dict[InstrumentId, list[float]] = {}
        self._now = 0
        self._working: list[_Working] = []
        self._events: list[ExecutionEvent] = []

    # -- state ---------------------------------------------------------------------
    @property
    def cash(self) -> Money:
        return Money.from_minor(self._cash, self._currency)

    @property
    def fees(self) -> Money:
        return Money.from_minor(self._fees, self._currency)

    @property
    def traded_notional(self) -> Money:
        return Money.from_minor(self._traded, self._currency)

    @property
    def working_orders(self) -> list[str]:
        return [w.order_id for w in self._working]

    def book(self, instrument_id: InstrumentId) -> TopOfBook | None:
        b = self._books.get(instrument_id)
        return None if b is None else TopOfBook(*b)

    def set_lot_size(self, instrument_id: InstrumentId, lot_size: float) -> None:
        if not (math.isfinite(lot_size) and lot_size > 0):
            raise ValueError(f"lot_size must be positive, got {lot_size}")
        self._lot_sizes[instrument_id] = lot_size

    # -- ExecutionPort ---------------------------------------------------------------
    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        if intent.side not in (OrderSide.BUY, OrderSide.SELL):
            raise ValueError(f"order {order_id} has no side: it must be buy or sell")
        if any(w.order_id == order_id for w in self._working):
            raise ValueError(f"order id {order_id} is already working")
        ts = max(ts, self._now)
        self._advance(ts)
        qty = intent.quantity
        kind = intent.order_type
        limit: float | None = None
        armed: float | None = None
        if kind is OrderType.MARKET:
            pass
        elif kind is OrderType.LIMIT:
            limit = intent.price
        elif kind is OrderType.STOP_MARKET:
            armed = intent.trigger_price
        elif kind is OrderType.STOP_LIMIT:
            limit, armed = intent.price, intent.trigger_price
        else:
            self._reject(order_id, intent, qty, "unsupported_order_type", ts)
            return
        needs_limit = kind in (OrderType.LIMIT, OrderType.STOP_LIMIT)
        needs_stop = kind in (OrderType.STOP_MARKET, OrderType.STOP_LIMIT)
        if (
            not (math.isfinite(qty) and qty > 0)
            or (needs_limit and not _usable(limit))
            or (needs_stop and not _usable(armed))
        ):
            self._reject(order_id, intent, qty, "invalid_order", ts)
            return
        expire_at = self._close_after(ts) if intent.time_in_force is TimeInForce.DAY else None
        self._working.append(_Working(order_id, intent, qty, 0.0, limit, armed, expire_at))
        if armed is None:
            self._aggress(len(self._working) - 1, ts, True)

    def cancel(self, order_id: str, now: int) -> None:
        for w in self._working:
            if w.order_id == order_id:
                self._working.remove(w)
                # ADR 0019 decision 6, L2 v1: both stamped ``now``.
                self._events.append(CancelRequested(order_id, now))
                self._events.append(Cancelled(order_id, self._rest(w), now))
                return

    def drain_events(self) -> list[ExecutionEvent]:
        out, self._events = self._events, []
        return out

    # -- market events -----------------------------------------------------------------
    def on_event(self, event: Any, ts_init: int) -> None:
        if isinstance(event, QuoteTick):
            self.on_quote(event)
        elif isinstance(event, TradeTick):
            self.on_trade(event)

    def on_quote(self, q: QuoteTick) -> None:
        """Update the book, then match working orders in time priority."""
        self._advance(q.ts)
        self._books[q.instrument_id] = [q.bid_price, q.ask_price, q.bid_size, q.ask_size]
        i = 0
        while i < len(self._working):
            w = self._working[i]
            if (
                w.armed is None
                and w.intent.instrument_id == q.instrument_id
                and self._aggress(i, q.ts, False)
            ):
                continue
            i += 1

    def on_trade(self, t: TradeTick) -> None:
        """Fill resting limits the print trades through (FIFO, sharing its size), then stops."""
        ts, px = t.ts, t.price
        self._advance(ts)
        left = t.size
        i = 0
        while i < len(self._working):
            w = self._working[i]
            through = False
            if (
                w.armed is None
                and w.intent.instrument_id == t.instrument_id
                and w.limit is not None
            ):
                through = px <= w.limit if w.intent.side is OrderSide.BUY else px >= w.limit
            if through and left > _QTY_EPS:
                assert w.limit is not None
                filled, removed = self._execute(i, left, w.limit, ts, True)
                left -= filled
                if removed:
                    continue
            i += 1
        i = 0
        while i < len(self._working):
            w = self._working[i]
            hit = False
            if w.armed is not None and w.intent.instrument_id == t.instrument_id:
                hit = px >= w.armed if w.intent.side is OrderSide.BUY else px <= w.armed
            if hit:
                w.armed = None
                if self._aggress(i, ts, True):
                    continue
            i += 1

    def on_session_close(self, ts: int) -> None:
        """Every working Day order expires, stamped ``ts``."""
        self._now = max(self._now, ts)
        i = 0
        while i < len(self._working):
            if self._working[i].intent.time_in_force is TimeInForce.DAY:
                self._expire(i, ts)
            else:
                i += 1

    def advance_to(self, ts: int) -> None:
        """Move event time to ``ts`` with no market event."""
        self._advance(ts)

    # -- internals -----------------------------------------------------------------------
    def _advance(self, ts: int) -> None:
        i = 0
        while i < len(self._working):
            at = self._working[i].expire_at
            if at is not None and at <= ts:
                self._expire(i, at)
            else:
                i += 1
        self._now = max(self._now, ts)

    def _expire(self, idx: int, ts: int) -> None:
        w = self._working.pop(idx)
        self._events.append(Expired(w.order_id, self._rest(w), ts))

    @staticmethod
    def _close_after(now: int) -> int:
        candidate = now // _NS_PER_DAY * _NS_PER_DAY + _DEFAULT_CLOSE_NS
        return candidate + _NS_PER_DAY if candidate < now else candidate

    @staticmethod
    def _rest(w: _Working) -> OrderIntent:
        """The order's intent carrying its unfilled remainder."""
        if w.intent.quantity == w.remaining:
            return w.intent
        return dataclasses.replace(w.intent, quantity=w.remaining)

    def _reject(self, order_id: str, intent: OrderIntent, qty: float, reason: str, ts: int) -> None:
        if intent.quantity != qty:
            intent = dataclasses.replace(intent, quantity=qty)
        self._events.append(Rejected(order_id, intent, reason, ts))

    def _touch(self, iid: InstrumentId, side: OrderSide) -> tuple[float, float] | None:
        b = self._books.get(iid)
        if b is None:
            return None
        px, sz = (b[1], b[3]) if side is OrderSide.BUY else (b[0], b[2])
        return (px, sz) if (math.isfinite(px) and px > 0 and sz > 0) else None

    def _aggress(self, idx: int, ts: int, first: bool) -> bool:
        """Match order ``idx`` against the touch; True when it left the working set."""
        w = self._working[idx]
        side, tif, remaining = w.intent.side, w.intent.time_in_force, w.remaining
        iid = w.intent.instrument_id
        touch = self._touch(iid, side)
        if touch is not None and w.limit is not None:
            ok = touch[0] <= w.limit if side is OrderSide.BUY else touch[0] >= w.limit
            touch = touch if ok else None
        if first and tif is TimeInForce.FOK and (touch is None or touch[1] + _QTY_EPS < remaining):
            self._working.pop(idx)
            self._reject(w.order_id, w.intent, w.remaining, "fok_unfillable", ts)
            return True
        if touch is not None:
            px, sz = touch
            filled, removed = self._execute(idx, min(sz, remaining), px, ts, False)
            book = self._books.get(iid)
            if book is not None:
                k = 3 if side is OrderSide.BUY else 2
                book[k] = max(book[k] - filled, 0.0)
            if removed:
                return True
        if first and tif is TimeInForce.IOC:
            self._working.pop(idx)
            self._events.append(Cancelled(w.order_id, self._rest(w), ts))
            return True
        return False

    def _execute(
        self, idx: int, avail: float, base: float, ts: int, passive: bool
    ) -> tuple[float, bool]:
        """Fill up to ``avail`` of order ``idx`` at ``base``: caps, funding cut, costs."""
        w = self._working[idx]
        side = w.intent.side
        iid = w.intent.instrument_id
        qty = min(avail, w.remaining)
        cut: str | None = None
        if self._long_only and side is OrderSide.SELL:
            capped = max(0.0, min(qty, self.positions.get(iid, 0.0)))
            if capped < qty:
                cut = "no_position"
                qty = capped
        px = base
        if w.limit is not None:
            px = min(px, w.limit) if side is OrderSide.BUY else max(px, w.limit)
        if side is OrderSide.BUY and qty > 0.0 and self._buy_cost(qty, px) > self._cash:
            qty = self._affordable(qty, px, self._cash, self._lot_sizes.get(iid, 1.0))
            cut = "insufficient_funds"
        if qty > 0.0:
            notional = Money.mul_qty(qty, px, self._currency).amount
            cost = self._costs(side, qty, px)
            delta = -(notional + cost) if side is OrderSide.BUY else notional - cost
            w.remaining -= qty
            w.filled += qty
            self._cash += delta
            self._book_fill(w, px, qty, notional, cost, ts)
        if cut is not None:
            self._working.pop(idx)
            self._reject(w.order_id, w.intent, w.remaining, cut, ts)
            return qty, True
        if w.remaining <= _QTY_EPS:
            self._working.pop(idx)
            return qty, True
        return qty, False

    def _book_fill(
        self, w: _Working, price: float, qty: float, notional: int, cost: int, ts: int
    ) -> None:
        iid = w.intent.instrument_id
        signed = qty if w.intent.side is OrderSide.BUY else -qty
        held = self.positions.get(iid, 0.0) + signed
        self.positions.pop(iid, None)  # the native book re-appends: last updated is last
        if abs(held) > _QTY_EPS:
            self.positions[iid] = held
        self._fees += cost
        self._traded += notional
        trade = Trade(
            iid,
            w.intent.side,
            qty,
            price,
            ts,
            w.order_id,
            costs=Money.from_minor(cost, self._currency),
        )
        self._events.append(Fill(trade, w.filled, w.remaining <= _QTY_EPS))

    def _costs(self, side: OrderSide, qty: float, px: float) -> int:
        cost = self._cost_fn(side, qty, px)
        if cost.amount == 0:
            return 0
        if cost.currency is not self._currency:
            raise ValueError(f"currency mismatch: fill cost {cost}, port {self._currency}")
        if cost.amount < 0:
            raise ValueError(f"fill cost must not be negative, got {cost}")
        return cost.amount

    def _buy_cost(self, qty: float, px: float) -> int:
        return Money.mul_qty(qty, px, self._currency).amount + self._costs(OrderSide.BUY, qty, px)

    def _affordable(self, want: float, px: float, available: int, lot: float) -> float:
        """Largest whole number of lots (<= ``want``) whose notional plus cost fits."""
        if px <= 0 or available <= 0:
            return 0.0
        avail_major = Money.from_minor(available, self._currency).to_major()
        hi = int(
            min(
                math.floor(want / lot + _QTY_EPS),
                math.floor(avail_major / px / lot + _QTY_EPS),
            )
        )
        lo = 0
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._buy_cost(mid * lot, px) <= available:
                lo = mid
            else:
                hi = mid - 1
        return lo * lot


class _NativeL2:
    """The :class:`_PythonL2` surface on top of ``honba._honba.L2Simulator``.

    Same id and intent bookkeeping as the L1 native wrapper: an id may be reused once its order
    is done while its event is still undrained, so every accepted order gets a unique native
    id ``"<seq>:<order_id>"`` and its intent is kept until the events are drained.
    """

    def __init__(
        self,
        *,
        cash: Money,
        costs: FillCostFn,
        long_only: bool,
        lot_sizes: Mapping[InstrumentId, float] | None,
    ) -> None:
        if cash.amount < 0:
            raise ValueError("cash must be >= 0")
        self._currency = cash.currency
        self._sim = _native_l2()(
            cash.amount,
            cash.currency.value,
            long_only=long_only,
            costs=_native_costs(costs, cash.currency),
        )
        self._intents: dict[str, OrderIntent] = {}
        self._seq = 0
        self.positions: MutableMapping[InstrumentId, float] = _NativeL2Positions(self._sim)
        for iid, lot in (lot_sizes or {}).items():
            self.set_lot_size(iid, lot)

    def _money(self, minor: int) -> Money:
        return Money.from_minor(minor, self._currency)

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
    def working_orders(self) -> list[str]:
        return [_user_id(w) for w in self._sim.working_orders]

    def book(self, instrument_id: InstrumentId) -> TopOfBook | None:
        b = self._sim.book(instrument_id.symbol, instrument_id.exchange)
        return None if b is None else TopOfBook(*b)

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        if intent.side not in (OrderSide.BUY, OrderSide.SELL):
            raise ValueError(f"order {order_id} has no side: it must be buy or sell")
        if any(_user_id(w) == order_id for w in self._sim.working_orders):
            raise ValueError(f"order id {order_id} is already working")
        iid = intent.instrument_id
        native_id = f"{self._seq}:{order_id}"
        # Remembered before the call: a failing cost function raises out of ``submit`` after the
        # order is queued, and its later events still need the intent. Stale entries are dropped
        # at the next drain.
        self._seq += 1
        self._intents[native_id] = intent
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
                "tif": intent.time_in_force.value,
                "ts": ts,
            }
        )

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
        if isinstance(event, QuoteTick):
            self.on_quote(event)
        elif isinstance(event, TradeTick):
            self.on_trade(event)

    def on_quote(self, q: QuoteTick) -> None:
        iid = q.instrument_id
        self._sim.on_quote(
            {
                "symbol": iid.symbol,
                "exchange": iid.exchange,
                "ts": q.ts,
                "bid": q.bid_price,
                "ask": q.ask_price,
                "bid_size": q.bid_size,
                "ask_size": q.ask_size,
            }
        )

    def on_trade(self, t: TradeTick) -> None:
        iid = t.instrument_id
        aggressor = {
            AggressorSide.BUYER: "buyer",
            AggressorSide.SELLER: "seller",
            AggressorSide.NO_AGGRESSOR: "none",
        }[t.aggressor_side]
        self._sim.on_trade(
            {
                "symbol": iid.symbol,
                "exchange": iid.exchange,
                "ts": t.ts,
                "price": t.price,
                "size": t.size,
                "aggressor": aggressor,
            }
        )

    def on_session_close(self, ts: int) -> None:
        self._sim.on_session_close(ts)

    def advance_to(self, ts: int) -> None:
        self._sim.advance_to(ts)

    def set_lot_size(self, instrument_id: InstrumentId, lot_size: float) -> None:
        if not (math.isfinite(lot_size) and lot_size > 0):
            raise ValueError(f"lot_size must be positive, got {lot_size}")
        self._sim.set_lot_size(instrument_id.symbol, lot_size, instrument_id.exchange)


class _NativeL2Positions(MutableMapping[InstrumentId, float]):
    """Live view of the native simulator's positions (assigning seeds, zero removes)."""

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


def resolve_l2_backend(requested: BackendChoice | None = None) -> Backend:
    """The backend an :class:`L2Execution` built now would run on (see ``resolve_backend``)."""
    return _resolve(requested, _native_l2)


class L2Execution(BaseExecutionPort):
    """``ExecutionPort`` matching orders against a quote / trade stream (see module docs).

    Feed it ``QuoteTick`` and ``TradeTick`` events through :meth:`on_event` (other events are
    ignored); submit and cancel as for any port. Runs on the native ``L2Simulator`` when the
    extension is usable and on the pure-Python reference otherwise; ``backend`` (``"auto"`` by
    default, or ``HONBA_SIM_BACKEND``) forces one and :attr:`backend` reports the choice.
    """

    def __init__(
        self,
        *,
        cash: Money,
        costs: FillCostFn = zero_costs,
        long_only: bool = True,
        lot_sizes: Mapping[InstrumentId, float] | None = None,
        backend: BackendChoice | None = None,
    ) -> None:
        self._backend: Backend = resolve_l2_backend(backend)
        self._impl: _PythonL2 | _NativeL2
        if self._backend == "native":
            self._impl = _NativeL2(cash=cash, costs=costs, long_only=long_only, lot_sizes=lot_sizes)
        else:
            self._impl = _PythonL2(cash=cash, costs=costs, long_only=long_only, lot_sizes=lot_sizes)

    @property
    def backend(self) -> Backend:
        """``"native"`` or ``"python"``: the implementation this port runs on."""
        return self._backend

    # -- state ---------------------------------------------------------------------------
    @property
    def cash(self) -> Money:
        """Booked cash (a fill moves it at once)."""
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
        """Net quantity per instrument; assigning an entry seeds a holding (zero removes it)."""
        return self._impl.positions

    @property
    def working_orders(self) -> list[str]:
        """Ids of orders not yet filled, rejected, cancelled or expired, in submission order."""
        return self._impl.working_orders

    def book(self, instrument_id: InstrumentId) -> TopOfBook | None:
        """The top of book of ``instrument_id`` once quoted, else ``None``."""
        return self._impl.book(instrument_id)

    # -- ExecutionPort -------------------------------------------------------------------
    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        """Accept an order at event time ``ts`` (a trailing stop is rejected at once)."""
        self._impl.submit(order_id, intent, ts)

    def cancel(self, order_id: str, now: int) -> None:
        """Cancel a working order; ``CancelRequested`` and ``Cancelled`` are stamped ``now``."""
        self._impl.cancel(order_id, now)

    def drain_events(self) -> list[ExecutionEvent]:
        """Return and clear the ordered execution events (a fill precedes its remainder's reject)."""
        return self._impl.drain_events()

    # -- market driver ---------------------------------------------------------------------
    def on_event(self, event: Any, ts_init: int) -> None:
        """Observe the runner's event stream: quotes and trades drive matching."""
        self._impl.on_event(event, ts_init)

    def on_quote(self, quote: QuoteTick) -> None:
        """Update the book and match working orders."""
        self._impl.on_quote(quote)

    def on_trade(self, trade: TradeTick) -> None:
        """Fill resting limits the print trades through and trigger the stops it crosses."""
        self._impl.on_trade(trade)

    def advance_to(self, ts: int) -> None:
        """Move event time to ``ts`` with no market event; due Day orders expire."""
        self._impl.advance_to(ts)

    def on_session_close(self, ts: int) -> None:
        """Explicit session close: every working Day order expires, stamped ``ts``."""
        self._impl.on_session_close(ts)

    def set_lot_size(self, instrument_id: InstrumentId, lot_size: float) -> None:
        """Quantity step a funding cut floors to for ``instrument_id`` (default 1)."""
        self._impl.set_lot_size(instrument_id, lot_size)
