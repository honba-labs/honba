"""``RestExecutionAdapter`` over a scripted ``Transport``: request building, error mapping,
retry rules (ADR 0030, E3-S7). The real-server proof is
``tests/integration/test_rest_execution_adapter.py``."""

from __future__ import annotations

from typing import Any

import pytest

from honba.adapters.capabilities import Capability
from honba.adapters.errors import AdapterError, AdapterFatalError, CapabilityError, SessionError
from honba.adapters.models import OrderStatus, Product
from honba.adapters.rest import LedgerLimitationError, RestApiError, RestExecutionAdapter
from honba.client import Client, Response
from honba.client.errors import TransportApiError
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide, OrderType, TimeInForce

try:
    from honba.wire.wire import SCHEMA_VERSION as SCHEMA
except ImportError:
    SCHEMA = 1

STAMP = {"iso": "1970-01-01T00:00:00.000000000Z", "unix_nanos": "0"}
TCS = InstrumentId("TCS", "NSE")


def ok(data: Any) -> Response:
    return Response(200, {"api_version": "1.0.0", "schema_version": SCHEMA, "data": data})


def failure(status: int, code: str, retryable: bool = False) -> Response:
    error = {"code": code, "message": f"{code} message", "retryable": retryable}
    return Response(status, {"api_version": "1.0.0", "schema_version": SCHEMA, "error": error})


def row(order_id: str = "c-1", status: str = "submitted", **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "order_id": order_id,
        "instrument_id": {"symbol": "TCS", "exchange": "NSE"},
        "side": "buy",
        "order_type": "market",
        "quantity": 10.0,
        "price": None,
        "trigger_price": None,
        "status": status,
        "time_in_force": "day",
        "ts_event": STAMP,
        "ts_init": STAMP,
    }
    body.update(over)
    return body


class Script:
    """A ``Transport`` that answers from a queue (a ``TransportApiError`` is raised)."""

    def __init__(self, *answers: Response | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str, Any]] = []

    def request(
        self, method: str, path: str, *, query: dict[str, Any] | None = None, body: Any = None
    ) -> Response:
        self.calls.append((method, path, body))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def close(self) -> None:
        pass


async def no_sleep(_: float) -> None:
    return None


def adapter(*answers: Response | Exception, **options: Any) -> tuple[RestExecutionAdapter, Script]:
    script = Script(*answers)
    rest = RestExecutionAdapter("http://unused", client=Client(script), sleep=no_sleep, **options)
    rest._connected = True  # the connect handshake has its own test
    return rest, script


BUY = OrderIntent.market_buy(TCS, 10.0)


async def test_place_order_posts_with_the_idempotency_key_and_reports_submitted() -> None:
    rest, script = adapter(ok(row()))
    report = await rest.place_order(BUY, product=Product.INTRADAY, client_order_id="c-1")
    assert report.status is OrderStatus.SUBMITTED
    assert (report.order_id, report.quantity, report.filled_quantity) == ("c-1", 10.0, 0.0)
    method, path, body = script.calls[0]
    assert (method, path, body["client_order_id"], body["side"]) == (
        "POST",
        "/orders",
        "c-1",
        "buy",
    )


async def test_a_risk_refusal_is_a_rejected_report_not_an_exception() -> None:
    rest, _ = adapter(failure(422, "risk_trading_halted"))
    report = await rest.place_order(BUY, product=Product.INTRADAY, client_order_id="c-2")
    assert report.status is OrderStatus.REJECTED
    assert report.order_id == "c-2"
    assert report.reject_reason is not None
    assert "risk_trading_halted" in report.reject_reason


async def test_an_order_rejected_answer_is_a_rejected_report() -> None:
    rest, _ = adapter(failure(422, "order_rejected"))
    report = await rest.place_order(BUY, product=Product.INTRADAY, client_order_id="c-3")
    assert report.status is OrderStatus.REJECTED
    assert report.reject_reason is not None
    assert "order_rejected" in report.reject_reason


async def test_a_row_the_venue_rejected_synchronously_is_rejected_with_a_placeholder_reason() -> (
    None
):
    rest, _ = adapter(ok(row("c-4", "rejected")))
    report = await rest.place_order(BUY, product=Product.INTRADAY, client_order_id="c-4")
    assert report.status is OrderStatus.REJECTED
    assert report.reject_reason is not None
    assert "ledger" in report.reject_reason  # an older server: says why there is no real reason


async def test_a_rejected_row_reports_the_ledger_code_and_message() -> None:
    reason = {"code": "order_rejected", "message": "no_position"}
    rest, _ = adapter(ok(row("c-4", "rejected", reject_reason=reason)))
    report = await rest.place_order(BUY, product=Product.INTRADAY, client_order_id="c-4")
    assert report.status is OrderStatus.REJECTED
    assert report.reject_reason == "order_rejected: no_position"


@pytest.mark.parametrize("code", ["unauthorized", "forbidden"])
async def test_auth_failures_are_fatal(code: str) -> None:
    rest, _ = adapter(failure(401 if code == "unauthorized" else 403, code))
    with pytest.raises(AdapterFatalError) as err:
        await rest.place_order(BUY, product=Product.INTRADAY, client_order_id="c-5")
    assert code in str(err.value)


async def test_execution_unavailable_is_retriable_and_not_fatal() -> None:
    rest, script = adapter(failure(503, "order_execution_unavailable", retryable=True), retry=())
    with pytest.raises(RestApiError) as err:
        await rest.place_order(BUY, product=Product.INTRADAY, client_order_id="c-6")
    assert not err.value.fatal
    assert err.value.api_code == "order_execution_unavailable"
    assert err.value.retryable is True
    assert len(script.calls) == 1


async def test_a_timeout_is_retried_when_the_order_has_a_client_id() -> None:
    timeout = TransportApiError("timeout", "slow", retryable=True)
    rest, script = adapter(timeout, ok(row()), retry=(0.0, 0.0))
    report = await rest.place_order(BUY, product=Product.INTRADAY, client_order_id="c-1")
    assert report.status is OrderStatus.SUBMITTED
    assert len(script.calls) == 2


async def test_a_timeout_is_not_retried_without_a_client_id() -> None:
    timeout = TransportApiError("timeout", "slow", retryable=True)
    rest, script = adapter(timeout, ok(row()), retry=(0.0, 0.0))
    with pytest.raises(RestApiError) as err:
        await rest.place_order(BUY, product=Product.INTRADAY)
    assert "outcome" in str(err.value)  # the order may exist
    assert len(script.calls) == 1


async def test_a_rate_limited_answer_is_retried_even_without_a_client_id() -> None:
    limited = failure(429, "rate_limited", retryable=True)
    rest, script = adapter(limited, ok(row("srv-1")), retry=(0.0,))
    report = await rest.place_order(BUY, product=Product.INTRADAY)
    assert report.order_id == "srv-1"
    assert len(script.calls) == 2
    assert "client_order_id" not in script.calls[0][2]


async def test_retries_are_bounded() -> None:
    limited = failure(429, "rate_limited", retryable=True)
    rest, script = adapter(limited, limited, limited, retry=(0.0, 0.0))
    with pytest.raises(RestApiError) as err:
        await rest.place_order(BUY, product=Product.INTRADAY, client_order_id="c-1")
    assert err.value.api_code == "rate_limited"
    assert len(script.calls) == 3


async def test_unsupported_intents_fail_before_any_request() -> None:
    rest, script = adapter()
    stop = OrderIntent(TCS, OrderSide.BUY, 1.0, OrderType.STOP_MARKET, trigger_price=99.0)
    gtc = OrderIntent(TCS, OrderSide.BUY, 1.0, time_in_force=TimeInForce.GTC)
    for intent in (stop, gtc):
        with pytest.raises(CapabilityError):
            await rest.place_order(intent, product=Product.INTRADAY)
    assert script.calls == []


async def test_a_not_connected_adapter_refuses_io() -> None:
    rest, script = adapter()
    rest._connected = False
    with pytest.raises(AdapterError):
        await rest.place_order(BUY, product=Product.INTRADAY)
    assert script.calls == []


def test_the_capability_descriptor_claims_only_what_the_routes_serve() -> None:
    caps = adapter()[0].capabilities()
    assert caps.supports(
        Capability.PLACE_ORDER, Capability.CANCEL_ORDER, Capability.ORDER_BOOK, Capability.POSITIONS
    )
    assert not caps.supports(
        Capability.MODIFY_ORDER, Capability.TRADE_BOOK, Capability.FUNDS, Capability.HOLDINGS
    )
    assert caps.time_in_force == frozenset({TimeInForce.DAY})
    assert caps.order_types == frozenset({OrderType.MARKET, OrderType.LIMIT})


# -- connect ----------------------------------------------------------------------------------


async def test_connect_checks_health_and_the_write_scope() -> None:
    script = Script(ok({"status": "ok"}), ok({"orders": []}))
    rest = RestExecutionAdapter("http://unused", client=Client(script), sleep=no_sleep)
    info = await rest.connect()
    assert rest.is_connected()
    assert info.user_id == "honba-rest"
    assert [(m, p) for m, p, _ in script.calls] == [("GET", "/health"), ("GET", "/orders")]


async def test_connect_with_a_bad_token_is_fatal_and_leaves_it_disconnected() -> None:
    script = Script(ok({"status": "ok"}), failure(401, "unauthorized"))
    rest = RestExecutionAdapter("http://unused", client=Client(script), sleep=no_sleep)
    with pytest.raises(AdapterFatalError):
        await rest.connect()
    assert not rest.is_connected()


async def test_connect_to_an_unreachable_server_is_a_recoverable_session_error() -> None:
    down = TransportApiError("transport_error", "refused", retryable=True)
    rest = RestExecutionAdapter(
        "http://unused", client=Client(Script(down)), retry=(), sleep=no_sleep
    )
    with pytest.raises(SessionError):
        await rest.connect()
    assert not rest.is_connected()


async def test_disconnect_is_safe_to_repeat() -> None:
    rest, _ = adapter()
    await rest.disconnect()
    await rest.disconnect()
    assert not rest.is_connected()


# -- reading the ledger -------------------------------------------------------------------------


async def test_orders_and_order_status_map_ledger_rows() -> None:
    rows = [row("a", "submitted"), row("b", "cancelled"), row("c", "rejected")]
    rest, script = adapter(ok({"orders": rows}), ok({"orders": rows}))
    reports = await rest.orders()
    assert [(r.order_id, r.status) for r in reports] == [
        ("a", OrderStatus.SUBMITTED),
        ("b", OrderStatus.CANCELLED),
        ("c", OrderStatus.REJECTED),
    ]
    assert reports[2].reject_reason is not None
    one = await rest.order_status("b")
    assert one.status is OrderStatus.CANCELLED
    assert [(m, p) for m, p, _ in script.calls] == [("GET", "/orders")] * 2


async def test_order_status_of_an_unknown_id_is_an_adapter_error() -> None:
    rest, _ = adapter(ok({"orders": [row("a")]}))
    with pytest.raises(AdapterError):
        await rest.order_status("nope")


async def test_a_filled_row_reports_the_real_quantity_and_average_price() -> None:
    filled = row("f", "filled", filled_qty=10.0, avg_fill_price=101.25)
    rest, _ = adapter(ok({"orders": [filled]}))  # no fill_price= needed
    [report] = await rest.orders()
    assert (report.status, report.filled_quantity, report.average_price) == (
        OrderStatus.FILLED,
        10.0,
        101.25,
    )


async def test_a_partial_fill_reports_its_real_quantity_and_average_price() -> None:
    partial = row("p", "partially_filled", filled_qty=4.0, avg_fill_price=99.5)
    rest, _ = adapter(ok({"orders": [partial]}))
    [report] = await rest.orders()
    assert (report.status, report.filled_quantity, report.average_price) == (
        OrderStatus.PARTIALLY_FILLED,
        4.0,
        99.5,
    )


async def test_a_cancelled_row_keeps_what_filled_before_the_cancel() -> None:
    cancelled = row("c", "cancelled", filled_qty=3.0, avg_fill_price=100.0)
    rest, _ = adapter(ok({"orders": [cancelled]}))
    [report] = await rest.orders()
    assert (report.status, report.filled_quantity, report.average_price) == (
        OrderStatus.CANCELLED,
        3.0,
        100.0,
    )


async def test_an_older_server_filled_row_has_no_price_so_the_report_says_so() -> None:
    rest, _ = adapter(ok({"orders": [row("f", "filled")]}), ok({"orders": [row("f", "filled")]}))
    with pytest.raises(LedgerLimitationError, match="older server"):
        await rest.order_status("f")
    ledger = await rest.ledger()  # the status-only view still works
    assert [(r.order_id, r.status) for r in ledger] == [("f", "filled")]


async def test_an_older_server_filled_row_reports_with_a_caller_supplied_fill_price() -> None:
    rest, _ = adapter(ok({"orders": [row("f", "filled")]}), fill_price=lambda _row: 101.5)
    [report] = await rest.orders()
    assert (report.status, report.filled_quantity, report.average_price) == (
        OrderStatus.FILLED,
        10.0,
        101.5,
    )


async def test_an_older_server_partial_fill_cannot_be_reported() -> None:
    rest, _ = adapter(ok({"orders": [row("p", "partially_filled")]}), fill_price=lambda _r: 1.0)
    with pytest.raises(LedgerLimitationError, match="older server"):
        await rest.orders()


# -- cancel -------------------------------------------------------------------------------------


async def test_cancel_deletes_a_working_order() -> None:
    rest, script = adapter(ok({"orders": [row("a")]}), ok({"status": "cancelled"}))
    await rest.cancel_order("a")
    assert [(m, p) for m, p, _ in script.calls] == [("GET", "/orders"), ("DELETE", "/orders/a")]


async def test_cancel_of_a_terminal_order_is_reported_not_resent() -> None:
    rest, script = adapter(ok({"orders": [row("a", "filled")]}))
    await rest.cancel_order("a")
    assert [m for m, _, _ in script.calls] == ["GET"]


async def test_cancel_of_an_unknown_id_is_an_adapter_error_and_sends_no_delete() -> None:
    rest, script = adapter(ok({"orders": []}))
    with pytest.raises(AdapterError):
        await rest.cancel_order("nope")
    assert [m for m, _, _ in script.calls] == ["GET"]


async def test_cancel_all_cancels_only_working_orders_of_the_instrument() -> None:
    other = row("o", instrument_id={"symbol": "INFY", "exchange": "NSE"})
    rows = [row("a"), row("b", "filled"), other, row("d", "partially_filled")]
    rest, script = adapter(
        ok({"orders": rows}), ok({"status": "cancelled"}), ok({"status": "cancelled"})
    )
    await rest.cancel_all(instrument_id=TCS)
    deletes = [p for m, p, _ in script.calls if m == "DELETE"]
    assert deletes == ["/orders/a", "/orders/d"]


# -- positions and the unsupported surface ---------------------------------------------------------


def fill(order_id: str, side: str, qty: float, px: float, **over: Any) -> dict[str, Any]:
    return row(
        order_id,
        over.pop("status", "filled"),
        side=side,
        quantity=over.pop("quantity", qty),
        filled_qty=qty,
        avg_fill_price=px,
        **over,
    )


async def test_positions_net_the_fills_per_instrument_with_the_real_average_price() -> None:
    infy = {"symbol": "INFY", "exchange": "NSE"}
    rows = [
        fill("a", "buy", 10.0, 100.0),
        fill("a2", "buy", 10.0, 110.0),
        fill("b", "sell", 4.0, 130.0),
        fill("c", "sell", 5.0, 50.0, instrument_id=infy),
        fill("d", "buy", 5.0, 50.0, instrument_id=infy),
        row("e", "submitted", quantity=99.0),
        row("f", "cancelled", quantity=99.0),
        fill("g", "buy", 2.0, 120.0, status="partially_filled", quantity=8.0),
        fill("h", "buy", 1.0, 120.0, status="cancelled", quantity=8.0),
    ]
    rest, _ = adapter(ok({"orders": rows}))
    [position] = await rest.positions()
    assert position.instrument_id == TCS
    # 20 @ 105 average, a 4-lot sale leaves the average alone, then 3 more @ 120.
    assert position.signed_quantity == 19.0
    assert position.avg_price == pytest.approx((16 * 105.0 + 3 * 120.0) / 19.0, abs=0.01)


async def test_positions_flip_to_a_short_priced_at_the_flipping_fill() -> None:
    rows = [fill("a", "buy", 5.0, 100.0), fill("b", "sell", 8.0, 90.0)]
    rest, _ = adapter(ok({"orders": rows}))
    [position] = await rest.positions()
    assert (position.signed_quantity, position.avg_price) == (-3.0, 90.0)


async def test_older_server_positions_keep_filled_rows_with_an_unknown_average() -> None:
    rows = [row("a", "filled", quantity=10.0), row("b", "filled", side="sell", quantity=4.0)]
    rest, _ = adapter(ok({"orders": rows}))
    [position] = await rest.positions()
    assert (position.signed_quantity, position.avg_price) == (6.0, 0.0)


async def test_older_server_positions_refuse_when_a_partial_fill_makes_the_quantity_unknown() -> (
    None
):
    rest, _ = adapter(ok({"orders": [row("p", "partially_filled")]}))
    with pytest.raises(LedgerLimitationError, match="older server"):
        await rest.positions()


async def test_unsupported_methods_raise_capability_errors_without_a_request() -> None:
    rest, script = adapter()
    for call in (
        rest.trades(),
        rest.holdings(),
        rest.funds(),
        rest.margin(),
        rest.modify_order("a", quantity=1.0),
    ):
        with pytest.raises(CapabilityError):
            await call
    assert script.calls == []


def test_the_adapter_satisfies_the_execution_protocol() -> None:
    from honba.adapters.base import ExecutionClient

    assert isinstance(adapter()[0], ExecutionClient)
