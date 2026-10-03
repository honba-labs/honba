"""``FakeAdapter``: an in-memory reference implementation of the adapter contract (E1-S1).

It exists so the contract suite has something to certify and so an adapter author can see the
contract working end to end before touching a broker. It is a **test double, not a paper
trading adapter**: fills are instant at the top of book with no slippage, latency, partial
fills, rejects from the market, or margin. Credible paper trading is the sandbox adapter over
the real simulator (E3-S7), which reuses this contract, not this code.

Everything here is deterministic: no network, no wall clock, no randomness. The clock is a
counter starting at a fixed epoch, so two runs of the same script produce identical reports.

The fake declares a *subset* of the capability set on purpose, so the suite can prove that
refusing an unsupported capability with :class:`CapabilityError` is itself part of the
contract. Ask for anything it does not declare and it refuses before doing anything.
"""

from __future__ import annotations

import datetime as dt

from honba.adapters.base import Adapter
from honba.adapters.capabilities import AdapterCapabilities, Capability
from honba.adapters.errors import AdapterError
from honba.adapters.models import (
    Funds,
    Holding,
    MarginReport,
    MarketDepth,
    OrderReport,
    Product,
    RunMode,
    SessionInfo,
    StreamCallback,
    StreamMode,
    Subscription,
)
from honba.domain.bar import Bar
from honba.domain.instrument import Instrument, InstrumentId, InstrumentKind
from honba.domain.order import OrderIntent, OrderSide, OrderStatus, OrderType
from honba.domain.position import Position, PositionSide
from honba.domain.tick import QuoteTick
from honba.domain.trade import Trade
from honba.wire.wire import PriceType

__all__ = ["FakeAdapter"]

_EPOCH_NS = 1_749_000_000_000_000_000  # 2025-06-02T09:15:00Z, fixed: no wall clock
_TICK_NS = 1_000_000  # 1ms per event
_BAR_START = dt.datetime(2025, 6, 2, 9, 15, tzinfo=dt.timezone.utc)
_BAR_STEP = dt.timedelta(minutes=1)
#: Deterministic (bid, ask) pairs per instrument, used for quotes and for history. A market
#: order fills a buy at the ask and a sell at the bid, so both sides are stated explicitly
#: rather than being derived from a single series.
_SERIES: dict[InstrumentId, tuple[tuple[float, float], ...]] = {
    InstrumentId("RELIANCE", "NSE"): (
        (2450.45, 2450.55),
        (2451.75, 2452.10),
        (2452.60, 2452.85),
        (2453.40, 2453.95),
    ),
    InstrumentId("TCS", "NSE"): (
        (4120.10, 4120.30),
        (4118.90, 4120.00),
        (4122.40, 4123.00),
        (4125.10, 4126.20),
    ),
}
_INSTRUMENTS: tuple[Instrument, ...] = (
    Instrument(
        InstrumentId("RELIANCE", "NSE"), InstrumentKind.EQUITY, lot_size=1.0, tick_size=0.05
    ),
    Instrument(InstrumentId("TCS", "NSE"), InstrumentKind.EQUITY, lot_size=1.0, tick_size=0.05),
)
_SUPPORTED: frozenset[Capability] = frozenset(
    {
        Capability.PLACE_ORDER,
        Capability.CANCEL_ORDER,
        Capability.ORDER_BOOK,
        Capability.TRADE_BOOK,
        Capability.POSITIONS,
        Capability.FUNDS,
        Capability.QUOTES,
        Capability.HISTORICAL_BARS,
        Capability.INSTRUMENT_MASTER,
    }
)


class FakeAdapter(Adapter):
    """An adapter that trades a fixed price series against an in-memory book.

    Market orders fill at the touch; limit orders fill when they cross the touch and rest as
    ``ACCEPTED`` otherwise. Orders larger than available cash are rejected with a reason,
    because a broker refusal is a report, not an exception.
    """

    def __init__(
        self,
        name: str = "fake",
        *,
        mode: RunMode = RunMode.LIVE,
        user_id: str = "FAKE_USER",
        opening_balance: float = 1_000_000.0,
        bars_per_request: int = 5,
    ) -> None:
        self.name = name
        self._mode = mode
        self._user_id = user_id
        self._opening_balance = opening_balance
        self._cash = opening_balance
        self._bars_per_request = bars_per_request
        self._instruments = {i.instrument_id: i for i in _INSTRUMENTS}
        self._cursors = {iid: 0 for iid in _SERIES}
        self._connected = False
        self._ts = _EPOCH_NS
        self._seq = 0
        self._orders: dict[str, OrderReport] = {}
        self._trades: list[Trade] = []
        self._positions: dict[InstrumentId, Position] = {}
        self._subscriptions: dict[
            str, tuple[StreamMode, StreamCallback, tuple[InstrumentId, ...]]
        ] = {}

    # -- facade ----------------------------------------------------------------

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            name=self.name,
            venues=frozenset({"NSE"}),
            products=frozenset({Product.DELIVERY, Product.INTRADAY}),
            order_types=frozenset({OrderType.MARKET, OrderType.LIMIT}),
            stream_modes=frozenset({StreamMode.LTP, StreamMode.QUOTE}),
            price_types=frozenset({PriceType.LAST, PriceType.BID, PriceType.ASK}),
            features=_SUPPORTED,
        )

    async def connect(self) -> SessionInfo:
        if self._connected:
            raise AdapterError(f"adapter {self.name} is already connected")
        self._connected = True
        return SessionInfo(user_id=self._user_id, mode=self._mode)

    async def disconnect(self) -> None:
        if not self._connected:
            raise AdapterError(f"adapter {self.name} is not connected")
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    async def session(self) -> SessionInfo:
        self.require_connected()
        return SessionInfo(user_id=self._user_id, mode=self._mode)

    # -- market data -----------------------------------------------------------

    async def instruments(self) -> list[Instrument]:
        self.require_connected()
        return list(_INSTRUMENTS)

    async def search_instruments(self, query: str) -> list[Instrument]:
        self.require_connected()
        needle = query.strip().upper()
        return [i for i in _INSTRUMENTS if needle in i.instrument_id.symbol]

    async def quote(self, instrument_id: InstrumentId) -> QuoteTick:
        self.require_connected()
        return self._quote_at(instrument_id, self._cursor(instrument_id))

    async def depth(self, instrument_id: InstrumentId, levels: int = 5) -> MarketDepth:
        self._refuse("depth", Capability.DEPTH)
        raise AssertionError("unreachable")  # pragma: no cover

    async def historical_bars(
        self,
        instrument_id: InstrumentId,
        *,
        timeframe: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> list[Bar]:
        self.require_connected()
        if timeframe not in {"1m", "5m", "1d"}:
            raise AdapterError(f"fake adapter does not serve timeframe {timeframe!r}")
        self._require_instrument(instrument_id)
        series = self._series(instrument_id)
        bars: list[Bar] = []
        for offset in range(self._bars_per_request):
            bar_ts = _BAR_START + _BAR_STEP * offset
            if not start <= bar_ts < end:
                continue
            bid, ask = series[offset % len(series)]
            close = (bid + ask) / 2.0
            bars.append(
                Bar(
                    instrument_id=instrument_id,
                    ts=int(bar_ts.timestamp() * 1e9),
                    open=close,
                    high=close + 1.0,
                    low=close - 1.0,
                    close=close,
                    volume=1000.0 + offset,
                )
            )
        return bars

    async def subscribe(
        self,
        instruments: tuple[InstrumentId, ...],
        *,
        mode: StreamMode,
        callback: StreamCallback,
    ) -> Subscription:
        self.require_connected()
        self.capabilities().require_stream_mode(mode)
        for instrument_id in instruments:
            self._require_instrument(instrument_id)
        self._seq += 1
        subscription_id = f"sub-{self._seq}"
        self._subscriptions[subscription_id] = (mode, callback, instruments)
        return Subscription(id=subscription_id, instruments=instruments, mode=mode)

    async def unsubscribe(self, subscription_id: str) -> None:
        self.require_connected()
        if self._subscriptions.pop(subscription_id, None) is None:
            raise AdapterError(f"unknown subscription {subscription_id!r}")

    # -- execution -------------------------------------------------------------

    async def place_order(
        self,
        intent: OrderIntent,
        *,
        product: Product,
        client_order_id: str | None = None,
    ) -> OrderReport:
        self.require_connected()
        self.capabilities().require(Capability.PLACE_ORDER)
        self.capabilities().require_product(product)
        self.capabilities().require_order_type(intent.order_type)
        self._require_instrument(intent.instrument_id)
        if client_order_id is not None and client_order_id in self._orders:
            return self._orders[client_order_id]  # idempotent by client order id

        self._seq += 1
        order_id = client_order_id or f"{self.name}-{self._seq}"
        quote = await self.quote(intent.instrument_id)
        fill_price = quote.ask_price if intent.side is OrderSide.BUY else quote.bid_price
        marketable = (
            intent.order_type is OrderType.MARKET
            or (
                intent.side is OrderSide.BUY
                and intent.price is not None
                and intent.price >= quote.ask_price
            )
            or (
                intent.side is OrderSide.SELL
                and intent.price is not None
                and intent.price <= quote.bid_price
            )
        )
        notional = intent.quantity * fill_price
        if notional > self._cash and intent.side is OrderSide.BUY:
            report = OrderReport(
                order_id=order_id,
                instrument_id=intent.instrument_id,
                side=intent.side,
                quantity=intent.quantity,
                status=OrderStatus.REJECTED,
                product=product,
                order_type=intent.order_type,
                time_in_force=intent.time_in_force,
                price=intent.price,
                trigger_price=intent.trigger_price,
                reject_reason="insufficient funds",
                ts_event=self._tick(),
            )
            self._orders[order_id] = report
            return report
        if not marketable:
            report = OrderReport(
                order_id=order_id,
                instrument_id=intent.instrument_id,
                side=intent.side,
                quantity=intent.quantity,
                status=OrderStatus.ACCEPTED,
                product=product,
                order_type=intent.order_type,
                time_in_force=intent.time_in_force,
                price=intent.price,
                trigger_price=intent.trigger_price,
                ts_event=self._tick(),
            )
            self._orders[order_id] = report
            return report

        filled = OrderReport(
            order_id=order_id,
            instrument_id=intent.instrument_id,
            side=intent.side,
            quantity=intent.quantity,
            status=OrderStatus.FILLED,
            product=product,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            filled_quantity=intent.quantity,
            average_price=fill_price,
            price=intent.price,
            trigger_price=intent.trigger_price,
            ts_event=self._tick(),
        )
        self._orders[order_id] = filled
        self._book_fill(filled)
        return filled

    async def modify_order(
        self,
        order_id: str,
        *,
        quantity: float | None = None,
        price: float | None = None,
        trigger_price: float | None = None,
    ) -> None:
        self._refuse("modify_order", Capability.MODIFY_ORDER)
        raise AssertionError("unreachable")  # pragma: no cover

    async def cancel_order(self, order_id: str) -> None:
        self.require_connected()
        self.capabilities().require(Capability.CANCEL_ORDER)
        report = self._orders.get(order_id)
        if report is None:
            raise AdapterError(f"unknown order {order_id!r}")
        if report.status in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED):
            return  # cancelling a terminal order is a no-op, not an error
        self._orders[order_id] = OrderReport(
            order_id=report.order_id,
            instrument_id=report.instrument_id,
            side=report.side,
            quantity=report.quantity,
            status=OrderStatus.CANCELLED,
            product=report.product,
            order_type=report.order_type,
            time_in_force=report.time_in_force,
            filled_quantity=report.filled_quantity,
            average_price=report.average_price,
            price=report.price,
            trigger_price=report.trigger_price,
            ts_event=self._tick(),
        )

    async def cancel_all(
        self,
        *,
        instrument_id: InstrumentId | None = None,
        product: Product | None = None,
    ) -> None:
        self._refuse("cancel_all", Capability.CANCEL_ALL)
        raise AssertionError("unreachable")  # pragma: no cover

    async def order_status(self, order_id: str) -> OrderReport:
        self.require_connected()
        self.capabilities().require(Capability.ORDER_BOOK)
        report = self._orders.get(order_id)
        if report is None:
            raise AdapterError(f"unknown order {order_id!r}")
        return report

    async def orders(self) -> list[OrderReport]:
        self.require_connected()
        self.capabilities().require(Capability.ORDER_BOOK)
        return list(self._orders.values())

    async def trades(self) -> list[Trade]:
        self.require_connected()
        self.capabilities().require(Capability.TRADE_BOOK)
        return list(self._trades)

    async def positions(self) -> list[Position]:
        self.require_connected()
        self.capabilities().require(Capability.POSITIONS)
        return [p for p in self._positions.values() if not p.is_flat]

    async def holdings(self) -> list[Holding]:
        self._refuse("holdings", Capability.HOLDINGS)
        raise AssertionError("unreachable")  # pragma: no cover

    async def funds(self) -> Funds:
        self.require_connected()
        self.capabilities().require(Capability.FUNDS)
        return Funds(
            available_cash=self._cash,
            opening_balance=self._opening_balance,
            margin_used=0.0,
        )

    async def margin(self, instrument_id: InstrumentId | None = None) -> MarginReport:
        self._refuse("margin", Capability.MARGIN)
        raise AssertionError("unreachable")  # pragma: no cover

    # -- test affordances (not part of the contract) ---------------------------

    def deposit(self, amount: float) -> None:
        """Add cash, so a test can place an order larger than the opening balance."""
        self._cash += amount

    def advance_prices(self, instrument_id: InstrumentId) -> None:
        """Move to the next (bid, ask) pair and push it to every matching subscriber."""
        series = self._series(instrument_id)
        self._cursors[instrument_id] = (self._cursors[instrument_id] + 1) % len(series)
        self._emit(instrument_id)

    # -- internals -------------------------------------------------------------

    def _refuse(self, method: str, capability: Capability) -> None:
        """Refuse an undeclared capability, after the usual connection check."""
        self.require_connected()
        self.capabilities().require(capability)
        raise AssertionError("unreachable")  # pragma: no cover

    def _series(self, instrument_id: InstrumentId) -> tuple[tuple[float, float], ...]:
        try:
            return _SERIES[instrument_id]
        except KeyError:
            raise AdapterError(f"unknown instrument {instrument_id}") from None

    def _require_instrument(self, instrument_id: InstrumentId) -> None:
        if instrument_id not in self._instruments:
            raise AdapterError(f"unknown instrument {instrument_id}")

    def _cursor(self, instrument_id: InstrumentId) -> int:
        self._require_instrument(instrument_id)
        return self._cursors[instrument_id]

    def _quote_at(self, instrument_id: InstrumentId, cursor: int) -> QuoteTick:
        series = self._series(instrument_id)
        bid, ask = series[cursor % len(series)]
        return QuoteTick(
            instrument_id=instrument_id,
            ts=self._tick(),
            bid_price=bid,
            ask_price=ask,
            bid_size=100.0,
            ask_size=100.0,
        )

    def _emit(self, instrument_id: InstrumentId) -> None:
        quote = self._quote_at(instrument_id, self._cursors[instrument_id])
        for mode, callback, instruments in self._subscriptions.values():
            if instrument_id in instruments and mode in (StreamMode.QUOTE, StreamMode.LTP):
                callback(quote)

    def _book_fill(self, report: OrderReport) -> None:
        position = self._positions.setdefault(
            report.instrument_id, Position(instrument_id=report.instrument_id)
        )
        side = PositionSide.LONG if report.side is OrderSide.BUY else PositionSide.SHORT
        position.apply_fill(side, report.filled_quantity, report.average_price)
        notional = report.filled_quantity * report.average_price
        self._cash += notional if report.side is OrderSide.SELL else -notional
        self._trades.append(
            Trade(
                instrument_id=report.instrument_id,
                side=report.side,
                quantity=report.filled_quantity,
                price=report.average_price,
                ts=report.ts_event,
                order_id=report.order_id,
            )
        )

    def _tick(self) -> int:
        now, self._ts = self._ts, self._ts + _TICK_NS
        return now
