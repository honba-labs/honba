"""``RestExecutionAdapter`` against a real ``honba serve`` (ADR 0030, E3-S7).

Every server is a child process killed in ``finally`` by ``_runs_support.serve``; scratch
directories live under ``target/tmp`` and are removed. The ledger is status-only, so a fill is
proved by the row reaching ``filled`` and by what the adapter refuses to invent (see
:mod:`honba.adapters.rest`).
"""

from __future__ import annotations

import asyncio
import hashlib
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from _runs_support import MINUTE, T0, honba_binary, scratch, serve, walk, write_bars

from honba.adapters.errors import AdapterError, CapabilityError, SessionError
from honba.adapters.models import OrderStatus, Product
from honba.adapters.rest import (
    LedgerLimitationError,
    RestApiError,
    RestAuthError,
    RestExecutionAdapter,
)
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent

pytestmark = pytest.mark.skipif(honba_binary() is None, reason="honba binary not built")

TCS = InstrumentId("TCS", "NSE")
WRITE = "w" * 64
READ_ONLY = "r" * 64


def _token(token_id: str, token: str, scopes: str) -> str:
    digest = hashlib.sha256(token.encode()).hexdigest()
    return (
        f'[[tokens]]\ntoken_id = "{token_id}"\ntoken_hash = "sha256:{digest}"\n'
        f'scopes = [{scopes}]\nlabel = "{token_id}"\ncreated_at = "2026-10-11T09:35:09Z"\n\n'
    )


TOKENS = _token("writer1", WRITE, '"read:market", "write:orders"') + _token(
    "reader1", READ_ONLY, '"read:market"'
)


@contextmanager
def server(*flags: str, tokens: bool = False) -> Iterator[str]:
    binary = honba_binary()
    assert binary is not None
    with scratch("rest-adapter") as root:
        data = root / "data"
        data.mkdir()
        write_bars(data, "TCS", walk(5))
        extra = list(flags)
        if tokens:
            (root / "tokens.toml").write_text(TOKENS, encoding="utf-8")
            extra += ["--tokens-file", str(root / "tokens.toml")]
        with serve(binary, data, root / "journals", *extra) as url:
            yield url


@pytest.fixture
def base_url() -> Iterator[str]:
    with server("--bar-ingest") as url:
        yield url


def bar(ts: int, close: float = 100.0) -> dict[str, Any]:
    stamp = {"iso": "2024-01-01T00:00:00.000000000Z", "unix_nanos": str(ts)}
    return {
        "bar_type": {
            "instrument_id": {"symbol": "TCS", "exchange": "NSE"},
            "spec": {"step": 1, "aggregation": "minute", "price_type": "last"},
        },
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": 1000.0,
        "ts_event": stamp,
        "ts_init": stamp,
    }


async def until(check: Callable[[], Any], what: str) -> Any:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found = await check()
        if found:
            return found
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


async def test_submit_is_idempotent_and_visible_in_the_order_book(base_url: str) -> None:
    rest = RestExecutionAdapter(base_url, retry=())
    await rest.connect()
    try:
        buy = OrderIntent.market_buy(TCS, 10.0)
        first = await rest.place_order(buy, product=Product.INTRADAY, client_order_id="it-1")
        again = await rest.place_order(buy, product=Product.INTRADAY, client_order_id="it-1")
        assert first.status is OrderStatus.SUBMITTED
        assert again.order_id == "it-1"
        book = await rest.orders()
        assert [(r.order_id, r.status, r.quantity) for r in book] == [
            ("it-1", OrderStatus.SUBMITTED, 10.0)
        ]
        assert (await rest.order_status("it-1")).instrument_id == TCS
    finally:
        await rest.disconnect()


async def test_cancel_a_working_order_then_again_is_harmless(base_url: str) -> None:
    rest = RestExecutionAdapter(base_url, retry=())
    await rest.connect()
    try:
        await rest.place_order(
            OrderIntent.market_buy(TCS, 5.0), product=Product.INTRADAY, client_order_id="it-2"
        )
        await rest.cancel_order("it-2")
        assert (await rest.order_status("it-2")).status is OrderStatus.CANCELLED
        await rest.cancel_order("it-2")  # terminal: reported, not resent
        with pytest.raises(AdapterError):
            await rest.cancel_order("never-existed")
    finally:
        await rest.disconnect()


async def test_a_fill_shows_as_the_row_status_and_no_price_is_invented(base_url: str) -> None:
    rest = RestExecutionAdapter(base_url, retry=(), fill_price=lambda _row: 100.0)
    bare = RestExecutionAdapter(base_url, retry=())
    await rest.connect()
    await bare.connect()
    try:
        await rest.place_order(
            OrderIntent.market_buy(TCS, 10.0), product=Product.INTRADAY, client_order_id="it-3"
        )
        # The simulator fills when the market advances: feed bars after the order.
        bars = [bar(T0 + (10 + i) * MINUTE) for i in range(3)]
        sent = rest._client.transport.request("POST", "/bars", body={"bars": bars})
        assert sent.status == 200, sent.json

        async def filled() -> bool:
            return any(r.order_id == "it-3" and r.status == "filled" for r in await rest.ledger())

        await until(filled, "the row to reach filled")
        report = await rest.order_status("it-3")
        assert (report.status, report.filled_quantity, report.average_price) == (
            OrderStatus.FILLED,
            10.0,
            100.0,
        )
        with pytest.raises(LedgerLimitationError):
            await bare.order_status("it-3")  # the ledger has no fill price
        with pytest.raises(CapabilityError):  # trades() is a capability error
            await rest.trades()
        [position] = await rest.positions()
        assert (position.instrument_id, position.signed_quantity) == (TCS, 10.0)
        # a filled order cannot be cancelled: reported, no error
        await rest.cancel_order("it-3")
    finally:
        await rest.disconnect()
        await bare.disconnect()


async def test_a_venue_rejection_is_a_rejected_report(base_url: str) -> None:
    rest = RestExecutionAdapter(base_url, retry=())
    await rest.connect()
    try:
        report = await rest.place_order(
            OrderIntent.limit_buy(TCS, 1.0, 99.0),
            product=Product.INTRADAY,
            client_order_id="it-4",
        )
        assert report.status is OrderStatus.REJECTED
        assert report.reject_reason
    finally:
        await rest.disconnect()


async def test_a_risk_refusal_is_a_rejected_report_with_the_stable_code(base_url: str) -> None:
    rest = RestExecutionAdapter(base_url, retry=())
    await rest.connect()
    try:
        unknown = InstrumentId("NOPE", "NSE")
        report = await rest.place_order(
            OrderIntent.market_buy(unknown, 1.0), product=Product.INTRADAY, client_order_id="it-5"
        )
        assert report.status is OrderStatus.REJECTED
        assert report.reject_reason is not None
        assert report.reject_reason.startswith("risk_instrument_unknown")
        assert await rest.orders() == []  # a refusal never reaches the ledger
    finally:
        await rest.disconnect()


async def test_a_halted_server_refuses_orders_but_still_cancels() -> None:
    with server("--trading-state", "halted") as url:
        rest = RestExecutionAdapter(url, retry=())
        await rest.connect()
        try:
            report = await rest.place_order(
                OrderIntent.market_buy(TCS, 1.0),
                product=Product.INTRADAY,
                client_order_id="it-6",
            )
            assert report.status is OrderStatus.REJECTED
            assert report.reject_reason is not None
            assert report.reject_reason.startswith("risk_trading_halted")
            await rest.cancel_all()  # allowed in every state; nothing to cancel
        finally:
            await rest.disconnect()


async def test_bearer_token_and_scope_failures_are_fatal_auth_errors() -> None:
    with server(tokens=True) as url:
        ok = RestExecutionAdapter(url, token=WRITE, retry=())
        await ok.connect()
        try:
            report = await ok.place_order(
                OrderIntent.market_buy(TCS, 1.0), product=Product.INTRADAY, client_order_id="a-1"
            )
            assert report.status is OrderStatus.SUBMITTED
        finally:
            await ok.disconnect()

        for token, code in (
            (None, "unauthorized"),
            ("x" * 64, "unauthorized"),
            (READ_ONLY, "forbidden"),
        ):
            bad = RestExecutionAdapter(url, token=token, retry=())
            with pytest.raises(RestAuthError) as err:
                await bad.connect()
            assert err.value.api_code == code
            assert err.value.fatal
            assert not bad.is_connected()


async def test_an_unreachable_server_is_a_recoverable_session_error() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    rest = RestExecutionAdapter(f"http://127.0.0.1:{port}", retry=(), timeout=1.0)
    with pytest.raises(SessionError):
        await rest.connect()


async def test_a_silent_server_times_out_and_a_post_without_an_id_is_not_retried() -> None:
    accepted: list[int] = []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    listener.settimeout(0.2)
    stop = threading.Event()
    held: list[socket.socket] = []

    def accept() -> None:
        while not stop.is_set():
            try:
                held.append(listener.accept()[0])
                accepted.append(1)
            except OSError:
                continue

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        rest = RestExecutionAdapter(
            f"http://127.0.0.1:{listener.getsockname()[1]}", retry=(0.0, 0.0), timeout=0.3
        )
        rest._connected = True
        with pytest.raises(RestApiError) as err:
            await rest.place_order(OrderIntent.market_buy(TCS, 1.0), product=Product.INTRADAY)
        assert err.value.api_code == "timeout"
        assert err.value.retryable
        assert len(accepted) == 1  # one attempt: the order may exist, so no retry
    finally:
        stop.set()
        thread.join(timeout=2)
        for conn in held:
            conn.close()
        listener.close()
