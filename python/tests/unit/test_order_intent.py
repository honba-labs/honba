"""Unit tests for the domain OrderIntent / Trade and their wire mapping (E0-S2)."""

from __future__ import annotations

import pytest

from honba.entities import wire
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderType, TimeInForce
from honba.entities.trade import Trade

NIFTY = InstrumentId("NIFTY50", "NSE")


def test_stop_constructors():
    i = OrderIntent.stop_buy(NIFTY, 75, 22050.0)
    assert (i.order_type, i.price, i.trigger_price) == (OrderType.STOP_MARKET, None, 22050.0)
    i = OrderIntent.stop_sell(NIFTY, 75, 21950.0)
    assert (i.side, i.trigger_price) == (OrderSide.SELL, 21950.0)
    i = OrderIntent.stop_limit_buy(NIFTY, 75, 22000.0, 22010.0)
    assert (i.order_type, i.trigger_price, i.price) == (OrderType.STOP_LIMIT, 22000.0, 22010.0)
    i = OrderIntent.stop_limit_sell(NIFTY, 75, 21950.0, 21940.0)
    assert (i.side, i.trigger_price, i.price) == (OrderSide.SELL, 21950.0, 21940.0)


def test_legacy_stop_name_is_stop_market():
    assert OrderType.STOP is OrderType.STOP_MARKET
    assert OrderType("stop") is OrderType.STOP_MARKET
    assert OrderType.STOP.value == "stop_market"


def test_time_in_force_matches_rust():
    assert {t.value for t in TimeInForce} == {"day", "gtc", "ioc", "fok", "gtd"}


@pytest.mark.parametrize(
    ("order_type", "price", "trigger"),
    [
        (OrderType.LIMIT, None, None),
        (OrderType.LIMIT, 1.0, 1.0),
        (OrderType.MARKET, 1.0, None),
        (OrderType.MARKET, None, 1.0),
        (OrderType.STOP_MARKET, None, None),
        (OrderType.STOP_MARKET, 1.0, 1.0),
        (OrderType.STOP_LIMIT, 1.0, None),
        (OrderType.STOP_LIMIT, None, 1.0),
        (OrderType.LIMIT, float("inf"), None),
    ],
)
def test_price_rules_per_order_type(order_type, price, trigger):
    with pytest.raises(ValueError):
        OrderIntent(NIFTY, OrderSide.BUY, 1.0, order_type, price, trigger_price=trigger)


def test_intent_rejects_no_side_and_bad_quantity():
    with pytest.raises(ValueError):
        OrderIntent(NIFTY, OrderSide.NO_ORDER_SIDE, 1.0)
    for q in (0.0, -1.0, float("nan")):
        with pytest.raises(ValueError):
            OrderIntent(NIFTY, OrderSide.BUY, q)


def test_wire_intent_maps_to_and_from_domain():
    domain = OrderIntent.stop_limit_sell(NIFTY, 75.0, 21950.0, 21940.0)
    w = wire.OrderIntent.from_domain(domain)
    assert w.trigger_price == 21950.0 and w.price == 21940.0
    assert w.instrument_id == wire.InstrumentId(symbol="NIFTY50", exchange="NSE")
    assert w.to_domain() == domain
    assert wire.InstrumentId.from_domain(NIFTY).to_domain() == NIFTY


def test_trade_has_order_id_and_costs_defaults():
    t = Trade(NIFTY, OrderSide.BUY, 4, 100.0)
    assert (t.order_id, t.costs) == (None, 0.0)
    t = Trade(NIFTY, OrderSide.BUY, 4, 100.0, ts=1, order_id="O-1", costs=2.5)
    assert (t.order_id, t.costs) == ("O-1", 2.5)
