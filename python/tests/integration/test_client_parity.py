"""``Client`` over both transports against the same Parquet data: identical outputs.

``InprocTransport`` drives the Rust router in process (needs the native extension);
``HttpTransport`` talks to a real ``honba serve`` child process bound to port 0 (skipped when
the binary is not built: ``cargo build -p honba-cli``, or point ``HONBA_BIN`` at it). Both run
the same scenarios, success and 404/422/501 envelopes alike, and must give equal results.
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from honba.client import (
    ApiError,
    Client,
    InvalidResponseError,
    MarketDataUnavailableApiError,
    NotFoundApiError,
    NotImplementedApiError,
    RequestValidationError,
    Response,
    ValidationApiError,
)
from honba.wire.wire import Bar, QuoteTick, UnixNanos

MINUTE = 60_000_000_000
T0 = 1_704_067_200_000_000_000  # 2024-01-01T00:00:00Z


def _write_bars(path: Path, count: int, base: float) -> None:
    rows = range(count)
    table = pa.table(
        {
            "ts": pa.array([T0 + i * MINUTE for i in rows], pa.int64()),
            "open": [base + i for i in rows],
            "high": [base + i + 2 for i in rows],
            "low": [base + i - 1 for i in rows],
            "close": [base + i + 1 for i in rows],
            "volume": [1000.0 + i for i in rows],
        }
    )
    pq.write_table(table, path)


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("client-parity")
    _write_bars(directory / "TCS.NSE.parquet", 5, 100.0)
    _write_bars(directory / "INFY.NSE.parquet", 2, 50.0)
    _write_bars(directory / "TCS.BSE.parquet", 1, 101.0)
    _write_bars(directory / "M&M.NSE.parquet", 2, 20.0)
    return directory


def _binary() -> Path | None:
    candidates = [os.environ.get("HONBA_BIN")]
    repo = Path(__file__).resolve().parents[3]
    for root in (os.environ.get("CARGO_TARGET_DIR"), str(repo / "target")):
        if root:
            candidates += [f"{root}/debug/honba", f"{root}/release/honba"]
    return next((Path(c) for c in candidates if c and Path(c).is_file()), None)


@pytest.fixture(scope="module")
def inproc(data_dir: Path) -> Iterator[Client]:
    pytest.importorskip("honba._honba")
    with Client.inproc(data_dir) as client:
        yield client


@pytest.fixture(scope="module")
def http(data_dir: Path) -> Iterator[Client]:
    binary = _binary()
    if binary is None:
        pytest.skip("honba binary not built (cargo build -p honba-cli, or set HONBA_BIN)")
    child = subprocess.Popen(
        [str(binary), "serve", "--data-dir", str(data_dir), "--addr", "127.0.0.1:0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        banner: queue.Queue[str] = queue.Queue()
        assert child.stdout is not None
        threading.Thread(target=lambda: banner.put(child.stdout.readline()), daemon=True).start()
        try:
            line = banner.get(timeout=30)
        except queue.Empty:
            pytest.fail("honba serve printed no banner within 30s")
        prefix = "listening on "
        if not line.startswith(prefix):
            pytest.skip(f"not a `honba serve` binary: {line!r}")
        with Client.http(line.strip()[len(prefix) :], timeout=10) as client:
            yield client
    finally:
        child.send_signal(signal.SIGINT)
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
        for stream in (child.stdout, child.stderr):
            if stream:
                stream.close()


def outcome(call: Callable[[Client], Any]) -> Callable[[Client], Any]:
    """Wrap a scenario so an ApiError becomes a comparable value."""

    def run(client: Client) -> Any:
        try:
            return call(client)
        except ApiError as err:
            return (
                type(err).__name__,
                err.status,
                err.code,
                err.category,
                err.retryable,
                err.message,
                err.context,
            )

    return run


SCENARIOS: dict[str, Callable[[Client], Any]] = {
    "health": lambda c: c.health(),
    "instruments": lambda c: c.instruments(),
    "instruments_by_exchange": lambda c: c.instruments(exchange="NSE"),
    "instruments_by_symbol": lambda c: c.instruments(symbol="TCS"),
    "instruments_no_match": lambda c: c.instruments(symbol="NOPE"),
    "instrument": lambda c: c.instrument("TCS.NSE"),
    "instrument_with_special_symbol": lambda c: c.instrument("M&M.NSE"),
    "bars_all": lambda c: c.bars("TCS.NSE"),
    "bars_range": lambda c: c.bars(
        "TCS.NSE", from_=UnixNanos.from_ns(T0 + MINUTE), to="2024-01-01T00:03:00Z"
    ),
    "bars_special_symbol": lambda c: c.bars("M&M.NSE", tf="1m"),
    "bars_date_range_outside": lambda c: c.bars("TCS.NSE", from_="2025-01-01"),
    "bars_other_timeframe": lambda c: c.bars("TCS.NSE", tf="5m"),
    "quotes_latest": lambda c: c.quotes(["TCS", "INFY"]),
    "quotes_venue": lambda c: c.quotes("TCS", venue="BSE"),
    "quotes_as_of": lambda c: c.quotes("TCS", venue="NSE", as_of="2024-01-01T00:01:00Z"),
    "quotes_before_data": lambda c: c.quotes("TCS", as_of="2023-01-01"),
    "quotes_unknown_symbol": lambda c: c.quotes("NOPE"),
    "depth_unavailable": lambda c: c.depth("TCS.NSE", levels=5),
    "depth_unknown_instrument": lambda c: c.depth("NOPE.NSE"),
    "instrument_unknown": lambda c: c.instrument("NOPE.NSE"),
    "bars_unknown_instrument": lambda c: c.bars("NOPE.NSE"),
    "verify_unverifiable": lambda c: c.verify_strategy({"name": "x"}),
    "verify_ok": lambda c: c.verify_strategy(MANIFEST),
}

MANIFEST = {
    "api_version": "1.0.0",
    "name": "sma",
    "source_hash": "sha256:abc",
    "universe": {"explicit": [{"symbol": "TCS", "exchange": "NSE"}]},
    "subscriptions": {"instruments": [{"symbol": "TCS", "exchange": "NSE"}]},
    "driving_timeframe": {"interval": 1, "aggregation": "day"},
    "warmup_bars": 20,
}


# --- in-process behaviour (no server needed) ---------------------------------------------


def test_inproc_instruments_are_typed_and_in_id_order(inproc: Client) -> None:
    got = inproc.instruments()
    assert [f"{i.id.symbol}.{i.id.exchange}" for i in got] == [
        "INFY.NSE",
        "M&M.NSE",
        "TCS.BSE",
        "TCS.NSE",
    ]
    assert got[0].kind == "equity" and got[0].currency.value == "INR"
    assert inproc.instrument("TCS.NSE") == got[3]


def test_inproc_bars_are_typed_with_unix_nanos_and_half_open_ranges(inproc: Client) -> None:
    bars = inproc.bars("TCS.NSE")
    assert all(isinstance(b, Bar) for b in bars)
    assert [b.ts_event.to_ns() for b in bars] == [T0 + i * MINUTE for i in range(5)]
    assert bars[0].open == 100.0 and bars[0].close == 101.0
    sliced = inproc.bars("TCS.NSE", from_=T0_ns(1), to="2024-01-01T00:03:00Z")
    assert [b.ts_event.to_ns() for b in sliced] == [T0 + MINUTE, T0 + 2 * MINUTE]
    assert inproc.bars("M&M.NSE")[0].bar_type.instrument_id.symbol == "M&M"


def T0_ns(minutes: int) -> UnixNanos:
    return UnixNanos.from_ns(T0 + minutes * MINUTE)


def test_inproc_quotes_are_derived_from_the_last_bar(inproc: Client) -> None:
    [quote] = inproc.quotes("TCS", venue="NSE")
    assert isinstance(quote, QuoteTick)
    assert quote.bid_price == quote.ask_price == 105.0  # close of the 5th bar
    assert (quote.bid_size, quote.ask_size) == (0.0, 0.0)
    assert quote.ts_event.to_ns() == T0 + 4 * MINUTE
    [early] = inproc.quotes("TCS", venue="NSE", as_of=T0_ns(1))
    assert early.bid_price == 102.0
    assert len(inproc.quotes("TCS")) == 2  # NSE and BSE


@pytest.mark.parametrize(
    ("call", "cls", "status", "code"),
    [
        (lambda c: c.instrument("NOPE.NSE"), NotFoundApiError, 404, "instrument_not_found"),
        (lambda c: c.quotes("NOPE"), NotFoundApiError, 404, "instrument_not_found"),
        (
            lambda c: c.quotes("TCS", as_of="2023-01-01"),
            MarketDataUnavailableApiError,
            404,
            "market_data_unavailable",
        ),
        (
            lambda c: c.depth("TCS.NSE"),
            MarketDataUnavailableApiError,
            404,
            "market_data_unavailable",
        ),
        (
            lambda c: c.bars("TCS.NSE", tf="5m"),
            MarketDataUnavailableApiError,
            404,
            "market_data_unavailable",
        ),
        (
            lambda c: c.verify_strategy({"name": "x"}),
            ValidationApiError,
            422,
            "validation_invalid_request",
        ),
    ],
)
def test_inproc_error_envelopes_are_typed(
    inproc: Client, call: Callable[[Client], Any], cls: type[ApiError], status: int, code: str
) -> None:
    with pytest.raises(cls) as err:
        call(inproc)
    assert (err.value.status, err.value.code) == (status, code)
    assert err.value.retryable is False


def test_inproc_verify_returns_the_ir(inproc: Client) -> None:
    ir = inproc.verify_strategy(MANIFEST)
    assert ir["warmup_bars"] == 20 and ir["manifest"]["name"] == "sma"


def test_inproc_server_side_422_names_the_field_when_the_client_check_is_bypassed(
    inproc: Client,
) -> None:
    resp = inproc.transport.request("GET", "/bars/TCS.NSE", query={"tf": "banana"})
    assert resp.status == 422
    assert resp.json["error"]["context"]["field"] == "tf"
    with pytest.raises(RequestValidationError):  # the client catches it first
        inproc.bars("TCS.NSE", tf="banana")


def test_inproc_501_placeholders(inproc: Client) -> None:
    for method, path in [("GET", "/orders"), ("GET", "/screener/scan"), ("GET", "/strategies")]:
        resp = inproc.transport.request(method, path)
        assert resp.status == 501
        assert resp.json["error"]["code"] == "not_implemented"
    from honba.client.errors import error_from_envelope

    resp = inproc.transport.request("POST", "/backtests", body={})
    err = error_from_envelope(resp.status, resp.json)
    assert isinstance(err, NotImplementedApiError) and err.category == "unsupported"
    assert err.retryable is False


def test_inproc_unknown_route_has_no_envelope(inproc: Client) -> None:
    resp = inproc.transport.request("GET", "/no/such/route")
    assert resp == Response(404, None)
    from honba.client.errors import error_from_envelope

    assert isinstance(error_from_envelope(resp.status, resp.json), InvalidResponseError)


def test_inproc_unreadable_data_dir_is_an_os_error(tmp_path: Path) -> None:
    (tmp_path / "not-an-instrument.parquet").write_bytes(b"junk")
    with pytest.raises(OSError, match="SYMBOL.EXCHANGE"):
        Client.inproc(tmp_path).health()


# --- parity ------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_both_transports_give_identical_results(name: str, inproc: Client, http: Client) -> None:
    run = outcome(SCENARIOS[name])
    assert run(inproc) == run(http)


def test_scenarios_cover_success_and_every_served_failure_kind(inproc: Client) -> None:
    results = {name: outcome(call)(inproc) for name, call in SCENARIOS.items()}
    errors = {r[2] for r in results.values() if isinstance(r, tuple)}
    assert {
        "instrument_not_found",
        "market_data_unavailable",
        "validation_invalid_request",
    } <= errors
    assert any(not isinstance(r, tuple) for r in results.values())


RAW_REQUESTS: list[tuple[str, str, dict[str, Any] | None, Any]] = [
    ("GET", "/health", None, None),
    ("GET", "/capabilities", None, None),
    ("GET", "/instruments", {"exchange": "NSE"}, None),
    ("GET", "/instruments", {"bogus": "1"}, None),  # 422: unknown query key
    ("GET", "/instruments/TCS", None, None),  # 422: malformed id
    ("GET", "/instruments/NOPE.NSE", None, None),  # 404
    ("GET", "/bars/TCS.NSE", {"tf": "banana"}, None),  # 422
    ("GET", "/bars/TCS.NSE", {"from": "2024-01-02", "to": "2024-01-01"}, None),  # 422
    ("GET", "/quotes", None, None),  # 422: no symbols
    ("GET", "/quotes", {"symbols": "TCS", "as_of": "never"}, None),  # 422
    ("GET", "/depth/TCS.NSE", {"depth": 51}, None),  # 422
    ("POST", "/strategies/verify", None, {"unknown": 1}),  # 422
    ("POST", "/strategies/verify", None, MANIFEST),
    ("GET", "/strategies", None, None),  # 501
    ("POST", "/strategies", None, {"name": "x"}),  # 501
    ("POST", "/backtests", None, {}),  # 501
    ("GET", "/backtests/abc", None, None),  # 501
    ("POST", "/sweeps", None, {}),  # 501
    ("GET", "/orders", None, None),  # 501
    ("POST", "/orders", None, {}),  # 501
    ("DELETE", "/orders/abc", None, None),  # 501
    ("POST", "/positions/close", None, None),  # 501
    ("GET", "/screener/scan", None, None),  # 501
    ("GET", "/journals/abc", None, None),  # 501
    ("GET", "/no/such/route", None, None),  # 404, no body
]


@pytest.mark.parametrize(
    ("method", "path", "query", "body"),
    RAW_REQUESTS,
    ids=[f"{m} {p} {q or ''}" for m, p, q, _ in RAW_REQUESTS],
)
def test_raw_responses_are_identical_across_transports(
    method: str, path: str, query: dict[str, Any] | None, body: Any, inproc: Client, http: Client
) -> None:
    mine = inproc.transport.request(method, path, query=query, body=body)
    theirs = http.transport.request(method, path, query=query, body=body)
    assert mine == theirs
    assert mine.status in (200, 404, 422, 501)


def test_raw_501_and_422_envelopes_carry_the_documented_codes(inproc: Client, http: Client) -> None:
    for client in (inproc, http):
        assert client.transport.request("GET", "/orders").json["error"]["code"] == "not_implemented"
        got = client.transport.request("GET", "/depth/TCS.NSE", query={"depth": 51})
        assert got.status == 422
        assert got.json["error"]["code"] == "validation_invalid_request"
        assert got.json["error"]["context"]["field"] == "depth"
