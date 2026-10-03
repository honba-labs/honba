"""Unit tests for the adapter capability descriptor (E1-S1).

The descriptor is how the engine learns what an adapter can do before it calls it, so it is
declared once as data and every set is non-empty enough to be meaningful.
"""

from __future__ import annotations

import math

import pytest

from honba.adapters.capabilities import (
    Capability,
    capability_for_method,
    default_capabilities,
)
from honba.adapters.errors import CapabilityError
from honba.adapters.models import Product, StreamMode
from honba.domain.order import OrderType, TimeInForce
from honba.wire.wire import PriceType


class TestCapabilityVocabulary:
    def test_capabilities_cover_the_broker_function_set(self) -> None:
        values = {c.value for c in Capability}
        assert {
            "place_order",
            "modify_order",
            "cancel_order",
            "cancel_all",
            "order_book",
            "trade_book",
            "positions",
            "holdings",
            "funds",
            "quotes",
            "depth",
            "historical_bars",
            "instrument_master",
        } <= values

    def test_capability_values_are_stable_wire_names(self) -> None:
        assert Capability.PLACE_ORDER.value == "place_order"
        assert all(c.value == c.name.lower() for c in Capability)


class TestAdapterCapabilities:
    def test_a_default_descriptor_is_valid_and_self_consistent(self) -> None:
        caps = default_capabilities()
        assert caps.name == "fake"
        assert Product.DELIVERY in caps.products
        assert OrderType.MARKET in caps.order_types
        assert TimeInForce.DAY in caps.time_in_force
        assert PriceType.LAST in caps.price_types
        assert StreamMode.QUOTE in caps.stream_modes
        assert caps.venues == frozenset({"NSE"})

    def test_name_is_the_registry_key_and_must_be_a_clean_slug(self) -> None:
        caps = default_capabilities(name="dhan-2")
        assert caps.name == "dhan-2"
        for bad in ("", " ", "Dhan", "two words", "dhan/x"):
            with pytest.raises(ValueError, match="name"):
                default_capabilities(name=bad)

    def test_every_dimension_must_declare_at_least_one_value(self) -> None:
        with pytest.raises(ValueError, match="venues"):
            default_capabilities(venues=frozenset())
        with pytest.raises(ValueError, match="products"):
            default_capabilities(products=frozenset())
        with pytest.raises(ValueError, match="order_types"):
            default_capabilities(order_types=frozenset())
        with pytest.raises(ValueError, match="time_in_force"):
            default_capabilities(time_in_force=frozenset())
        with pytest.raises(ValueError, match="price_types"):
            default_capabilities(price_types=frozenset())
        with pytest.raises(ValueError, match="stream_modes"):
            default_capabilities(stream_modes=frozenset())

    def test_sets_are_immutable(self) -> None:
        with pytest.raises(AttributeError):
            default_capabilities().venues = frozenset({"BSE"})  # type: ignore[misc]

    def test_venue_entries_must_not_be_blank_or_nan_free_garbage(self) -> None:
        with pytest.raises(ValueError, match="venues"):
            default_capabilities(venues=frozenset({""}))
        with pytest.raises(ValueError, match="venues"):
            default_capabilities(venues=frozenset({"NSE", " "}))

    def test_supports_requires_every_capability(self) -> None:
        caps = default_capabilities(features=frozenset({Capability.PLACE_ORDER}))
        assert caps.supports(Capability.PLACE_ORDER)
        assert not caps.supports(Capability.PLACE_ORDER, Capability.DEPTH)
        assert caps.supports() is True

    def test_require_raises_and_names_what_is_missing(self) -> None:
        caps = default_capabilities(features=frozenset({Capability.PLACE_ORDER}))
        with pytest.raises(CapabilityError) as excinfo:
            caps.require(Capability.PLACE_ORDER, Capability.DEPTH)
        message = str(excinfo.value)
        assert "depth" in message
        assert "place_order" not in message

    def test_require_is_silent_when_everything_is_supported(self) -> None:
        caps = default_capabilities()
        caps.require(Capability.PLACE_ORDER, Capability.FUNDS)

    def test_require_reports_the_adapter_name_for_actionable_errors(self) -> None:
        caps = default_capabilities(features=frozenset({Capability.PLACE_ORDER}))
        with pytest.raises(CapabilityError, match="fake") as excinfo:
            caps.require(Capability.MARGIN)
        assert "margin" in str(excinfo.value)

    def test_per_dimension_predicates(self) -> None:
        caps = default_capabilities(
            products=frozenset({Product.INTRADAY}),
            order_types=frozenset({OrderType.MARKET, OrderType.LIMIT}),
            time_in_force=frozenset({TimeInForce.DAY, TimeInForce.IOC}),
            price_types=frozenset({PriceType.LAST}),
            stream_modes=frozenset({StreamMode.LTP}),
        )
        assert caps.supports_product(Product.INTRADAY)
        assert not caps.supports_product(Product.CARRY)
        assert caps.supports_order_type(OrderType.LIMIT)
        assert not caps.supports_order_type(OrderType.STOP_LIMIT)
        assert caps.supports_time_in_force(TimeInForce.IOC)
        assert not caps.supports_time_in_force(TimeInForce.GTC)
        assert caps.supports_price_type(PriceType.LAST)
        assert not caps.supports_price_type(PriceType.BID)
        assert caps.supports_stream_mode(StreamMode.LTP)
        assert not caps.supports_stream_mode(StreamMode.DEPTH)

    def test_requiring_an_unsupported_product_names_the_product(self) -> None:
        caps = default_capabilities(products=frozenset({Product.INTRADAY}))
        with pytest.raises(CapabilityError, match="carry"):
            caps.require_product(Product.CARRY)

    def test_unsupported_combination_is_rejected_not_silently_downgraded(self) -> None:
        caps = default_capabilities(order_types=frozenset({OrderType.MARKET}))
        with pytest.raises(CapabilityError, match="stop_limit"):
            caps.require_order_type(OrderType.STOP_LIMIT)

    def test_descriptor_does_not_accept_nan_floats_in_price_related_data(self) -> None:
        # Guards the descriptor itself against a float leaking in through **kwargs style calls.
        with pytest.raises((TypeError, ValueError)):
            default_capabilities(venues=frozenset({"NSE"}), max_orders_per_second=math.nan)


class TestCapabilityForMethod:
    def test_maps_every_execution_and_data_method_to_a_capability(self) -> None:
        assert capability_for_method("place_order") is Capability.PLACE_ORDER
        assert capability_for_method("modify_order") is Capability.MODIFY_ORDER
        assert capability_for_method("cancel_order") is Capability.CANCEL_ORDER
        assert capability_for_method("cancel_all") is Capability.CANCEL_ALL
        assert capability_for_method("orders") is Capability.ORDER_BOOK
        assert capability_for_method("trades") is Capability.TRADE_BOOK
        assert capability_for_method("positions") is Capability.POSITIONS
        assert capability_for_method("holdings") is Capability.HOLDINGS
        assert capability_for_method("funds") is Capability.FUNDS
        assert capability_for_method("quote") is Capability.QUOTES
        assert capability_for_method("depth") is Capability.DEPTH
        assert capability_for_method("historical_bars") is Capability.HISTORICAL_BARS
        assert capability_for_method("instruments") is Capability.INSTRUMENT_MASTER

    def test_stream_methods_are_checked_against_stream_modes_instead(self) -> None:
        assert capability_for_method("subscribe") is None
        assert capability_for_method("unsubscribe") is None

    def test_unknown_method_has_no_capability(self) -> None:
        assert capability_for_method("do_the_thing") is None
