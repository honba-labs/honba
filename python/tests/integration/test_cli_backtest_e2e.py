"""``honba backtest`` end to end: real in-process server (and ``--url`` to a real ``honba serve``).

Exit codes: 0 completed, 1 failed run, 2 usage / validation, 3 transport or queue.
"""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from _runs_support import END, START, honba_binary, scratch, serve, walk, write_bars
from typer.testing import CliRunner

from honba.cli.main import app

pytest.importorskip("honba._honba")
runner = CliRunner()


@pytest.fixture(scope="module")
def dirs() -> Iterator[tuple[Path, Path]]:
    with scratch("cli-backtest") as root:
        data = root / "bars"
        data.mkdir()
        write_bars(data, "TCS", walk(300))
        yield data, root / "journals"


def args(dirs: tuple[Path, Path], *extra: str, **override: str) -> list[str]:
    data, journals = dirs
    base = {
        "--strategy": "sma_crossover",
        "--universe": "TCS.NSE",
        "--start": START,
        "--end": END,
        "--seed": "42",
        "--bar-spec": "1m",
    }
    base.update({f"--{k.replace('_', '-')}": v for k, v in override.items()})
    flat = [x for pair in base.items() for x in pair]
    return ["backtest", "--data-dir", str(data), "--journals-dir", str(journals), *flat, *extra]


def test_a_completed_run_exits_0_with_json_on_stdout(dirs: tuple[Path, Path]) -> None:
    res = runner.invoke(app, args(dirs))
    assert res.exit_code == 0, res.output
    out: dict[str, Any] = json.loads(res.stdout)
    assert out["status"] == "completed" and len(out["run_id"]) == 26
    assert out["metrics"]["trades"] >= 1
    assert "slippage" in out["assumptions"]["not_modelled"]


def test_a_run_without_bars_exits_1_and_reports_the_error(dirs: tuple[Path, Path]) -> None:
    res = runner.invoke(app, args(dirs, start="2030-01-01", end="2030-02-01"))
    assert res.exit_code == 1, res.output
    out = json.loads(res.stdout)
    assert out["status"] == "failed" and out["error"]["code"] == "market_data_unavailable"
    assert "market_data_unavailable" in res.stderr


def test_an_unregistered_strategy_exits_2(dirs: tuple[Path, Path]) -> None:
    res = runner.invoke(app, args(dirs, strategy="momentum_alpha"))
    assert res.exit_code == 2, res.output
    assert "strategy" in res.stderr


def test_a_closed_port_exits_3() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    res = runner.invoke(
        app,
        ["backtest", "--url", f"http://127.0.0.1:{port}", "--strategy", "buy_and_hold",
         "--universe", "TCS.NSE", "--start", START, "--end", END, "--seed", "1"],
    )  # fmt: skip
    assert res.exit_code == 3, res.output
    assert res.stderr


def test_url_runs_against_a_real_server(dirs: tuple[Path, Path]) -> None:
    binary = honba_binary()
    if binary is None:
        pytest.skip("honba binary not built (cargo build -p honba-cli, or set HONBA_BIN)")
    data, journals = dirs
    with serve(binary, data, journals / "serve") as url:
        res = runner.invoke(
            app,
            ["backtest", "--url", url, "--strategy", "buy_and_hold", "--universe", "TCS.NSE",
             "--start", START, "--end", END, "--seed", "1", "--bar-spec", "1m"],
        )  # fmt: skip
        assert res.exit_code == 0, res.output
        assert json.loads(res.stdout)["status"] == "completed"
        bad = runner.invoke(
            app,
            ["backtest", "--url", url, "--strategy", "nope", "--universe", "TCS.NSE",
             "--start", START, "--end", END, "--seed", "1"],
        )  # fmt: skip
        assert bad.exit_code == 2, bad.output
