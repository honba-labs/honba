"""The capability descriptor an adapter publishes (E1-S1).

An adapter declares what it can do as data, before it is called, so the engine can refuse an
unsupported request with a typed :class:`~honba.adapters.errors.CapabilityError` instead of
discovering it as an ``AttributeError`` or, worse, a failed HTTP call. Every adapter method
is mapped to the capability it needs in :data:`_METHOD_CAPABILITIES`; the streaming methods
are exempt because they are checked against ``stream_modes`` instead (the mode is an
argument, so the capability depends on the call).

The vocabulary here is market-neutral. Broker product codes (CNC, NRML, MIS) are E1-S4 and
live in the per-adapter mapping tables, not in the core.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from honba.adapters.errors import CapabilityError
from honba.adapters.models import Product, StreamMode
from honba.domain.order import OrderType, TimeInForce
from honba.wire.wire import PriceType

__all__ = [
    "AdapterCapabilities",
    "Capability",
    "capability_for_method",
    "default_capabilities",
]

_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class Capability(Enum):
    """An operation an adapter may or may not support.

    Streaming is expressed by :class:`~honba.adapters.models.StreamMode` rather than by
    members here, because the mode is chosen per subscription.
    """

    PLACE_ORDER = "place_order"
    MODIFY_ORDER = "modify_order"
    CANCEL_ORDER = "cancel_order"
    CANCEL_ALL = "cancel_all"
    ORDER_BOOK = "order_book"
    TRADE_BOOK = "trade_book"
    POSITIONS = "positions"
    HOLDINGS = "holdings"
    FUNDS = "funds"
    MARGIN = "margin"
    QUOTES = "quotes"
    DEPTH = "depth"
    HISTORICAL_BARS = "historical_bars"
    INSTRUMENT_MASTER = "instrument_master"


#: Adapter method name -> the capability it requires, or ``None`` when it is checked against
#: ``stream_modes`` instead. Every public method of the role protocols must appear here.
_METHOD_CAPABILITIES: dict[str, Capability | None] = {
    "connect": None,  # every adapter must connect
    "disconnect": None,
    "capabilities": None,
    "require_connected": None,
    "require_capabilities": None,
    "session": None,
    "is_connected": None,
    "place_order": Capability.PLACE_ORDER,
    "modify_order": Capability.MODIFY_ORDER,
    "cancel_order": Capability.CANCEL_ORDER,
    "cancel_all": Capability.CANCEL_ALL,
    "order_status": Capability.ORDER_BOOK,
    "orders": Capability.ORDER_BOOK,
    "trades": Capability.TRADE_BOOK,
    "positions": Capability.POSITIONS,
    "holdings": Capability.HOLDINGS,
    "funds": Capability.FUNDS,
    "margin": Capability.MARGIN,
    "instruments": Capability.INSTRUMENT_MASTER,
    "search_instruments": Capability.INSTRUMENT_MASTER,
    "quote": Capability.QUOTES,
    "depth": Capability.DEPTH,
    "historical_bars": Capability.HISTORICAL_BARS,
    "subscribe": None,  # stream mode is the argument
    "unsubscribe": None,
}


def capability_for_method(method: str) -> Capability | None:
    """The capability ``method`` requires, or ``None`` if it needs none.

    Unknown names return ``None``: a helper for arbitrary attribute names must not invent a
    requirement. The contract suite asserts the table covers every protocol method, so an
    unmapped *new* method fails a test rather than slipping through.
    """
    return _METHOD_CAPABILITIES.get(method)


_ALL_PRODUCTS = frozenset(Product)
#: Order types an adapter must list explicitly; default capabilities never claim them.
_OPT_IN_ORDER_TYPES = frozenset({OrderType.TRAILING_STOP})
_ALL_ORDER_TYPES = frozenset(OrderType) - _OPT_IN_ORDER_TYPES
_ALL_TIME_IN_FORCE = frozenset(TimeInForce)
_ALL_PRICE_TYPES = frozenset(PriceType)
_ALL_STREAM_MODES = frozenset(StreamMode)
_ALL_FEATURES = frozenset(Capability)


def _require_non_empty_set(name: str, values: frozenset[object]) -> None:
    if not values:
        raise ValueError(f"{name} must declare at least one value")
    for value in values:
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"{name} contains a blank entry")


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    """What an adapter supports: exchanges, products, order vocabulary, prices and feed modes.

    Every dimension is a non-empty set. The descriptor is pure data, frozen, and cheap to
    build once at adapter construction.
    """

    name: str = "fake"
    exchanges: frozenset[str] = field(default_factory=lambda: frozenset({"NSE"}))
    products: frozenset[Product] = _ALL_PRODUCTS
    order_types: frozenset[OrderType] = _ALL_ORDER_TYPES
    time_in_force: frozenset[TimeInForce] = _ALL_TIME_IN_FORCE
    price_types: frozenset[PriceType] = _ALL_PRICE_TYPES
    stream_modes: frozenset[StreamMode] = _ALL_STREAM_MODES
    features: frozenset[Capability] = _ALL_FEATURES

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.match(self.name):
            raise ValueError(
                f"name must be a lower-case slug ([a-z0-9] then [a-z0-9._-]), got {self.name!r}"
            )
        for dimension in (
            "exchanges",
            "products",
            "order_types",
            "time_in_force",
            "price_types",
            "stream_modes",
            "features",
        ):
            _require_non_empty_set(dimension, getattr(self, dimension))

    def supports(self, *capabilities: Capability) -> bool:
        """Whether every named capability is supported."""
        return all(capability in self.features for capability in capabilities)

    def require(self, *capabilities: Capability) -> None:
        """Raise :class:`CapabilityError` naming the unsupported capabilities.

        Reports only what is missing, so the message stays short and actionable.
        """
        missing = sorted(
            (c.value for c in capabilities if c not in self.features),
        )
        if missing:
            raise CapabilityError(f"adapter {self.name} does not support: {', '.join(missing)}")

    def supports_product(self, product: Product) -> bool:
        return product in self.products

    def require_product(self, product: Product) -> None:
        if product not in self.products:
            raise CapabilityError(f"adapter {self.name} does not support product {product.value}")

    def supports_order_type(self, order_type: OrderType) -> bool:
        return order_type in self.order_types

    def require_order_type(self, order_type: OrderType) -> None:
        if order_type not in self.order_types:
            raise CapabilityError(
                f"adapter {self.name} does not support order type {order_type.value}"
            )

    def supports_time_in_force(self, time_in_force: TimeInForce) -> bool:
        return time_in_force in self.time_in_force

    def require_time_in_force(self, time_in_force: TimeInForce) -> None:
        if time_in_force not in self.time_in_force:
            raise CapabilityError(
                f"adapter {self.name} does not support time in force {time_in_force.value}"
            )

    def supports_price_type(self, price_type: PriceType) -> bool:
        return price_type in self.price_types

    def require_price_type(self, price_type: PriceType) -> None:
        if price_type not in self.price_types:
            raise CapabilityError(
                f"adapter {self.name} does not support price type {price_type.value}"
            )

    def supports_stream_mode(self, mode: StreamMode) -> bool:
        return mode in self.stream_modes

    def require_stream_mode(self, mode: StreamMode) -> None:
        if mode not in self.stream_modes:
            raise CapabilityError(f"adapter {self.name} does not support stream mode {mode.value}")


def default_capabilities(**overrides: object) -> AdapterCapabilities:
    """A full-capability baseline descriptor, with ``overrides`` applied.

    Used by :class:`honba.adapters.testing.FakeAdapter` and by tests that need a valid
    descriptor without spelling out all seven dimensions. Real adapters build
    :class:`AdapterCapabilities` directly with their own sets.
    """
    return AdapterCapabilities(**overrides)  # type: ignore[arg-type]
