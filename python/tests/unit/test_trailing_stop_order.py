"""Unit tests for the opt-in trailing stop order type: intent validation and capability."""

from __future__ import annotations

import math

import pytest

from honba.adapters.capabilities import AdapterCapabilities, default_capabilities
from honba.adapters.errors import CapabilityError
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide, OrderType, validate_intent

NIFTY = InstrumentId("NIFTY50", "NSE")
TS = OrderType.TRAILING_STOP


class TestOrderType:
    def test_wire_value(self) -> None:
        assert TS.value == "trailing_stop"
        assert OrderType("trailing_stop") is TS

    def test_stop_alias_still_resolves_to_stop_market(self) -> None:
        assert OrderType.STOP is OrderType.STOP_MARKET


class TestConstructors:
    def test_sell_protects_a_long_with_amount(self) -> None:
        i = OrderIntent.trailing_stop_sell(NIFTY, 75, trail_amount=50.0)
        assert (i.side, i.order_type) == (OrderSide.SELL, TS)
        assert (i.trail_amount, i.trail_percent) == (50.0, None)
        assert (i.price, i.trigger_price) == (None, None)

    def test_sell_with_percent(self) -> None:
        i = OrderIntent.trailing_stop_sell(NIFTY, 75, trail_percent=2.5)
        assert (i.trail_amount, i.trail_percent) == (None, 2.5)

    def test_buy_protects_a_short(self) -> None:
        i = OrderIntent.trailing_stop_buy(NIFTY, 75, trail_percent=1.0)
        assert (i.side, i.order_type, i.trail_percent) == (OrderSide.BUY, TS, 1.0)

    @pytest.mark.parametrize("ctor", ["trailing_stop_sell", "trailing_stop_buy"])
    def test_requires_exactly_one_trail(self, ctor: str) -> None:
        make = getattr(OrderIntent, ctor)
        with pytest.raises(ValueError, match="exactly one"):
            make(NIFTY, 1)
        with pytest.raises(ValueError, match="exactly one"):
            make(NIFTY, 1, trail_amount=1.0, trail_percent=1.0)


class TestValidation:
    def _v(self, **kw: object) -> None:
        args: dict[str, object] = {
            "side": OrderSide.SELL,
            "quantity": 1.0,
            "order_type": TS,
            "price": None,
            "trigger_price": None,
        }
        args.update(kw)
        validate_intent(**args)  # type: ignore[arg-type]

    def test_valid_amount_and_percent(self) -> None:
        self._v(trail_amount=0.05)
        self._v(trail_percent=99.9)

    def test_rejects_price(self) -> None:
        with pytest.raises(ValueError, match="takes no price"):
            self._v(price=10.0, trail_amount=1.0)

    def test_rejects_trigger_price(self) -> None:
        with pytest.raises(ValueError, match="takes no trigger_price"):
            self._v(trigger_price=10.0, trail_amount=1.0)

    def test_rejects_neither_and_both(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            self._v()
        with pytest.raises(ValueError, match="exactly one"):
            self._v(trail_amount=1.0, trail_percent=1.0)

    @pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf, -math.inf])
    def test_amount_must_be_finite_positive(self, bad: float) -> None:
        with pytest.raises(ValueError, match="trail_amount"):
            self._v(trail_amount=bad)

    @pytest.mark.parametrize("bad", [0.0, -1.0, 100.0, 150.0, math.nan, math.inf])
    def test_percent_bounds(self, bad: float) -> None:
        with pytest.raises(ValueError, match="trail_percent"):
            self._v(trail_percent=bad)

    @pytest.mark.parametrize(
        "order_type,price,trigger",
        [
            (OrderType.MARKET, None, None),
            (OrderType.LIMIT, 10.0, None),
            (OrderType.STOP_MARKET, None, 10.0),
            (OrderType.STOP_LIMIT, 10.0, 11.0),
        ],
    )
    @pytest.mark.parametrize("field", ["trail_amount", "trail_percent"])
    def test_other_types_reject_trail_fields(
        self, order_type: OrderType, price: float | None, trigger: float | None, field: str
    ) -> None:
        with pytest.raises(ValueError, match=field):
            self._v(order_type=order_type, price=price, trigger_price=trigger, **{field: 1.0})

    def test_existing_callers_without_trail_args_still_work(self) -> None:
        validate_intent(OrderSide.BUY, 1.0, OrderType.MARKET, None, None)

    def test_intent_dataclass_rejects_trail_on_market(self) -> None:
        with pytest.raises(ValueError, match="trail_amount"):
            OrderIntent(NIFTY, OrderSide.BUY, 1.0, trail_amount=1.0)


class TestCapability:
    def test_default_does_not_claim_trailing_stop(self) -> None:
        caps = default_capabilities()
        assert TS not in caps.order_types
        assert not caps.supports_order_type(TS)
        assert {
            OrderType.MARKET,
            OrderType.LIMIT,
            OrderType.STOP_MARKET,
            OrderType.STOP_LIMIT,
        } <= caps.order_types

    def test_require_raises_when_not_declared(self) -> None:
        with pytest.raises(CapabilityError, match="trailing_stop"):
            default_capabilities().require_order_type(TS)

    def test_explicit_inclusion_enables_it(self) -> None:
        caps = AdapterCapabilities(order_types=frozenset({OrderType.MARKET, TS}))
        assert caps.supports_order_type(TS)
        caps.require_order_type(TS)
