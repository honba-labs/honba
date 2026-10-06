"""``HttpTransport`` (httpx mock) and ``InprocTransport`` (fake native) in isolation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from honba.client import (
    ApiError,
    HttpTransport,
    InprocTransport,
    Response,
    RetryPolicy,
    TransportApiError,
)


def make(handler: Any, **kw: Any) -> HttpTransport:
    return HttpTransport(
        "http://honba.test/", transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw
    )


def test_get_builds_url_query_and_returns_status_and_json() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": 1})

    got = make(handler).request("GET", "/bars/TCS.NSE", query={"tf": "1m", "from": "2024-01-01"})
    assert got == Response(200, {"data": 1})
    [req] = seen
    assert req.method == "GET"
    assert req.url.path == "/bars/TCS.NSE"
    assert dict(req.url.params) == {"tf": "1m", "from": "2024-01-01"}


def test_an_encoded_path_is_sent_as_given() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    make(handler).request("GET", "/instruments/M%26M.NSE")
    assert seen[0].url.raw_path == b"/instruments/M%26M.NSE"


def test_post_sends_a_json_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(422, json={"error": {"code": "x"}})

    got = make(handler).request("POST", "/strategies/verify", body={"name": "s"})
    assert got.status == 422
    assert seen[0].content == b'{"name":"s"}'
    assert json.loads(seen[0].content) == {"name": "s"}
    assert seen[0].headers["content-type"] == "application/json"


def test_non_json_bodies_yield_none() -> None:
    got = make(lambda r: httpx.Response(404, text="")).request("GET", "/nope")
    assert got == Response(404, None)
    got = make(lambda r: httpx.Response(502, text="<html>")).request("GET", "/x")
    assert got == Response(502, None)


def test_connection_failures_are_transport_errors_and_not_retried_by_default() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("refused")

    with pytest.raises(TransportApiError) as err:
        make(handler).request("GET", "/health")
    assert (err.value.code, err.value.retryable, err.value.status) == (
        "transport_error",
        True,
        None,
    )
    assert len(calls) == 1


def test_timeouts_use_the_timeout_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ApiError) as err:
        make(handler).request("GET", "/health")
    assert err.value.code == "timeout"


def retryable_then_ok(fail_times: int, calls: list[int]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) <= fail_times:
            error = {"code": "timeout", "message": "m", "retryable": True}
            return httpx.Response(504, json={"error": error})
        return httpx.Response(200, json={"data": "ok"})

    return handler


def test_retryable_envelopes_are_retried_within_the_policy() -> None:
    calls: list[int] = []
    sleeps: list[float] = []
    transport = HttpTransport(
        "http://h",
        transport=httpx.MockTransport(retryable_then_ok(2, calls)),
        retry=RetryPolicy(delays=(0.1, 0.2)),
        sleep=sleeps.append,
    )
    assert transport.request("GET", "/health").status == 200
    assert len(calls) == 3
    assert sleeps == [0.1, 0.2]


def test_the_policy_is_bounded_and_returns_the_last_response() -> None:
    calls: list[int] = []
    transport = HttpTransport(
        "http://h",
        transport=httpx.MockTransport(retryable_then_ok(99, calls)),
        retry=RetryPolicy(delays=(0.0,)),
        sleep=lambda s: None,
    )
    assert transport.request("GET", "/health").status == 504
    assert len(calls) == 2


def test_retries_are_off_by_default() -> None:
    calls: list[int] = []
    transport = make(retryable_then_ok(5, calls))
    assert transport.request("GET", "/health").status == 504
    assert len(calls) == 1


def test_non_retryable_errors_are_never_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        error = {"code": "validation_invalid_request", "message": "m", "retryable": False}
        return httpx.Response(422, json={"error": error})

    transport = make(handler, retry=RetryPolicy(delays=(0.0, 0.0, 0.0)))
    assert transport.request("GET", "/x").status == 422
    assert len(calls) == 1


def test_connection_failures_are_retried_under_a_policy_then_raised() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("refused")

    transport = make(handler, retry=RetryPolicy(delays=(0.0, 0.0)))
    with pytest.raises(TransportApiError):
        transport.request("GET", "/health")
    assert len(calls) == 3


def test_retry_policy_validation() -> None:
    assert RetryPolicy.none().delays == ()
    assert RetryPolicy.fixed(3, 0.5).delays == (0.5, 0.5, 0.5)
    for bad in ((-1.0,), (float("nan"),)):
        with pytest.raises(ValueError):
            RetryPolicy(delays=bad)
    with pytest.raises(ValueError):
        RetryPolicy.fixed(11, 0.1)  # bounded: at most 10 retries


def test_http_transport_rejects_bad_construction() -> None:
    for url in ("", "honba.test", "ftp://x"):
        with pytest.raises(ValueError):
            HttpTransport(url)
    with pytest.raises(ValueError):
        HttpTransport("http://h", timeout=0)


class FakeNative:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any) -> tuple[int, str]:
        self.calls.append(args)
        return 200, '{"data": {"status": "ok"}}'


def test_inproc_passes_json_strings_to_the_native_function(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeNative()
    monkeypatch.setattr("honba.client.transport.native_attr", lambda name: fake)
    transport = InprocTransport(tmp_path)
    got = transport.request("GET", "/health")
    assert got == Response(200, {"data": {"status": "ok"}})
    transport.request("POST", "/strategies/verify", query={"a": "b"}, body={"n": 1})
    assert fake.calls[0] == (str(tmp_path), "GET", "/health", None, None)
    assert fake.calls[1] == (
        str(tmp_path),
        "POST",
        "/strategies/verify",
        '{"a":"b"}',
        '{"n":1}',
    )


def test_inproc_empty_body_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("honba.client.transport.native_attr", lambda name: lambda *a: (404, ""))
    assert InprocTransport(tmp_path).request("GET", "/x") == Response(404, None)


def test_inproc_requires_an_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        InprocTransport(tmp_path / "missing")
    (tmp_path / "f").write_text("x")
    with pytest.raises(NotADirectoryError):
        InprocTransport(tmp_path / "f")


@pytest.mark.parametrize("exc", [OSError("data dir gone"), ValueError("bad utf8")])
def test_inproc_native_failures_become_non_retryable_transport_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    def boom(*args: Any) -> tuple[int, str]:
        raise exc

    monkeypatch.setattr("honba.client.transport.native_attr", lambda name: boom)
    with pytest.raises(TransportApiError) as err:
        InprocTransport(tmp_path).request("GET", "/health")
    assert err.value.code == "transport_error"
    assert err.value.retryable is False
    assert err.value.__cause__ is exc
