"""``FakeAdapter`` derives every report status from an ``OrderState`` (ADR 0019 section 9).

The adapter port surface is unchanged (adapter contract (c) is a later story); only the way
the fake arrives at ``OrderReport.status`` changes: events go through the FSM, nothing sets
``FILLED`` / ``CANCELLED`` / ``REJECTED`` directly.
"""

from __future__ import annotations

from honba.adapters.models import Product
from honba.adapters.testing import FakeAdapter
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderStatus
from honba.entities.order_state import OrderState

RELIANCE = InstrumentId("RELIANCE", "NSE")


async def _connected(**kw: float) -> FakeAdapter:
    adapter = FakeAdapter(**kw)
    await adapter.connect()
    return adapter


async def test_a_marketable_order_is_filled_through_the_fsm() -> None:
    adapter = await _connected()
    report = await adapter.place_order(
        OrderIntent.market_buy(RELIANCE, 3), product=Product.INTRADAY, client_order_id="c-1"
    )
    state = adapter.order_state("c-1")
    assert isinstance(state, OrderState)
    assert (state.status, state.quantity, state.filled_qty) == (OrderStatus.FILLED, 3.0, 3.0)
    assert report.status is state.status
    assert report.filled_quantity == state.filled_qty


async def test_a_resting_order_is_accepted_then_cancelled_through_the_fsm() -> None:
    adapter = await _connected()
    quote = await adapter.quote(RELIANCE)
    resting = await adapter.place_order(
        OrderIntent.limit_buy(RELIANCE, 2, quote.bid_price - 5.0),
        product=Product.INTRADAY,
        client_order_id="c-2",
    )
    state = adapter.order_state("c-2")
    assert state.status is OrderStatus.ACCEPTED and resting.status is OrderStatus.ACCEPTED
    await adapter.cancel_order("c-2")
    assert adapter.order_state("c-2").status is OrderStatus.CANCELLED
    assert adapter.order_state("c-2").cancel_requested is False  # cleared on the terminal
    assert (await adapter.order_status("c-2")).status is OrderStatus.CANCELLED
    await adapter.cancel_order("c-2")  # terminal: still a no-op
    assert adapter.order_state("c-2").status is OrderStatus.CANCELLED


async def test_a_rejected_order_is_rejected_through_the_fsm() -> None:
    adapter = await _connected(opening_balance=1.0)
    report = await adapter.place_order(
        OrderIntent.market_buy(RELIANCE, 1000), product=Product.INTRADAY, client_order_id="c-3"
    )
    assert report.status is OrderStatus.REJECTED
    assert adapter.order_state("c-3").status is OrderStatus.REJECTED


async def test_every_report_status_equals_its_state() -> None:
    adapter = await _connected()
    for n in range(3):
        await adapter.place_order(OrderIntent.market_buy(RELIANCE, 1 + n), product=Product.INTRADAY)
    for report in await adapter.orders():
        assert report.status is adapter.order_state(report.order_id).status
