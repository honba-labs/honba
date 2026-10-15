"""``ExecutionAdapterGateway``: an ``ExecutionAdapter`` behind the JSON gateway protocol the
Rust ``PythonGateway`` calls (ADR 0030, E3-S7)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from honba.adapters.errors import AdapterError
from honba.adapters.gateway import ExecutionAdapterGateway, GatewayRejected
from honba.adapters.models import OrderReport, OrderStatus, Product
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide, OrderType
from honba.domain.trade import Trade

X = InstrumentId("X", "NSE")


def _order_json(order_id: str = "o1", **over: Any) -> str:
    body: dict[str, Any] = {
        "order_id": order_id,
        "instrument_id": {"symbol": "X", "exchange": "NSE"},
        "side": "buy",
        "order_type": "market",
        "quantity": 25.0,
        "price": None,
        "trigger_price": None,
        "status": "submitted",
        "time_in_force": "day",
        "ts_event": {"iso": "1970-01-01T00:00:00.000000001Z", "unix_nanos": "1"},
        "ts_init": {"iso": "1970-01-01T00:00:00.000000001Z", "unix_nanos": "1"},
    }
    body.update(over)
    return json.dumps(body)


class Recorder:
    def __init__(self) -> None:
        self.placed: list[tuple[OrderIntent, Product, str | None]] = []
        self.cancelled: list[str] = []
        self.modified: list[tuple[str, float | None, float | None]] = []
        self.fills: list[Trade] = []
        self.reject: str | None = None

    async def place_order(
        self, intent: OrderIntent, *, product: Product, client_order_id: str | None = None
    ) -> OrderReport:
        self.placed.append((intent, product, client_order_id))
        if self.reject:
            return OrderReport(
                order_id="B-1",
                instrument_id=intent.instrument_id,
                side=intent.side,
                quantity=intent.quantity,
                status=OrderStatus.REJECTED,
                product=product,
                reject_reason=self.reject,
            )
        return OrderReport(
            order_id="B-1",
            instrument_id=intent.instrument_id,
            side=intent.side,
            quantity=intent.quantity,
            status=OrderStatus.ACCEPTED,
            product=product,
        )

    async def cancel_order(self, order_id: str) -> None:
        self.cancelled.append(order_id)

    async def modify_order(
        self,
        order_id: str,
        *,
        quantity: float | None = None,
        price: float | None = None,
        trigger_price: float | None = None,
    ) -> None:
        self.modified.append((order_id, quantity, price))

    async def trades(self) -> list[Trade]:
        return list(self.fills)


async def test_submit_places_the_intent_and_returns_the_broker_order_id() -> None:
    rec = Recorder()
    gw = ExecutionAdapterGateway(rec, product=Product.DELIVERY)
    venue_id = await gw.submit_order(
        _order_json("c1", order_type="limit", price=101.5, side="sell")
    )
    assert venue_id == "B-1"
    [(intent, product, client_id)] = rec.placed
    assert intent == OrderIntent(X, OrderSide.SELL, 25.0, OrderType.LIMIT, price=101.5)
    assert product is Product.DELIVERY
    assert client_id == "c1"


async def test_a_broker_refusal_raises_gateway_rejected_with_the_reason() -> None:
    rec = Recorder()
    rec.reject = "RMS:margin"
    gw = ExecutionAdapterGateway(rec)
    with pytest.raises(GatewayRejected) as raised:
        await gw.submit_order(_order_json())
    assert raised.value.reject_code == "RMS:margin"
    assert isinstance(raised.value, AdapterError)


async def test_cancel_and_modify_forward_to_the_adapter() -> None:
    rec = Recorder()
    gw = ExecutionAdapterGateway(rec)
    await gw.cancel_order("B-1")
    await gw.modify_order("B-1", 10.0, None)
    assert rec.cancelled == ["B-1"]
    assert rec.modified == [("B-1", 10.0, None)]


async def test_next_fill_yields_each_new_trade_once_then_none() -> None:
    rec = Recorder()
    gw = ExecutionAdapterGateway(rec)
    assert await gw.next_fill() is None
    rec.fills.append(Trade(X, OrderSide.BUY, 10.0, 100.0, ts=5, order_id="B-1"))
    first = json.loads(await gw.next_fill() or "null")
    assert first["order_id"] == "B-1"
    assert first["quantity"] == 10.0
    assert first["instrument_id"] == {"symbol": "X", "exchange": "NSE"}
    assert first["ts_event"]["unix_nanos"] == "5"
    assert await gw.next_fill() is None
    rec.fills.append(Trade(X, OrderSide.BUY, 15.0, 101.0, ts=6, order_id="B-1"))
    assert json.loads(await gw.next_fill() or "null")["quantity"] == 15.0
    assert await gw.next_fill() is None
