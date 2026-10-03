"""The adapter contract (E1-S1).

One facade plus two roles. The facade is identity, capability and lifecycle; the roles are
what a caller actually depends on, so a market-data consumer never has to implement order
placement and a paper adapter never has to fake a REST client it does not use. All three are
pure declarations: no implementation, no I/O, no broker knowledge.

    Adapter             capabilities(), connect(), disconnect(), session(), is_connected()
      |-- MarketDataAdapter   quotes, depth, history, instrument master, streaming
      |-- ExecutionAdapter    orders, books, positions, holdings, funds, margin

Every I/O method is ``async``: broker I/O is concurrent and a stream must not block the
caller. The facade's identity and guard members (``capabilities()``, ``is_connected()``,
``require_connected()``, ``require_capabilities()``) are sync. Backtest adapters implement
the same I/O methods over a simulator, which is why a strategy cannot tell the modes apart.

Rules every implementation must follow (the shared suite in
:mod:`honba.adapters.contract` enforces them):

* call ``require_connected()`` before any I/O, and refuse an unsupported capability with
  :class:`~honba.adapters.errors.CapabilityError` *before* making a request;
* a broker refusal is an :class:`~honba.adapters.models.OrderReport` with
  ``status=REJECTED``, never an exception;
* an unknown instrument or order id raises :class:`~honba.adapters.errors.AdapterError`,
  never ``KeyError`` or ``LookupError``;
* broker wire types never appear in a return value; only the types in
  :mod:`honba.adapters.models` and :mod:`honba.domain`.
"""

from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from honba.adapters.capabilities import AdapterCapabilities, Capability
from honba.adapters.errors import AdapterError
from honba.adapters.models import (
    Funds,
    Holding,
    MarginReport,
    MarketDepth,
    OrderReport,
    Product,
    SessionInfo,
    StreamCallback,
    StreamMode,
    Subscription,
)
from honba.domain.bar import Bar
from honba.domain.instrument import Instrument, InstrumentId
from honba.domain.order import OrderIntent
from honba.domain.position import Position
from honba.domain.tick import QuoteTick
from honba.domain.trade import Trade

__all__ = ["Adapter", "ExecutionAdapter", "MarketDataAdapter"]


class Adapter(ABC):
    """Identity, capabilities and connection lifecycle, shared by both roles.

    Subclasses declare ``name`` so a descriptor and the registry agree without a lookup, and
    implement the five abstract members. ``require_connected`` and ``require_capabilities``
    are concrete helpers every implementation should call first.
    """

    #: Registry key for this adapter, e.g. ``"dhan"``. Must match ``capabilities().name``.
    name: str = "adapter"

    @abstractmethod
    def capabilities(self) -> AdapterCapabilities:
        """What this adapter supports, as data. Cheap, side-effect free, callable offline."""
        ...

    @abstractmethod
    async def connect(self) -> SessionInfo:
        """Authenticate and open whatever transport the adapter needs.

        Returns the resulting session: who we are, in which mode, until when. Raises
        :class:`~honba.adapters.errors.SessionError` when recoverable (retry or refresh will
        help) and :class:`~honba.adapters.errors.AdapterFatalError` when not.
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close transports and release resources. Must be safe to call after a failure."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the adapter is ready for I/O right now."""
        ...

    @abstractmethod
    async def session(self) -> SessionInfo:
        """The current session, re-reading it from the transport when the adapter has one.

        Never returns a stale token: an adapter that caches must refresh on expiry.
        """
        ...

    def require_connected(self) -> None:
        """Raise :class:`AdapterError` unless the adapter is connected.

        The one guard every I/O method starts with, so calling an adapter out of order fails
        as a typed error instead of a ``None`` dereference.
        """
        if not self.is_connected():
            raise AdapterError(f"adapter {self.name} is not connected")

    def require_capabilities(self, *capabilities: Capability) -> None:
        """Raise :class:`CapabilityError` unless every capability is supported."""
        self.capabilities().require(*capabilities)


@runtime_checkable
class MarketDataAdapter(Protocol):
    """Quotes, depth, history, the instrument master, and live subscriptions."""

    async def instruments(self) -> list[Instrument]:
        """The full instrument list the broker publishes, with lot and tick metadata.

        Raises :class:`~honba.adapters.errors.AdapterError` on a transport failure. Dated
        snapshots and ``as_of`` resolution are the instrument master's job (E1-S3), built on
        top of this fetch.
        """
        ...

    async def search_instruments(self, query: str) -> list[Instrument]:
        """Instruments matching a symbol or company-name fragment; empty when none match."""
        ...

    async def quote(self, instrument_id: InstrumentId) -> QuoteTick:
        """One top-of-book snapshot. Unknown instrument: ``AdapterError``, not ``KeyError``."""
        ...

    async def depth(self, instrument_id: InstrumentId, levels: int = 5) -> MarketDepth:
        """Up to ``levels`` levels per side. A broker with shallower data returns less.

        Raises :class:`~honba.adapters.errors.CapabilityError` when the broker publishes no
        book at all; that is a missing capability, not a transport failure.
        """
        ...

    async def historical_bars(
        self,
        instrument_id: InstrumentId,
        *,
        timeframe: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> list[Bar]:
        """Bars in ascending time order for ``[start, end)``; empty when there are none.

        ``timeframe`` is the canonical aggregation name (``"1m"``, ``"1d"``), never a
        broker-specific code.
        """
        ...

    async def subscribe(
        self,
        instruments: tuple[InstrumentId, ...],
        *,
        mode: StreamMode,
        callback: StreamCallback,
    ) -> Subscription:
        """Start delivering ``mode`` updates for ``instruments`` to ``callback``.

        The callback runs on the adapter's transport thread; push events onto the engine
        queue rather than acting on them inline.
        """
        ...

    async def unsubscribe(self, subscription_id: str) -> None:
        """Stop a subscription. Unknown ids raise :class:`~honba.adapters.errors.AdapterError`."""
        ...


@runtime_checkable
class ExecutionAdapter(Protocol):
    """Order placement and the account books that report what happened."""

    async def place_order(
        self,
        intent: OrderIntent,
        *,
        product: Product,
        client_order_id: str | None = None,
    ) -> OrderReport:
        """Submit one order and return its first known state.

        ``client_order_id`` is the caller's idempotency key (E2-S11); a repeat with the same
        id must not create a second order. A broker refusal comes back as
        ``status=REJECTED`` with a reason, not an exception. Passing an intent the adapter
        cannot express (unsupported order type, time in force or product) raises
        :class:`~honba.adapters.errors.CapabilityError` before any request.
        """
        ...

    async def modify_order(
        self,
        order_id: str,
        *,
        quantity: float | None = None,
        price: float | None = None,
        trigger_price: float | None = None,
    ) -> None:
        """Change a live order. All-``None`` is a no-op error: raise ``AdapterError``."""
        ...

    async def cancel_order(self, order_id: str) -> None:
        """Request cancellation. Already-terminal orders are reported, not re-cancelled."""
        ...

    async def cancel_all(
        self,
        *,
        instrument_id: InstrumentId | None = None,
        product: Product | None = None,
    ) -> None:
        """Cancel every open order, optionally narrowed to one instrument or product."""
        ...

    async def order_status(self, order_id: str) -> OrderReport:
        """Current state of one order. Unknown id: ``AdapterError``, not ``KeyError``."""
        ...

    async def orders(self) -> list[OrderReport]:
        """The order book for the day, open and closed."""
        ...

    async def trades(self) -> list[Trade]:
        """Today's fills. Cost detail is the single ``Trade.costs`` total until E3-S3 splits
        it into named charges."""
        ...

    async def positions(self) -> list[Position]:
        """Open positions as the broker sees them, for reconciliation (E2-S7)."""
        ...

    async def holdings(self) -> list[Holding]:
        """Demat holdings, including zero-quantity entries pending delivery."""
        ...

    async def funds(self) -> Funds:
        """Cash and margin as the broker reports them."""
        ...

    async def margin(self, instrument_id: InstrumentId | None = None) -> MarginReport:
        """Margin required per the broker; account level when ``instrument_id`` is ``None``."""
        ...
