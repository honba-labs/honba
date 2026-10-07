"""Integration: MCP tool calls through the gateway equal the REST responses (E5-S1).

Uses the same Parquet fixtures as ``test_client_parity.py`` and the in-process transport,
so the request the gateway builds is the request the ``Client`` would send.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from honba.ai.mcp.handlers import McpError, McpGateway
from honba.client import Client

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
    directory = tmp_path_factory.mktemp("mcp-parity")
    _write_bars(directory / "TCS.NSE.parquet", 5, 100.0)
    _write_bars(directory / "INFY.NSE.parquet", 2, 50.0)
    return directory


@pytest.fixture(scope="module")
def client(data_dir: Path) -> Iterator[Client]:
    pytest.importorskip("honba._honba")
    with Client.inproc(data_dir) as inproc:
        yield inproc


def test_mcp_call_matches_rest_response(client: Client) -> None:
    gateway = McpGateway(client)

    instruments = gateway.call_tool("get_instruments", {"query": {"exchange": "NSE"}})
    assert instruments.data == client.request_data("GET", "/instruments", query={"exchange": "NSE"})
    assert [i.id.symbol for i in client.instruments(exchange="NSE")] == ["INFY", "TCS"]

    bars = gateway.call_tool("get_bars", {"id": "TCS.NSE", "query": {"tf": "1m"}})
    assert bars.data == client.request_data("GET", "/bars/TCS.NSE", query={"tf": "1m"})
    assert len(bars.data["bars"]) == len(client.bars("TCS.NSE", tf="1m"))

    scan = gateway.call_tool("screen", {"query": {"universe": '["TCS.NSE"]', "tf": "1m"}})
    assert scan.data == client.request_data(
        "GET", "/screener/scan", query={"universe": '["TCS.NSE"]', "tf": "1m"}
    )

    strategies = gateway.call_tool("list_strategies", {})
    assert strategies.data == client.request_data("GET", "/strategies")


def test_mcp_error_carries_the_rest_code(client: Client) -> None:
    gateway = McpGateway(client)

    with pytest.raises(McpError) as err:
        gateway.call_tool("get_bars", {"id": "NOPE.NSE", "query": {}})

    assert err.value.code == "instrument_not_found"
    with pytest.raises(Exception) as rest_err:
        client.request_data("GET", "/bars/NOPE.NSE")
    assert getattr(rest_err.value, "code", None) == err.value.code


def test_mcp_result_marks_broker_text_untrusted(client: Client) -> None:
    gateway = McpGateway(client)

    result = gateway.call_tool("get_instruments", {"query": {"symbol": "TCS"}})

    wrapped = json.loads(result.content)["instruments"][0]["id"]["symbol"]
    assert wrapped["untrusted"] is True
    assert wrapped["content"] == "TCS"
    # The raw payload is unchanged, so the result still equals the REST response.
    assert result.data["instruments"][0]["id"]["symbol"] == "TCS"
