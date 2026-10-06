"""``Client.inproc`` answers identically inside and outside a started ``honba.event_loop``.

The in-process REST path must reuse the one explicit runtime (ADR 0015); it may not change
results or leak threads whether or not the runtime is started.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

pytest.importorskip("honba._honba")

from honba import event_loop
from honba.client import ApiError, Client

MINUTE = 60_000_000_000
T0 = 1_704_067_200_000_000_000


@pytest.fixture(autouse=True)
def _stopped() -> Iterator[None]:
    event_loop.stop()
    yield
    event_loop.stop()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    rows = range(5)
    table = pa.table(
        {
            "ts": pa.array([T0 + i * MINUTE for i in rows], pa.int64()),
            "open": [100.0 + i for i in rows],
            "high": [102.0 + i for i in rows],
            "low": [99.0 + i for i in rows],
            "close": [101.0 + i for i in rows],
            "volume": [1000.0 + i for i in rows],
        }
    )
    pq.write_table(table, tmp_path / "TCS.NSE.parquet")
    return tmp_path


def _scenarios() -> dict[str, Callable[[Client], Any]]:
    return {
        "health": lambda c: c.health(),
        "capabilities": lambda c: c.capabilities(),
        "instruments": lambda c: c.instruments(),
    }


def _outcomes(client: Client) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, call in _scenarios().items():
        try:
            out[name] = call(client)
        except ApiError as exc:
            out[name] = (type(exc).__name__, str(exc))
    return out


def test_inproc_results_are_identical_inside_and_outside_the_event_loop(data_dir: Path) -> None:
    with Client.inproc(data_dir) as client:
        outside = _outcomes(client)
        with event_loop.running(worker_threads=2):
            inside = _outcomes(client)
        after = _outcomes(client)
    assert inside == outside
    assert after == outside


def test_inproc_calls_inside_the_loop_spawn_no_extra_runtime_threads(data_dir: Path) -> None:
    if not sys.platform.startswith("linux"):
        pytest.skip("native thread count needs /proc")

    def threads() -> int:
        return len(os.listdir("/proc/self/task"))

    with Client.inproc(data_dir) as client:
        _outcomes(client)  # warm the cached state
        with event_loop.running(worker_threads=2):
            started = threads()
            for _ in range(5):
                _outcomes(client)
            assert threads() == started
