"""Helpers shared by the run (``/backtests``) integration tests.

Scratch directories live under the repo's ``target/tmp`` (git-ignored), never ``/tmp``.
Bars are a seeded random walk written as ``SYMBOL.EXCHANGE.parquet`` one-minute files, the
layout both ``honba serve`` and the in-process transport read.
"""

from __future__ import annotations

import math
import os
import queue
import random
import shutil
import signal
import subprocess
import threading
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from honba.client import Client
from honba.domain.instrument import InstrumentKind
from honba.entities.bar import Bar
from honba.entities.instrument import Instrument, InstrumentId

REPO = Path(__file__).resolve().parents[3]
MINUTE = 60_000_000_000
T0 = 1_704_067_200_000_000_000  # 2024-01-01T00:00:00Z
START, END = "2024-01-01", "2024-01-02"
TCS = InstrumentId("TCS", "NSE")


@contextmanager
def scratch(label: str) -> Iterator[Path]:
    """A fresh directory under ``target/tmp/pytest-runs``, removed afterwards."""
    root = Path(os.environ.get("CARGO_TARGET_DIR", REPO / "target")) / "tmp" / "pytest-runs"
    path = root / f"{label}-{uuid.uuid4().hex[:8]}"
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def walk(count: int, seed: int = 1234) -> list[tuple[int, float, float, float, float, float]]:
    """``count`` one-minute rows ``(ts, open, high, low, close, volume)``: a seeded random
    walk with a slow sine so moving-average crossovers happen."""
    rng = random.Random(seed)
    price, rows = 100.0, []
    for i in range(count):
        price = max(5.0, price + rng.gauss(0, 1.5) + 2.0 * math.sin(i / 9))
        o = round(price, 2)
        c = round(price + rng.gauss(0, 0.5), 2)
        rows.append((T0 + i * MINUTE, o, max(o, c) + 1, min(o, c) - 1, c, 1000.0))
    return rows


def write_bars(directory: Path, symbol: str, rows: list[tuple[int, ...]]) -> None:
    names = ["ts", "open", "high", "low", "close", "volume"]
    table = pa.table({name: [row[i] for row in rows] for i, name in enumerate(names)})
    pq.write_table(table, directory / f"{symbol}.NSE.parquet")


class RowsProvider:
    """A ``BacktestSession`` data provider over the very rows written to Parquet."""

    def __init__(self, rows: list[tuple[int, ...]]) -> None:
        self._bars = [Bar(TCS, r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]

    def bars(self, instrument_id, *, timeframe, start, end) -> list[Bar]:
        return list(self._bars)

    def instrument(self, instrument_id) -> Instrument:
        return Instrument(instrument_id, InstrumentKind.EQUITY, 1.0, 0.05)


def honba_binary() -> Path | None:
    candidates = [os.environ.get("HONBA_BIN")]
    for root in (os.environ.get("CARGO_TARGET_DIR"), str(REPO / "target")):
        if root:
            candidates += [f"{root}/debug/honba", f"{root}/release/honba"]
    return next((Path(c) for c in candidates if c and Path(c).is_file()), None)


@contextmanager
def serve(binary: Path, data_dir: Path, journals_dir: Path, *flags: str) -> Iterator[str]:
    """Run ``honba serve`` on a free port; yields its base URL. Skips when it is not one."""
    import pytest

    child = subprocess.Popen(
        [
            str(binary),
            "serve",
            "--data-dir",
            str(data_dir),
            "--journals-dir",
            str(journals_dir),
            "--addr",
            "127.0.0.1:0",
            *flags,
        ],
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
        yield line.strip()[len(prefix) :]
    finally:
        child.send_signal(signal.SIGINT)
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
        for stream in (child.stdout, child.stderr):
            if stream:
                stream.close()


@contextmanager
def open_clients(data_dir: Path, journals_dir: Path, *flags: str) -> Iterator[dict[str, Client]]:
    """``{"inproc": client}`` plus ``"http"`` when the ``honba`` binary exists; closed on exit."""
    with ExitStack() as stack:
        found = {
            "inproc": stack.enter_context(
                Client.inproc(data_dir, journals_dir=journals_dir / "inproc")
            )
        }
        binary = honba_binary()
        if binary is not None:
            url = stack.enter_context(serve(binary, data_dir, journals_dir / "http", *flags))
            found["http"] = stack.enter_context(Client.http(url, timeout=30))
        yield found
