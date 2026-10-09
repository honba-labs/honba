"""Canonical value types on the adapter boundary (E1-S1).

An adapter translates a broker's wire format into these types and nothing else; broker
payloads never travel past the adapter (see ``honba.adapters.boundary``). They are pure
values: frozen, validated at construction, no I/O, no framework, no wall clock. Timestamps
are unix nanoseconds, matching ``honba.domain``.

Prices and quantities are ``float`` here, consistent with ``honba.domain``. The
money-representation decision (integer minor units vs fixed point, E0-S6) will settle this; until
then these types follow the existing domain convention rather than diverging from it.

Deliberately *not* in this module: instrument master snapshots with an as-of date (E1-S3),
per-broker product codes such as CNC/NRML/MIS (E1-S4), named statutory charges (E3-S3) and
margin requirements (E2-S2). ``Product`` here is the market-neutral vocabulary an adapter
maps its broker codes onto.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from itertools import pairwise
from typing import TypeAlias

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderSide, OrderStatus, OrderType, TimeInForce
from honba.domain.tick import QuoteTick, TradeTick

__all__ = [
    "DepthLevel",
    "Funds",
    "Holding",
    "MarginReport",
    "MarketDepth",
    "OrderReport",
    "Product",
    "RunMode",
    "SessionInfo",
    "StreamCallback",
    "StreamEvent",
    "StreamMode",
    "Subscription",
]


class RunMode(Enum):
    """How a run executes orders: a property of the run and its adapter, never a global flag.

    ``BACKTEST`` is driven by the simulator, ``PAPER`` by a sandbox adapter over live market
    data (E3-S7), ``LIVE`` by a broker. A strategy never sees the difference.
    """

    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class Product(Enum):
    """Market-neutral product vocabulary an adapter maps its broker codes onto.

    Indian brokers express these as CNC (delivery), NRML (overnight carry) and MIS
    (intraday); the per-adapter translation tables are E1-S4. Keeping the core vocabulary
    generic is what lets a non-India adapter implement the same contract.
    """

    INTRADAY = "intraday"
    DELIVERY = "delivery"
    CARRY = "carry"


class StreamMode(Enum):
    """Normalised market-data feed modes, so callers never pass a broker-specific flag.

    ``LTP`` is last traded price only, ``QUOTE`` is top of book (``QuoteTick``), ``DEPTH`` is
    a multi-level book (``MarketDepth``). E1-S6 adds reconnect and data-gap markers on top.
    """

    LTP = "ltp"
    QUOTE = "quote"
    DEPTH = "depth"


#: What a streaming callback receives: a top-of-book quote, a printed trade, or a bar.
StreamEvent: TypeAlias = QuoteTick | TradeTick | Bar
StreamCallback: TypeAlias = Callable[[StreamEvent], None]


def _finite(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value}")


def _non_negative(name: str, value: float) -> None:
    _finite(name, value)
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value}")


def _non_empty(name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank, got {value!r}")


def _as_tuple(name: str, value: object) -> tuple[object, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple, got {type(value).__name__}")
    return value


@dataclass(frozen=True, slots=True)
class SessionInfo:
    """Who the adapter is authenticated as, for how long, and in which mode.

    ``expires_at`` is unix nanoseconds, or ``None`` when the broker reports no expiry
    (some brokers use tokens that only die on use). Refresh policy is E1-S5; this type only
    reports what the adapter knows.
    """

    user_id: str
    mode: RunMode
    expires_at: int | None = None
    accounts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _non_empty("user_id", self.user_id)
        if self.expires_at is not None and self.expires_at < 0:
            raise ValueError(f"expires_at must be >= 0, got {self.expires_at}")
        _as_tuple("accounts", self.accounts)
        for account in self.accounts:
            _non_empty("accounts", account)


@dataclass(frozen=True, slots=True)
class DepthLevel:
    """One price level of a book: ``price > 0``, ``quantity >= 0``."""

    price: float
    quantity: float

    def __post_init__(self) -> None:
        _finite("price", self.price)
        if self.price <= 0:
            raise ValueError(f"price must be > 0, got {self.price}")
        _non_negative("quantity", self.quantity)


@dataclass(frozen=True, slots=True)
class MarketDepth:
    """A multi-level book snapshot.

    ``bids`` descend by price and ``asks`` ascend; either side may be empty, but a book with
    both sides must not be crossed. Levels are tuples so an adapter converts its parsed
    lists once, at the boundary, instead of handing mutable state downstream.
    """

    instrument_id: InstrumentId
    ts: int
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]

    def __post_init__(self) -> None:
        if self.ts < 0:
            raise ValueError(f"ts must be >= 0, got {self.ts}")
        _as_tuple("bids", self.bids)
        _as_tuple("asks", self.asks)
        for side in ("bids", "asks"):
            levels: tuple[DepthLevel, ...] = getattr(self, side)
            prices = [level.price for level in levels]
            if side == "bids" and any(lower <= higher for lower, higher in pairwise(prices)):
                raise ValueError(f"bids must be in descending price order, got {prices}")
            if side == "asks" and any(higher <= lower for lower, higher in pairwise(prices)):
                raise ValueError(f"asks must be in ascending price order, got {prices}")
        best_bid, best_ask = self.best_bid, self.best_ask
        if best_bid is not None and best_ask is not None and best_bid.price > best_ask.price:
            raise ValueError(
                f"crossed book: best bid {best_bid.price} above best ask {best_ask.price}"
            )

    @property
    def best_bid(self) -> DepthLevel | None:
        """Highest bid, or ``None`` when the bid side is empty."""
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> DepthLevel | None:
        """Lowest ask, or ``None`` when the ask side is empty."""
        return self.asks[0] if self.asks else None

    @property
    def mid_price(self) -> float | None:
        """Mid of the top of book, or ``None`` when a side is empty."""
        best_bid, best_ask = self.best_bid, self.best_ask
        if best_bid is None or best_ask is None:
            return None
        return (best_bid.price + best_ask.price) / 2.0


@dataclass(frozen=True, slots=True)
class Funds:
    """Account cash and margin as the broker reports it. Every amount is finite and >= 0.

    Amounts are in settlement currency, kept separate rather than netted, so the engine can
    show what is available versus what is tied up. Margin requirements computed by the risk
    stage (E2-S2) are a different object from this broker-reported snapshot.
    """

    available_cash: float
    opening_balance: float
    currency: str = "INR"
    margin_used: float = 0.0
    collateral: float = 0.0
    payin: float = 0.0
    payout: float = 0.0

    def __post_init__(self) -> None:
        _non_empty("currency", self.currency)
        for name in (
            "available_cash",
            "opening_balance",
            "margin_used",
            "collateral",
            "payin",
            "payout",
        ):
            _non_negative(name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class Holding:
    """A demat holding. ``quantity == 0`` is a real state (shares sold, not yet delivered)."""

    instrument_id: InstrumentId
    quantity: float
    average_price: float = 0.0
    last_price: float = 0.0

    def __post_init__(self) -> None:
        _finite("quantity", self.quantity)
        _non_negative("average_price", self.average_price)
        _non_negative("last_price", self.last_price)

    @property
    def is_zero(self) -> bool:
        """Whether the demat balance is flat (shares sold, delivery pending)."""
        return self.quantity == 0.0


@dataclass(frozen=True, slots=True)
class MarginReport:
    """Margin the broker reports as required, distinct from margin it reports as used.

    ``instrument_id is None`` is the account-level report. This is the broker's own view;
    the risk stage computes what *should* be required from the active ``MarketProfile``
    (E2-S2) and the two are compared during reconciliation (E2-S7).
    """

    initial: float
    maintenance: float
    currency: str = "INR"
    instrument_id: InstrumentId | None = None

    def __post_init__(self) -> None:
        _non_empty("currency", self.currency)
        _non_negative("initial", self.initial)
        _non_negative("maintenance", self.maintenance)
        if self.maintenance > self.initial:
            raise ValueError(
                f"maintenance must not exceed initial, got {self.maintenance} > {self.initial}"
            )

    @property
    def headroom(self) -> float:
        """How far the position can be extended before maintenance is breached."""
        return self.initial - self.maintenance


@dataclass(frozen=True, slots=True)
class OrderReport:
    """An order's state as the broker reports it, at one instant.

    This is the snapshot counterpart of the order-state machine (E2-S6), not a replacement
    for it: every field here is one of that machine's states. A broker refusal is a report
    with ``status=REJECTED`` and a ``reject_reason``, never an exception.
    """

    order_id: str
    instrument_id: InstrumentId
    side: OrderSide
    quantity: float
    status: OrderStatus
    product: Product
    order_type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.DAY
    filled_quantity: float = 0.0
    average_price: float = 0.0
    price: float | None = None
    trigger_price: float | None = None
    reject_reason: str | None = None
    ts_event: int = 0
    venue_order_id: str | None = None

    def __post_init__(self) -> None:
        _non_empty("order_id", self.order_id)
        _finite("quantity", self.quantity)
        if self.quantity <= 0:
            raise ValueError(f"quantity must be > 0, got {self.quantity}")
        _finite("filled_quantity", self.filled_quantity)
        if not 0.0 <= self.filled_quantity <= self.quantity:
            raise ValueError(
                f"filled_quantity must be within [0, {self.quantity}], got {self.filled_quantity}"
            )
        _non_negative("average_price", self.average_price)
        if self.filled_quantity > 0 and self.average_price <= 0:
            raise ValueError(
                f"average_price must be > 0 when filled_quantity > 0, got {self.average_price}"
            )
        if self.status is OrderStatus.FILLED and self.filled_quantity != self.quantity:
            raise ValueError(
                "filled order must have filled_quantity == quantity, "
                f"got {self.filled_quantity} of {self.quantity}"
            )
        if self.status is OrderStatus.REJECTED:
            if self.reject_reason is None or not self.reject_reason.strip():
                raise ValueError("a rejected order must carry a reject_reason")
        elif self.reject_reason is not None:
            raise ValueError(
                f"reject_reason is only valid with status=REJECTED, got {self.status.value}"
            )
        for name in ("price", "trigger_price"):
            value = getattr(self, name)
            if value is not None:
                _finite(name, value)
        if self.ts_event < 0:
            raise ValueError(f"ts_event must be >= 0, got {self.ts_event}")


@dataclass(frozen=True, slots=True)
class Subscription:
    """A live feed registration, returned by ``MarketDataAdapter.subscribe``.

    ``id`` is the adapter's handle for ``unsubscribe`` and for reconnect bookkeeping.
    """

    id: str
    instruments: tuple[InstrumentId, ...]
    mode: StreamMode

    def __post_init__(self) -> None:
        _non_empty("id", self.id)
        instruments = _as_tuple("instruments", self.instruments)
        if not instruments:
            raise ValueError("instruments must not be empty")
        if len(set(instruments)) != len(instruments):
            raise ValueError("instruments must be unique")
