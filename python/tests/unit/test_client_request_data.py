"""``Client.request_data``: the raw passthrough the MCP gateway routes tool calls through."""

from __future__ import annotations

from typing import Any

import pytest

from honba.client import Client, Response, ValidationApiError

try:
    from honba.wire.wire import SCHEMA_VERSION as SCHEMA
except ImportError:  # no native extension: the version check is skipped
    SCHEMA = 1


def ok(data: Any) -> Response:
    return Response(200, {"api_version": "1.0.0", "schema_version": SCHEMA, "data": data})


def failure(status: int, code: str) -> Response:
    return Response(
        status,
        {
            "api_version": "1.0.0",
            "schema_version": SCHEMA,
            "error": {"code": code, "message": "m", "retryable": False},
        },
    )


class FakeTransport:
    def __init__(self, response: Response) -> None:
        self.response = response
        self.calls: list[tuple[str, str, Any, Any]] = []

    def request(self, method, path, *, query=None, body=None):
        self.calls.append((method, path, query, body))
        return self.response


def test_request_data_sends_the_raw_request_and_returns_the_data_member() -> None:
    fake = FakeTransport(ok({"instruments": []}))
    got = Client(fake).request_data("GET", "/instruments", query={"exchange": "NSE"})
    assert got == {"instruments": []}
    assert fake.calls == [("GET", "/instruments", {"exchange": "NSE"}, None)]


def test_request_data_maps_the_error_envelope() -> None:
    fake = FakeTransport(failure(422, "validation_invalid_request"))
    with pytest.raises(ValidationApiError) as err:
        Client(fake).request_data("GET", "/bars/TCS.NSE", query={"tf": "banana"})
    assert err.value.code == "validation_invalid_request"
    assert fake.calls == [("GET", "/bars/TCS.NSE", {"tf": "banana"}, None)]
