"""``RestExecutionAdapter`` in front of a ``GatewayServer`` with a Python broker behind it:
both directions of ADR 0030 in one process. Orders go REST -> Rust venue bridge -> Python
broker; the broker's fills and refusals come back as ledger statuses (the ledger carries no
fill quantity, price or reject reason, so only statuses are asserted)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

pytest.importorskip("honba._honba")

from honba._honba import GatewayServer
from honba.adapters.gateway import ExecutionAdapterGateway
from honba.adapters.models import OrderReport, OrderStatus, Product
from honba.adapters.rest import RestExecutionAdapter
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide
from honba.domain.trade import Trade

TCS = InstrumentId("TCS", "NSE")
T0 = 1_704_067_200_000_000_000


class Broker:
    def __init__(self) -> None:
        self.placed: list[str | None] = []
        self.cancelled: list[str] = []
        self.fills: list[Trade] = []
        self.reject: str | None = None

    async def place_order(
        self, intent: OrderIntent, *, product: Product, client_order_id: str | None = None
    ) -> OrderReport:
        self.placed.append(client_order_id)
        return OrderReport(
            order_id=f"B-{len(self.placed)}",
            instrument_id=intent.instrument_id,
            side=intent.side,
            quantity=intent.quantity,
            status=OrderStatus.REJECTED if self.reject else OrderStatus.ACCEPTED,
            product=product,
            reject_reason=self.reject,
        )

    async def cancel_order(self, order_id: str) -> None:
        self.cancelled.append(order_id)

    async def trades(self) -> list[Trade]:
        return list(self.fills)


@pytest.fixture
def venue(tmp_path: Path) -> Iterator[tuple[str, Broker]]:
    pq.write_table(
        pa.table(
            {
                "ts": pa.array([T0 + i * 60_000_000_000 for i in range(3)], pa.int64()),
                "open": [100.0] * 3,
                "high": [101.0] * 3,
                "low": [99.0] * 3,
                "close": [100.0] * 3,
                "volume": [1000.0] * 3,
            }
        ),
        tmp_path / "TCS.NSE.parquet",
    )
    broker = Broker()
    server = GatewayServer(
        str(tmp_path),
        ExecutionAdapterGateway(broker),  # type: ignore[arg-type]
        poll_interval_ms=5,
        drain_interval_ms=5,
    )
    server.start()
    try:
        yield server.url, broker
    finally:
        server.stop()


async def status_of(rest: RestExecutionAdapter, order_id: str, want: str) -> Any:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        rows = {r.order_id: r.status for r in await rest.ledger()}
        if rows.get(order_id) == want:
            return rows
        await asyncio.sleep(0.02)
    raise AssertionError(f"{order_id} never reached {want}: {rows}")


async def test_broker_fills_reach_the_rest_adapter_as_ledger_statuses(
    venue: tuple[str, Broker],
) -> None:
    url, broker = venue
    rest = RestExecutionAdapter(url, retry=(), fill_price=lambda _row: 100.0)
    await rest.connect()
    try:
        await rest.place_order(
            OrderIntent.market_buy(TCS, 25.0), product=Product.INTRADAY, client_order_id="g-1"
        )
        await status_of(rest, "g-1", "submitted")
        while not broker.placed:
            await asyncio.sleep(0.02)
        assert broker.placed == ["g-1"]
        broker.fills.append(Trade(TCS, OrderSide.BUY, 10.0, 100.0, ts=T0, order_id="B-1"))
        await status_of(rest, "g-1", "partially_filled")
        broker.fills.append(Trade(TCS, OrderSide.BUY, 15.0, 100.0, ts=T0, order_id="B-1"))
        await status_of(rest, "g-1", "filled")
        [position] = await rest.positions()
        assert position.signed_quantity == 25.0
    finally:
        await rest.disconnect()


async def test_cancel_and_broker_refusal_flow_through_the_bridge(
    venue: tuple[str, Broker],
) -> None:
    url, broker = venue
    rest = RestExecutionAdapter(url, retry=())
    await rest.connect()
    try:
        await rest.place_order(
            OrderIntent.market_buy(TCS, 5.0), product=Product.INTRADAY, client_order_id="g-2"
        )
        await status_of(rest, "g-2", "submitted")
        while not broker.placed:
            await asyncio.sleep(0.02)
        await rest.cancel_order("g-2")
        await status_of(rest, "g-2", "cancelled")
        broker.reject = "RMS: margin"
        await rest.place_order(
            OrderIntent.market_buy(TCS, 5.0), product=Product.INTRADAY, client_order_id="g-3"
        )
        await status_of(rest, "g-3", "rejected")
        report = await rest.order_status("g-3")
        assert report.status is OrderStatus.REJECTED
        assert report.reject_reason  # the placeholder: the ledger drops the broker's reason
    finally:
        await rest.disconnect()
