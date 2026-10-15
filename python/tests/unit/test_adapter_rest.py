"""``RestExecutionAdapter`` over a scripted ``Transport``: request building, error mapping,
retry rules (ADR 0030, E3-S7). The real-server proof is
``tests/integration/test_rest_execution_adapter.py``."""

from __future__ import annotations

from typing import Any

import pytest

from honba.adapters.capabilities import Capability
from honba.adapters.errors import AdapterError, AdapterFatalError, CapabilityError
from honba.adapters.models import OrderStatus, Product
from honba.adapters.rest import RestApiError, RestExecutionAdapter
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
    assert "ledger" in report.reject_reason  # says why there is no real reason


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
