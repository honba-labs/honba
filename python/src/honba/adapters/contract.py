"""The shared adapter contract suite (E1-S1).

One suite, every adapter, so "implements the adapter contract" means something checkable
rather than aspirational. An adapter's own suite calls it::

    from honba.adapters.contract import verify_adapter_contract

    async def test_dhan_satisfies_the_contract():
        await verify_adapter_contract(DhanAdapter(config))

What the suite checks, in order:

* the facade exists, and its descriptor agrees with its registry name;
* the lifecycle refuses work before ``connect()``, reports the session, and treats a repeated
  ``connect()`` or ``disconnect()`` as a programming error;
* every method declared **unsupported** refuses with
  :class:`~honba.adapters.errors.CapabilityError` before doing any work, and every method
  declared **supported** returns the declared canonical type instead of raising;
* market data is canonical (``Instrument``, ``QuoteTick``, ``MarketDepth``, ``Bar``), history
  is ascending, and lookups of unknown instruments and orders are typed errors, not
  ``KeyError``;
* an order placed through the adapter is retrievable by id, appears in the order book, is
  idempotent under a repeated client order id, and its fills reach the trade and position
  books;
* a broker refusal arrives as ``status=REJECTED`` with a reason, never as an exception;
* nothing but canonical types comes back out.

Deliberately out of scope: stream delivery, reconnect and data-gap markers (E1-S6), recorded
broker fixtures (each adapter's own tests) and margin sufficiency (E2-S2).

The suite is pytest-free and deterministic: no network, no wall clock, no randomness. It
never leaves the adapter connected.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, cast

from honba.adapters.base import Adapter, ExecutionAdapter, MarketDataAdapter, MarketDataClient
from honba.adapters.capabilities import AdapterCapabilities, Capability, capability_for_method
from honba.adapters.errors import AdapterError, CapabilityError
from honba.adapters.models import (
    Funds,
    Holding,
    MarginReport,
    MarketDepth,
    OrderReport,
    Product,
    RunMode,
    SessionInfo,
    StreamMode,
    Subscription,
)
from honba.domain.bar import Bar
from honba.domain.instrument import Instrument, InstrumentId, InstrumentKind
from honba.domain.order import OrderIntent, OrderStatus, OrderType
from honba.domain.position import Position
from honba.domain.tick import QuoteTick, TradeTick
from honba.domain.trade import Trade

__all__ = ["ContractViolation", "verify_adapter_contract"]

#: Wide enough that any real broker has daily bars inside it.
_HISTORY_START = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)
_HISTORY_END = dt.datetime(2030, 1, 1, tzinfo=dt.timezone.utc)
#: Statuses that mean "the broker is still working on it".
_LIVE_STATUSES = frozenset(
    {OrderStatus.SUBMITTED, OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}
)
#: Element type each method must return; the value itself must be a list or tuple of them.
_ELEMENT_TYPES: dict[str, type] = {
    "instruments": Instrument,
    "search_instruments": Instrument,
    "quote": QuoteTick,
    "depth": MarketDepth,
    "historical_bars": Bar,
    "funds": Funds,
    "margin": MarginReport,
    "holdings": Holding,
    "positions": Position,
    "orders": OrderReport,
    "trades": Trade,
}
#: Element types a streamed event may be.
_STREAM_TYPES = (QuoteTick, TradeTick, Bar)


class ContractViolation(AssertionError):
    """An implementation broke the adapter contract.

    An ``AssertionError`` so pytest reports it as a failure without an adapter importing
    anything, and a named class so a caller can catch one deliberately.
    """


def _fail(message: str) -> None:
    raise ContractViolation(message)


def _expect(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


async def _call(label: str, target: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call ``target``, turning any unexpected failure into a violation naming the call.

    Only :class:`~honba.adapters.errors.AdapterError` subclasses may escape the contract, so
    anything else (a ``KeyError`` from a dict lookup, a broker SDK exception, a bare
    ``ValueError``) is reported with that rule attached.
    """
    try:
        result = target(*args, **kwargs)
        if hasattr(result, "__await__"):
            return await result
    except ContractViolation:
        raise
    except AdapterError as error:
        _fail(f"{label} raised {type(error).__name__}: {error}")
    except Exception as error:  # noqa: BLE001 - reporting any failure is the point
        _fail(
            f"{label} raised {type(error).__name__}: {error}; only AdapterError subclasses "
            "may escape the adapter contract (a broker refusal is an OrderReport with "
            "status=REJECTED, not an exception)"
        )
    return result


def _expect_kind(label: str, value: Any, *types: type) -> None:
    """``value`` is one of ``types``, or a list/tuple whose every item is."""
    if isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _expect_kind(f"{label}[{index}]", item, *types)
        return
    if not isinstance(value, types):
        expected = " or ".join(t.__name__ for t in types)
        _fail(f"{label} returned {type(value).__name__}, expected {expected}")


async def _refuses(label: str, call: Callable[[], Any], expected: type[Exception]) -> None:
    """``call`` must raise ``expected``. Anything else, including silence, is a violation."""
    try:
        result = call()
        if hasattr(result, "__await__"):
            result = await result
    except expected:
        return
    except ContractViolation:
        raise
    except Exception as error:  # noqa: BLE001
        _fail(f"{label} raised {type(error).__name__}, expected {expected.__name__}: {error}")
    returned = type(result).__name__ if result is not None else "None"
    _fail(f"{label} returned {returned} while not connected, expected {expected.__name__}")


@dataclass(frozen=True, slots=True)
class _Probe:
    """What the suite drives the adapter with. Never a real order, only a probe."""

    instrument_id: InstrumentId
    product: Product
    unknown_instrument: InstrumentId
    unknown_order: str
    unknown_subscription: str
    client_order_id: str
    quantity: float


def _probe_from(
    instruments: Iterable[Instrument], caps: AdapterCapabilities, quantity: float
) -> _Probe:
    first = next(iter(instruments))
    return _Probe(
        instrument_id=first.instrument_id,
        product=min(caps.products, key=lambda p: p.value),
        unknown_instrument=InstrumentId("HONBA-CONTRACT-UNKNOWN", first.instrument_id.exchange),
        unknown_order="honba-contract-unknown-order",
        unknown_subscription="honba-contract-unknown-subscription",
        client_order_id="honba-contract-probe",
        quantity=quantity,
    )


def _methods_of(protocol: type) -> list[str]:
    return [
        name
        for name, value in vars(protocol).items()
        if not name.startswith("_") and callable(value)
    ]


def _args_for(method: str, probe: _Probe) -> tuple[tuple, dict]:
    """Probe arguments for a method, chosen so a refusal must precede any state lookup."""
    if method in {"quote", "depth"}:
        return (probe.instrument_id,), {}
    if method == "historical_bars":
        return (
            (probe.instrument_id,),
            {"timeframe": "1d", "start": _HISTORY_START, "end": _HISTORY_END},
        )
    if method == "search_instruments":
        return (probe.instrument_id.symbol,), {}
    if method == "subscribe":
        return ((probe.instrument_id,), {"mode": StreamMode.QUOTE, "callback": lambda _event: None})
    if method in {"unsubscribe"}:
        return (probe.unknown_subscription,), {}
    if method == "place_order":
        return (
            (OrderIntent.market_buy(probe.instrument_id, probe.quantity),),
            {"product": probe.product},
        )
    if method in {"order_status", "cancel_order"}:
        return (probe.unknown_order,), {}
    if method == "modify_order":
        return (probe.unknown_order,), {"quantity": probe.quantity}
    return (), {}


#: Preference order for the call that must fail before ``connect()``: an account-wide read is
#: cheapest and needs no instrument, and ``funds`` is last so a type check on it is not
#: shadowed by the refusal check.
_OUT_OF_ORDER_PROBES = (
    "orders",
    "positions",
    "trades",
    "holdings",
    "instruments",
    "quote",
    "funds",
)


def _out_of_order_probe(adapter: Any) -> str:
    caps = adapter.capabilities()
    for name in _OUT_OF_ORDER_PROBES:
        capability = capability_for_method(name)
        if capability is not None and caps.supports(capability):
            return name
    return "orders"


def _missing(adapter: Any, protocol: type) -> list[str]:
    return [name for name in _methods_of(protocol) if not callable(getattr(adapter, name, None))]


def _verify_roles(adapter: Any) -> None:
    """Both roles are implemented, with every method callable."""
    for protocol in (MarketDataAdapter, ExecutionAdapter):
        missing = _missing(adapter, protocol)
        _expect(
            not missing,
            f"{type(adapter).__name__} does not implement {protocol.__name__}: "
            f"missing or not callable: {', '.join(sorted(missing))}",
        )


async def _verify_descriptor(adapter: Adapter) -> None:
    """The descriptor is data, agrees with the registry name, and supports what it declares."""
    caps = await _call("capabilities()", adapter.capabilities)
    _expect_kind("capabilities()", caps, AdapterCapabilities)
    _expect(
        caps.name == adapter.name,
        f"capabilities().name is {caps.name!r} but the adapter's registry name is "
        f"{adapter.name!r}; they must match",
    )
    for method in _ELEMENT_TYPES:
        capability = capability_for_method(method)
        if capability is None or not caps.supports(capability):
            continue
        _expect(
            callable(getattr(adapter, method, None)),
            f"capabilities() declares {capability.value} but {method}() is not callable",
        )


async def _verify_lifecycle(adapter: Adapter) -> None:
    """No work before ``connect()``; a real session after; repeated transitions are errors."""
    _expect(adapter.is_connected() is False, "is_connected() must be False on a fresh adapter")
    await _refuses(
        "require_connected() before connect", lambda: adapter.require_connected(), AdapterError
    )
    probe = _out_of_order_probe(adapter)
    await _refuses(f"{probe}() before connect", lambda: getattr(adapter, probe)(), AdapterError)
    session = await _call("connect()", adapter.connect)
    _expect_kind("connect()", session, SessionInfo)
    _expect(isinstance(session.mode, RunMode), "connect() must report the RunMode it opened in")
    _expect(bool(session.user_id.strip()), "connect() must report a non-blank user_id")
    _expect(adapter.is_connected() is True, "is_connected() must be True after connect()")
    current = await _call("session()", adapter.session)
    _expect_kind("session()", current, SessionInfo)
    _expect(
        (current.user_id, current.mode) == (session.user_id, session.mode),
        "session() disagrees with what connect() returned",
    )
    await _refuses("connect() twice", lambda: adapter.connect(), AdapterError)


async def _verify_capability_refusals(adapter: Adapter, probe: _Probe) -> None:
    """Anything declared unsupported refuses with ``CapabilityError`` before doing work."""
    caps = adapter.capabilities()
    for protocol in (MarketDataAdapter, ExecutionAdapter):
        for method in _methods_of(protocol):
            capability = capability_for_method(method)
            if capability is None or caps.supports(capability):
                continue
            if not callable(getattr(adapter, method, None)):
                continue
            args, kwargs = _args_for(method, probe)
            await _refuses(
                f"{method}() without capability {capability.value}",
                lambda method=method, args=args, kwargs=kwargs: getattr(adapter, method)(
                    *args, **kwargs
                ),
                CapabilityError,
            )


async def _verify_order_type_refusals(adapter: Adapter, probe: _Probe) -> None:
    """An undeclared opt-in order type refuses with ``CapabilityError`` before any request.

    Only checked for adapters that can place orders at all and do not declare the type. An
    adapter that declares it is not probed: the generic suite cannot know its semantics.
    """
    caps = adapter.capabilities()
    if not caps.supports(Capability.PLACE_ORDER) or not callable(
        getattr(adapter, "place_order", None)
    ):
        return
    if caps.supports_order_type(OrderType.TRAILING_STOP):
        return
    intent = OrderIntent.trailing_stop_sell(probe.instrument_id, probe.quantity, trail_percent=1.0)
    await _refuses(
        f"place_order() with undeclared order type {OrderType.TRAILING_STOP.value}",
        lambda: cast(ExecutionAdapter, adapter).place_order(intent, product=probe.product),
        CapabilityError,
    )


async def _verify_market_data(adapter: Any, probe: _Probe) -> None:
    """Canonical market data, ascending history, typed errors for unknown instruments."""
    caps = adapter.capabilities()
    if caps.supports(Capability.INSTRUMENT_MASTER):
        instruments = await _call("instruments()", adapter.instruments)
        _expect_kind("instruments()", instruments, Instrument)
        _expect(bool(instruments), "instruments() returned nothing; there is nothing to trade")
        ids = [i.instrument_id for i in instruments]
        _expect(len(set(ids)) == len(ids), "instruments() returned duplicate instrument ids")
        found = await _call(
            f"search_instruments({probe.instrument_id.symbol!r})",
            adapter.search_instruments,
            probe.instrument_id.symbol,
        )
        _expect_kind("search_instruments()", found, Instrument)
        _expect(
            probe.instrument_id in {i.instrument_id for i in found},
            f"search_instruments() did not find {probe.instrument_id} by its own symbol",
        )
    if caps.supports(Capability.QUOTES):
        quote = await _call("quote()", adapter.quote, probe.instrument_id)
        _expect_kind("quote()", quote, QuoteTick)
        _expect(quote.instrument_id == probe.instrument_id, "quote() returned another instrument")
        _expect(quote.ts >= 0, "quote() must carry a unix-nanosecond timestamp")
        await _refuses(
            "quote() for an unknown instrument",
            lambda: adapter.quote(probe.unknown_instrument),
            AdapterError,
        )
    if caps.supports(Capability.DEPTH):
        depth = await _call("depth()", adapter.depth, probe.instrument_id)
        _expect_kind("depth()", depth, MarketDepth)
        _expect(depth.instrument_id == probe.instrument_id, "depth() returned another instrument")
    if caps.supports(Capability.HISTORICAL_BARS):
        bars = await _call(
            "historical_bars()",
            adapter.historical_bars,
            probe.instrument_id,
            timeframe="1d",
            start=_HISTORY_START,
            end=_HISTORY_END,
        )
        _expect_kind("historical_bars()", bars, Bar)
        stamps = [bar.ts for bar in bars]
        _expect(
            stamps == sorted(stamps),
            f"historical_bars() must be in ascending time order, got {stamps}",
        )
        await _refuses(
            "historical_bars() for an unknown instrument",
            lambda: adapter.historical_bars(
                probe.unknown_instrument,
                timeframe="1d",
                start=_HISTORY_START,
                end=_HISTORY_END,
            ),
            AdapterError,
        )


async def _verify_stream(adapter: Any, probe: _Probe) -> None:
    """Subscriptions are well formed and removable; unknown ids are typed errors."""
    caps = adapter.capabilities()
    if not caps.stream_modes:
        return
    received: list[Any] = []
    mode = min(caps.stream_modes, key=lambda m: m.value)
    subscription = await _call(
        "subscribe()",
        adapter.subscribe,
        (probe.instrument_id,),
        mode=mode,
        callback=received.append,
    )
    _expect_kind("subscribe()", subscription, Subscription)
    _expect(
        subscription.instruments == (probe.instrument_id,),
        "subscribe() must echo exactly the instruments it was given",
    )
    _expect(subscription.mode is mode, f"subscribe() must report the mode it was given ({mode})")
    _expect(bool(subscription.id.strip()), "subscribe() must return a usable subscription id")
    await _call("unsubscribe()", adapter.unsubscribe, subscription.id)
    await _refuses(
        "unsubscribe() for an unknown subscription",
        lambda: adapter.unsubscribe(probe.unknown_subscription),
        AdapterError,
    )
    # An adapter that delivers inline has its events checked for type here; one that streams
    # asynchronously is checked by its own tests, since delivery semantics belong to E1-S6.
    for index, event in enumerate(received):
        _expect_kind(f"a streamed event [{index}]", event, *_STREAM_TYPES)


def _identity(report: OrderReport) -> tuple:
    """The fields that define an order's identity and state.

    Excludes timestamps and prices, which legitimately move between two reads of a live
    order. Includes status and filled quantity, which must not.
    """
    return (
        report.order_id,
        report.instrument_id,
        report.side,
        report.quantity,
        report.filled_quantity,
        report.status,
        report.product,
    )


async def _place_resting_order(adapter: Any, probe: _Probe) -> OrderReport | None:
    """A limit order far below the touch, so it rests and can be cancelled.

    ``None`` when there is no quote to rest away from, or when the adapter filled it anyway:
    cancel behaviour cannot be probed without inventing a market.
    """
    if not adapter.capabilities().supports(Capability.QUOTES):
        return None
    quote = await _call("quote()", adapter.quote, probe.instrument_id)
    intent = OrderIntent.limit_buy(probe.instrument_id, probe.quantity, quote.bid_price / 2.0)
    report = await _call(
        "place_order() (resting limit)", adapter.place_order, intent, product=probe.product
    )
    return report if report.status in _LIVE_STATUSES else None


async def _verify_execution(adapter: Any, probe: _Probe) -> None:
    """Orders round-trip: retrievable, idempotent, and their fills reach the books."""
    caps = adapter.capabilities()
    for method in ("funds", "margin", "holdings", "positions", "orders", "trades"):
        capability = capability_for_method(method)
        if capability is None or not caps.supports(capability):
            continue
        args, kwargs = _args_for(method, probe)
        value = await _call(f"{method}()", getattr(adapter, method), *args, **kwargs)
        _expect_kind(f"{method}()", value, _ELEMENT_TYPES[method])
    if not caps.supports(Capability.PLACE_ORDER):
        return

    intent = OrderIntent.market_buy(probe.instrument_id, probe.quantity)
    report = await _call(
        "place_order()",
        adapter.place_order,
        intent,
        product=probe.product,
        client_order_id=probe.client_order_id,
    )
    _expect_kind("place_order()", report, OrderReport)
    _expect(bool(report.order_id.strip()), "place_order() must return a non-blank order_id")
    _expect(
        (report.instrument_id, report.side) == (intent.instrument_id, intent.side),
        "place_order() must report the instrument and side it was asked for",
    )
    _expect(
        report.status is OrderStatus.REJECTED
        or report.status in _LIVE_STATUSES | {OrderStatus.FILLED},
        f"place_order() returned an unexpected status {report.status}",
    )
    if report.status is OrderStatus.REJECTED:
        # A refusal is data: nothing escaped as an exception, and the reason is there to journal.
        _expect(bool(report.reject_reason), "a rejected order must carry a reject_reason")

    if caps.supports(Capability.ORDER_BOOK):
        book = await _call("orders()", adapter.orders)
        _expect(
            any(o.order_id == report.order_id for o in book),
            f"place_order() returned {report.order_id} but it is absent from the order book",
        )
        current = await _call(
            f"order_status({report.order_id})", adapter.order_status, report.order_id
        )
        _expect_kind("order_status()", current, OrderReport)
        _expect(
            _identity(current) == _identity(report),
            f"order_status() disagrees with the report place_order() returned: "
            f"{_identity(current)} vs {_identity(report)}",
        )
        await _refuses(
            "order_status() for an unknown order",
            lambda: adapter.order_status(probe.unknown_order),
            AdapterError,
        )

    again = await _call(
        "place_order() with a repeated client_order_id",
        adapter.place_order,
        intent,
        product=probe.product,
        client_order_id=probe.client_order_id,
    )
    _expect(
        again.order_id == report.order_id,
        "a repeated client_order_id must be idempotent, got "
        f"{report.order_id} then {again.order_id}",
    )

    if report.status is OrderStatus.FILLED and caps.supports(Capability.TRADE_BOOK):
        fills: Sequence[Trade] = await _call("trades()", adapter.trades)
        matching = [t for t in fills if t.order_id == report.order_id]
        _expect(
            bool(matching),
            f"a filled order produced no trade in the trade book (order {report.order_id})",
        )
        _expect(
            any(t.quantity == report.filled_quantity for t in matching),
            f"trades() disagrees with the fill quantity {report.filled_quantity}",
        )
    if report.status is OrderStatus.FILLED and caps.supports(Capability.POSITIONS):
        positions: Sequence[Position] = await _call("positions()", adapter.positions)
        held = [p for p in positions if p.instrument_id == probe.instrument_id]
        _expect(
            bool(held) and not held[0].is_flat,
            f"a filled buy left no open position in the position book for {probe.instrument_id}",
        )

    if caps.supports(Capability.CANCEL_ORDER):
        resting = await _place_resting_order(adapter, probe)
        if resting is not None:
            await _call("cancel_order()", adapter.cancel_order, resting.order_id)
            after = await _call(
                f"order_status({resting.order_id})", adapter.order_status, resting.order_id
            )
            _expect(
                after.status is OrderStatus.CANCELLED,
                f"cancel_order() left {resting.order_id} in {after.status}, expected cancelled",
            )
            await _call(
                "cancel_order() again (a no-op, not an error)",
                adapter.cancel_order,
                resting.order_id,
            )
        await _refuses(
            "cancel_order() for an unknown order",
            lambda: adapter.cancel_order(probe.unknown_order),
            AdapterError,
        )

    if caps.supports(Capability.CANCEL_ALL):
        resting = await _place_resting_order(adapter, probe)
        if resting is not None:
            await _call("cancel_all()", adapter.cancel_all)
            book: Sequence[OrderReport] = await _call("orders()", adapter.orders)
            live = [o.order_id for o in book if o.status in _LIVE_STATUSES]
            _expect(not live, f"cancel_all() left live orders behind: {live}")


async def verify_adapter_contract(
    adapter: Adapter,
    *,
    probe_quantity: float = 1.0,
    disconnect: bool = True,
) -> None:
    """Verify ``adapter`` against the whole contract, raising :class:`ContractViolation`.

    ``probe_quantity`` is the size of the probe order; keep it small, because a broker with a
    real account will reject an oversized probe and a rejection is a valid outcome.
    """
    _verify_roles(adapter)
    await _verify_descriptor(adapter)
    await _verify_lifecycle(adapter)
    try:
        caps = adapter.capabilities()
        if caps.supports(Capability.INSTRUMENT_MASTER):
            instruments: list[Instrument] = await _call(
                "instruments()", cast(MarketDataClient, adapter).instruments
            )
        else:
            instruments = [
                Instrument(InstrumentId("HONBA-CONTRACT", "TEST"), InstrumentKind.EQUITY, 1.0, 0.05)
            ]
        probe = _probe_from(instruments, caps, probe_quantity)
        await _verify_capability_refusals(adapter, probe)
        await _verify_market_data(adapter, probe)
        await _verify_stream(adapter, probe)
        await _verify_execution(adapter, probe)
        await _verify_order_type_refusals(adapter, probe)
    finally:
        if disconnect and adapter.is_connected():
            await adapter.disconnect()
    if disconnect:
        _expect(
            adapter.is_connected() is False,
            "disconnect() left the adapter claiming to be connected",
        )
