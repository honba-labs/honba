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


@pytest.fixture(scope="module")
def run_client(data_dir: Path) -> Iterator[Client]:
    """A client with a journals root, so the run read tools (ADR 0017 decision 9) answer."""
    pytest.importorskip("honba._honba")
    journals = data_dir.parent / "mcp-parity-journals"
    with Client.inproc(data_dir, journals_dir=journals) as inproc:
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


def test_mcp_run_reads_match_rest(run_client: Client) -> None:
    """ADR 0017 decision 9: an agent polls and reads a run through the gateway."""
    gateway = McpGateway(run_client)
    submitted = run_client.submit_backtest(
        strategy="sma_crossover", universe="TCS.NSE", start="2024-01-01", end="2024-01-02",
        seed=1, bar_spec="1m", initial_capital=1_000_000.0,
    )  # fmt: skip
    run_id = submitted.run_id
    run_client.wait(run_id, timeout=60)

    status = gateway.call_tool("get_backtest", {"id": run_id})
    assert status.data == run_client.request_data("GET", f"/backtests/{run_id}")
    assert status.data["status"] == "completed"

    journal = gateway.call_tool("get_backtest_journal", {"id": run_id})
    assert journal.data == run_client.request_data("GET", f"/backtests/{run_id}/journal")


def test_mcp_error_carries_the_rest_code(client: Client) -> None:
    gateway = McpGateway(client)

    with pytest.raises(McpError) as err:
        gateway.call_tool("get_bars", {"id": "NOPE.NSE", "query": {}})

    assert err.value.code == "instrument_not_found"
    with pytest.raises(Exception) as rest_err:
        client.request_data("GET", "/bars/NOPE.NSE")
    assert getattr(rest_err.value, "code", None) == err.value.code


def test_mcp_tool_output_marked(client: Client) -> None:
    """E5-S2: free text in an MCP tool's output is wrapped and marked untrusted."""
    gateway = McpGateway(client)

    result = gateway.call_tool("get_instruments", {"query": {"symbol": "TCS"}})

    wrapped = json.loads(result.content)["instruments"][0]["id"]["symbol"]
    assert wrapped["untrusted"] is True
    assert wrapped["content"] == "TCS"
    assert wrapped["origin"] == "mcp:get_instruments:symbol"
    assert wrapped["content_hash"]
    # The raw payload is unchanged, so the result still equals the REST response.
    assert result.data["instruments"][0]["id"]["symbol"] == "TCS"
