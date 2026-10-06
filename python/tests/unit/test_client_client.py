"""``Client`` over a fake ``Transport``: request building, parsing, error mapping."""

from __future__ import annotations

from typing import Any

import pytest

from honba.client import (
    ApiError,
    Client,
    InvalidResponseError,
    NotFoundApiError,
    NotImplementedApiError,
    RequestValidationError,
    Response,
    ValidationApiError,
)
from honba.wire.wire import Bar, QuoteTick, UnixNanos

try:
    from honba.wire.wire import SCHEMA_VERSION as SCHEMA
except ImportError:  # no native extension: the client then skips the version check
    SCHEMA = 1

T0 = 1_704_067_200_000_000_000
STAMP = {"iso": "2024-01-01T00:00:00.000000000Z", "unix_nanos": str(T0)}
TCS = {"symbol": "TCS", "exchange": "NSE"}


def ok(data: Any) -> Response:
    return Response(200, {"api_version": "1.0.0", "schema_version": SCHEMA, "data": data})


def failure(status: int, code: str, **extra: Any) -> Response:
    error = {"code": code, "message": "m", "retryable": False, **extra}
    return Response(status, {"api_version": "1.0.0", "schema_version": SCHEMA, "error": error})


class FakeTransport:
    def __init__(self, *responses: Response) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any] | None, Any]] = []
        self.closed = False

    def request(
        self, method: str, path: str, *, query: dict[str, Any] | None = None, body: Any = None
    ) -> Response:
        self.calls.append((method, path, query, body))
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def bar(close: float = 11.0, ts: dict[str, str] = STAMP) -> dict[str, Any]:
    return {
        "bar_type": {
            "instrument_id": TCS,
            "spec": {"step": 1, "aggregation": "minute", "price_type": "last"},
        },
        "open": 10.0,
        "high": 12.0,
        "low": 9.0,
        "close": close,
        "volume": 100.0,
        "ts_event": ts,
        "ts_init": ts,
        "a_future_field": 1,
    }


def test_health() -> None:
    fake = FakeTransport(ok({"status": "ok"}))
    assert Client(fake).health().status == "ok"
    assert fake.calls == [("GET", "/health", None, None)]


def test_instruments_parse_into_typed_records_and_ignore_unknown_fields() -> None:
    row = {
        "id": TCS,
        "kind": "equity",
        "currency": "INR",
        "lot_size": 1,
        "tick_size": 0.05,
        "added_later": True,
    }
    fake = FakeTransport(ok({"instruments": [row]}))
    got = Client(fake).instruments(exchange="NSE")
    assert fake.calls == [("GET", "/instruments", {"exchange": "NSE"}, None)]
    [inst] = got
    assert (inst.id.symbol, inst.id.exchange, inst.kind) == ("TCS", "NSE", "equity")
    assert inst.currency.value == "INR"
    assert (inst.lot_size, inst.tick_size) == (1, 0.05)


def test_instrument_by_id() -> None:
    row = {"id": TCS, "kind": "equity", "currency": "INR", "lot_size": 1, "tick_size": 0.05}
    fake = FakeTransport(ok(row))
    assert Client(fake).instrument("TCS.NSE").id.symbol == "TCS"
    assert fake.calls[0][:2] == ("GET", "/instruments/TCS.NSE")


def test_bars_are_wire_bars_with_unix_nanos_timestamps() -> None:
    fake = FakeTransport(ok({"bars": [bar()]}))
    [one] = Client(fake).bars("TCS.NSE", tf="1m", from_="2024-01-01", to="2024-01-02")
    assert isinstance(one, Bar)
    assert isinstance(one.ts_event, UnixNanos)
    assert one.ts_event.to_ns() == T0
    assert one.close == 11.0
    assert fake.calls == [
        ("GET", "/bars/TCS.NSE", {"tf": "1m", "from": "2024-01-01", "to": "2024-01-02"}, None)
    ]


def test_quotes_are_wire_quote_ticks() -> None:
    quote = {
        "instrument_id": TCS,
        "bid_price": 11.0,
        "ask_price": 11.0,
        "bid_size": 0.0,
        "ask_size": 0.0,
        "ts_event": STAMP,
        "ts_init": STAMP,
    }
    fake = FakeTransport(ok({"quotes": [quote]}))
    [one] = Client(fake).quotes(["TCS"], venue="NSE", as_of=T0_STAMP())
    assert isinstance(one, QuoteTick)
    assert one.bid_price == 11.0
    assert fake.calls[0][2] == {"symbols": "TCS", "venue": "NSE", "as_of": STAMP["iso"]}


def T0_STAMP() -> UnixNanos:
    return UnixNanos.from_ns(T0)


def test_depth() -> None:
    fake = FakeTransport(ok({"bids": [{"price": 10.0, "qty": 5.0}], "asks": []}))
    book = Client(fake).depth("TCS.NSE", levels=3)
    assert book.bids[0].price == 10.0 and book.bids[0].qty == 5.0
    assert book.asks == ()
    assert fake.calls[0] == ("GET", "/depth/TCS.NSE", {"depth": 3}, None)


def test_verify_strategy_returns_the_ir_record() -> None:
    fake = FakeTransport(ok({"warmup_bars": 20, "manifest": {"name": "x"}}))
    ir = Client(fake).verify_strategy({"name": "x"})
    assert ir["warmup_bars"] == 20
    assert fake.calls[0] == ("POST", "/strategies/verify", None, {"name": "x"})


IR = {"warmup_bars": 20, "manifest": {"name": "x"}}


def test_compile_strategy_returns_a_typed_compiled_strategy() -> None:
    fake = FakeTransport(ok({"id": "sha256:ab", "ir": IR, "added": 1}))
    got = Client(fake).compile_strategy({"name": "x"})
    assert (got.id, got.ir) == ("sha256:ab", IR)
    assert fake.calls[0] == ("POST", "/strategies", None, {"manifest": {"name": "x"}})


def test_strategies_lists_compiled_strategies_in_server_order() -> None:
    rows = [{"id": "sha256:a", "ir": IR}, {"id": "sha256:b", "ir": IR}]
    fake = FakeTransport(ok({"strategies": rows}), ok({"strategies": []}))
    client = Client(fake)
    assert [s.id for s in client.strategies()] == ["sha256:a", "sha256:b"]
    assert client.strategies() == []
    assert fake.calls[0] == ("GET", "/strategies", None, None)


def test_strategies_rejects_a_payload_that_does_not_parse() -> None:
    with pytest.raises(InvalidResponseError):
        Client(FakeTransport(ok({"strategies": [{"id": 1}]}))).strategies()
    with pytest.raises(InvalidResponseError):
        Client(FakeTransport(ok({"nope": []}))).strategies()


def test_compile_strategy_maps_the_validation_error_with_its_reason() -> None:
    fake = FakeTransport(
        failure(422, "validation_invalid_request", context={"reason": "source_unsupported"})
    )
    with pytest.raises(ValidationApiError) as err:
        Client(fake).compile_strategy({"name": "x"})
    assert err.value.context == {"reason": "source_unsupported"}


def test_bad_input_is_rejected_before_the_transport_is_called() -> None:
    fake = FakeTransport()
    client = Client(fake)
    with pytest.raises(RequestValidationError):
        client.bars("TCS.NSE", tf="banana")
    with pytest.raises(RequestValidationError):
        client.depth("TCS.NSE", levels=0)
    with pytest.raises(RequestValidationError):
        client.quotes([])
    assert fake.calls == []


def test_error_envelopes_raise_typed_errors() -> None:
    fake = FakeTransport(
        failure(404, "instrument_not_found"),
        failure(422, "validation_invalid_request", context={"field": "tf"}),
        failure(501, "not_implemented"),
    )
    client = Client(fake)
    with pytest.raises(NotFoundApiError) as e1:
        client.instrument("NOPE.NSE")
    assert e1.value.status == 404
    with pytest.raises(ValidationApiError) as e2:
        client.health()
    assert e2.value.context == {"field": "tf"}
    with pytest.raises(NotImplementedApiError):
        client.health()


def test_a_success_status_with_an_error_envelope_is_still_an_error() -> None:
    bad = Response(200, {"api_version": "1", "schema_version": SCHEMA, "error": None, "data": None})
    with pytest.raises(InvalidResponseError):
        Client(FakeTransport(bad)).health()
    odd = Response(200, {"api_version": "1", "schema_version": SCHEMA, "data": {}})
    with pytest.raises(InvalidResponseError):
        Client(FakeTransport(odd)).depth("T.N")


@pytest.mark.parametrize("payload", [None, "html", {"data": 1}, [1]])
def test_a_non_envelope_success_body_is_an_invalid_response(payload: object) -> None:
    with pytest.raises(InvalidResponseError) as err:
        Client(FakeTransport(Response(200, payload))).health()
    assert err.value.status == 200


def test_a_malformed_payload_is_an_invalid_response_not_a_validation_leak() -> None:
    fake = FakeTransport(ok({"bars": [{"open": 1}]}))
    with pytest.raises(InvalidResponseError) as err:
        Client(fake).bars("TCS.NSE")
    assert isinstance(err.value, ApiError)
    assert err.value.__cause__ is not None


def test_a_missing_payload_key_is_an_invalid_response() -> None:
    with pytest.raises(InvalidResponseError):
        Client(FakeTransport(ok({"nope": []}))).bars("TCS.NSE")


def test_close_and_context_manager_close_the_transport() -> None:
    fake = FakeTransport()
    with Client(fake):
        pass
    assert fake.closed


def test_a_transport_without_close_is_fine() -> None:
    class Bare:
        def request(self, method: str, path: str, **kw: Any) -> Response:
            return ok({"status": "ok"})

    with Client(Bare()) as client:
        assert client.health().status == "ok"


def test_an_envelope_of_another_schema_version_is_rejected() -> None:
    pytest.importorskip("honba._honba")
    other = Response(
        200, {"api_version": "1", "schema_version": SCHEMA + 1, "data": {"status": "x"}}
    )
    with pytest.raises(InvalidResponseError, match="schema_version"):
        Client(FakeTransport(other)).health()
