"""A Python broker adapter behind ``/orders`` through ``honba._honba.GatewayServer``.

The fake adapter is pure Python. Orders go in over real HTTP, fills come out of the adapter's
``trades()``, and the order ledger is read back with ``GET /orders`` (ADR 0030, E3-S7).

The ledger row carries ``filled_qty`` / ``avg_fill_price`` and ``reject_reason`` (the stable
``ErrorCode`` string plus a message), so fills and refusals are asserted end to end here. The
full exception-to-code table is also covered in ``crates/honba-py/src/tests/python_gateway.rs``
(Python exception to ``PortError``) and ``gateway_venue.rs`` (``PortError`` to ``ErrorCode``).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

pytest.importorskip("honba._honba")

from honba._honba import GatewayServer
from honba.adapters.errors import AdapterFatalError
from honba.adapters.gateway import ExecutionAdapterGateway
from honba.adapters.models import OrderReport, OrderStatus, Product
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide
from honba.domain.trade import Trade

TCS = InstrumentId("TCS", "NSE")
T0 = 1_704_067_200_000_000_000


class FakeBroker:
    """Just enough of ``ExecutionAdapter`` for the gateway: orders, cancels and fills."""

    def __init__(self) -> None:
        self.placed: list[str | None] = []
        self.cancelled: list[str] = []
        self.fills: list[Trade] = []
        self.delay = 0.0
        self.fail: Exception | None = None
        self.reject: str | None = None

    async def place_order(
        self, intent: OrderIntent, *, product: Product, client_order_id: str | None = None
    ) -> OrderReport:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        self.placed.append(client_order_id)
        status = OrderStatus.REJECTED if self.reject else OrderStatus.ACCEPTED
        return OrderReport(
            order_id=f"B-{len(self.placed)}",
            instrument_id=intent.instrument_id,
            side=intent.side,
            quantity=intent.quantity,
            status=status,
            product=product,
            reject_reason=self.reject,
        )

    async def cancel_order(self, order_id: str) -> None:
        self.cancelled.append(order_id)

    async def trades(self) -> list[Trade]:
        return list(self.fills)

    def fill(self, venue_id: str, quantity: float, price: float = 100.0) -> None:
        self.fills.append(Trade(TCS, OrderSide.BUY, quantity, price, ts=T0, order_id=venue_id))


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    table = pa.table(
        {
            "ts": pa.array([T0 + i * 60_000_000_000 for i in range(3)], pa.int64()),
            "open": [100.0] * 3,
            "high": [101.0] * 3,
            "low": [99.0] * 3,
            "close": [100.0] * 3,
            "volume": [1000.0] * 3,
        }
    )
    pq.write_table(table, tmp_path / "TCS.NSE.parquet")
    return tmp_path


@pytest.fixture
def serve(data_dir: Path) -> Iterator[Callable[..., httpx.Client]]:
    servers: list[GatewayServer] = []
    clients: list[httpx.Client] = []

    def start(broker: FakeBroker, gateway: Any = None, **options: Any) -> httpx.Client:
        server = GatewayServer(
            str(data_dir),
            gateway or ExecutionAdapterGateway(broker),  # type: ignore[arg-type]
            poll_interval_ms=5,
            drain_interval_ms=5,
            **options,
        )
        server.start()
        servers.append(server)
        client = httpx.Client(base_url=server.url, timeout=5.0)
        clients.append(client)
        return client

    yield start
    for client in clients:
        client.close()
    for server in servers:
        server.stop()


def buy(order_id: str, qty: float = 25.0) -> dict[str, Any]:
    return {
        "instrument_id": {"symbol": "TCS", "exchange": "NSE"},
        "side": "buy",
        "qty": qty,
        "client_order_id": order_id,
    }


def order(client: httpx.Client, order_id: str) -> dict[str, Any]:
    rows = client.get("/orders").json()["data"]["orders"]
    [row] = [r for r in rows if r["order_id"] == order_id]
    return row


def until(client: httpx.Client, order_id: str, status: str) -> dict[str, Any]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        row = order(client, order_id)
        if row["status"] == status:
            return row
        time.sleep(0.01)
    raise AssertionError(f"{order_id} is {order(client, order_id)['status']}, wanted {status}")


def test_submit_then_partial_then_full_fill_reach_the_ledger(
    serve: Callable[..., httpx.Client],
) -> None:
    broker = FakeBroker()
    client = serve(broker)
    assert client.post("/orders", json=buy("o1")).status_code == 200
    until(client, "o1", "submitted")
    deadline = time.monotonic() + 5
    while not broker.placed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert broker.placed == ["o1"]

    broker.fill("B-1", 10.0)
    row = until(client, "o1", "partially_filled")
    assert (row["filled_qty"], row["avg_fill_price"]) == (10.0, 100.0)
    assert "reject_reason" not in row
    broker.fill("B-1", 15.0, price=102.0)
    row = until(client, "o1", "filled")
    assert row["filled_qty"] == 25.0
    assert row["avg_fill_price"] == pytest.approx((10 * 100.0 + 15 * 102.0) / 25.0)


def test_delete_cancels_on_the_broker_by_its_order_id(
    serve: Callable[..., httpx.Client],
) -> None:
    broker = FakeBroker()
    client = serve(broker)
    client.post("/orders", json=buy("o2"))
    deadline = time.monotonic() + 5
    while not broker.placed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert client.delete("/orders/o2").status_code == 200
    until(client, "o2", "cancelled")
    deadline = time.monotonic() + 5
    while not broker.cancelled and time.monotonic() < deadline:
        time.sleep(0.01)
    assert broker.cancelled == ["B-1"]


def test_a_broker_refusal_rejects_the_row(
    serve: Callable[..., httpx.Client],
) -> None:
    broker = FakeBroker()
    broker.reject = "RMS: margin"
    client = serve(broker)
    client.post("/orders", json=buy("o3"))
    row = until(client, "o3", "rejected")
    assert row["reject_reason"]["code"] == "order_rejected"
    # The broker's own text survives the Python exception -> PortError -> ledger path.
    assert row["reject_reason"]["message"] == "RMS: margin"
    assert "filled_qty" not in row


def test_a_raising_adapter_rejects_the_row(
    serve: Callable[..., httpx.Client],
) -> None:
    broker = FakeBroker()
    broker.fail = AdapterFatalError("account suspended")
    client = serve(broker)
    client.post("/orders", json=buy("o4"))
    row = until(client, "o4", "rejected")
    assert row["reject_reason"]["code"] == "order_execution_unavailable"


def test_a_slow_coroutine_does_not_stall_other_requests(
    serve: Callable[..., httpx.Client],
) -> None:
    broker = FakeBroker()
    broker.delay = 1.5
    client = serve(broker)
    started = time.monotonic()
    assert client.post("/orders", json=buy("slow")).status_code == 200
    assert client.post("/orders", json=buy("slow2")).status_code == 200
    for _ in range(5):
        assert client.get("/orders").status_code == 200
        assert client.get("/health").status_code == 200
    assert time.monotonic() - started < 1.0
    assert broker.placed == []  # the broker is still thinking
    deadline = time.monotonic() + 10
    while len(broker.placed) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert broker.placed == ["slow", "slow2"]


class SyncGateway:
    """The protocol with plain functions: they run on the blocking pool, not a coroutine loop."""

    def __init__(self) -> None:
        self.submitted: list[str] = []
        self.pending: list[str] = []

    def submit_order(self, order_json: str) -> str:
        self.submitted.append(order_json)
        return "S-1"

    def cancel_order(self, venue_id: str) -> None:
        return None

    def modify_order(self, venue_id: str, quantity: float, price: float | None) -> None:
        return None

    def next_fill(self) -> str | None:
        return self.pending.pop(0) if self.pending else None


def test_a_plain_function_gateway_works_without_coroutines(
    serve: Callable[..., httpx.Client],
) -> None:
    gateway = SyncGateway()
    client = serve(FakeBroker(), gateway=gateway)
    assert client.post("/orders", json=buy("p1")).status_code == 200
    deadline = time.monotonic() + 5
    while not gateway.submitted and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(gateway.submitted) == 1
    gateway.pending.append(
        json.dumps(
            {
                "order_id": "S-1",
                "instrument_id": {"symbol": "TCS", "exchange": "NSE"},
                "side": "buy",
                "quantity": 25.0,
                "price": 100.0,
                "costs": {"amount": 0, "currency": "INR"},
                "ts_event": {"iso": "1970-01-01T00:00:00.000000001Z", "unix_nanos": "1"},
                "ts_init": {"iso": "1970-01-01T00:00:00.000000001Z", "unix_nanos": "1"},
            }
        )
    )
    row = until(client, "p1", "filled")
    assert (row["filled_qty"], row["avg_fill_price"]) == (25.0, 100.0)


def test_a_call_that_outlives_the_timeout_rejects_the_order(
    serve: Callable[..., httpx.Client],
) -> None:
    broker = FakeBroker()
    broker.delay = 3.0
    client = serve(broker, call_timeout_s=0.2)
    client.post("/orders", json=buy("t1"))
    row = until(client, "t1", "rejected")
    assert row["reject_reason"]["code"] == "timeout"
