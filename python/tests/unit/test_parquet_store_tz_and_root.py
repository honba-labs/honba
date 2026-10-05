"""``ParquetBarStore`` regression tests: UTC-explicit read windows and side-effect-free roots.

* ``read`` used to build its ``[start, end)`` window from naive local-time datetimes, so the
  same store returned different bars under different ``TZ`` settings.
* ``ParquetBarStore()`` used to derive its root from the working directory and create
  ``data/catalog`` there on construction, so merely importing the CLI wrote into cwd.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.screener.coverage import CoverageRecord, CoverageStatus, DateInterval
from honba.screener.store import DATA_DIR_ENV, ParquetBarStore, find_data_root

X = InstrumentId("TZTEST", "NSE")


def _ns(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso).timestamp() * 1_000_000_000)


# Bars straddling UTC midnight: a local-time window shifts which of these fall inside.
TIMESTAMPS = [
    _ns("2024-01-01T20:00:00+00:00"),
    _ns("2024-01-02T00:00:00+00:00"),
    _ns("2024-01-02T03:45:00+00:00"),
    _ns("2024-01-02T20:00:00+00:00"),
    _ns("2024-01-03T00:00:00+00:00"),
]


@pytest.fixture
def restore_tz(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    yield
    monkeypatch.undo()
    time.tzset()


def _seed(root: Path) -> ParquetBarStore:
    store = ParquetBarStore(root)
    bars = [Bar(X, ts, 10.0, 11.0, 9.0, 10.5, 100.0) for ts in TIMESTAMPS]
    record = CoverageRecord(
        exchange="NSE",
        symbol="TZTEST",
        timeframe="1D",
        interval=DateInterval(dt.date(2024, 1, 1), dt.date(2024, 1, 4)),
        status=CoverageStatus("final"),
        source="test",
        row_count=len(bars),
    )
    store.append(record, bars)
    return store


@pytest.mark.parametrize("tz", ["UTC", "Asia/Kolkata", "America/Los_Angeles"])
def test_read_window_is_utc_regardless_of_local_timezone(
    tz: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restore_tz: None
) -> None:
    store = _seed(tmp_path)
    monkeypatch.setenv("TZ", tz)
    time.tzset()

    got = store.read(X, "1D", DateInterval(dt.date(2024, 1, 2), dt.date(2024, 1, 3)))

    # The window is [2024-01-02T00:00Z, 2024-01-03T00:00Z) in every timezone.
    assert [b.ts for b in got] == TIMESTAMPS[1:4]


def test_construction_and_read_do_not_touch_the_filesystem(tmp_path: Path) -> None:
    root = tmp_path / "not-yet"
    store = ParquetBarStore(root)
    assert store.read(X, "1D", DateInterval(dt.date(2024, 1, 1), dt.date(2024, 2, 1))) == []
    assert store.coverage(X, "1D") == []
    assert not root.exists()


def test_append_creates_the_root_lazily(tmp_path: Path) -> None:
    root = tmp_path / "lazy"
    store = _seed(root)
    assert (root / "catalog").is_dir()
    assert store.read(X, "1D", DateInterval(dt.date(2024, 1, 1), dt.date(2024, 1, 4)))


def test_default_root_does_not_write_into_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    monkeypatch.chdir(tmp_path)

    store = ParquetBarStore()
    store.read(X, "1D", DateInterval(dt.date(2024, 1, 1), dt.date(2024, 2, 1)))
    store.coverage(X, "1D")

    assert list(tmp_path.iterdir()) == []


def test_find_data_root_has_no_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    assert find_data_root(tmp_path) == (tmp_path / "data").resolve()
    assert list(tmp_path.iterdir()) == []


def test_env_var_overrides_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = tmp_path / "explicit"
    monkeypatch.setenv(DATA_DIR_ENV, str(explicit))
    monkeypatch.chdir(tmp_path)
    assert find_data_root() == explicit.resolve()
    assert ParquetBarStore().data_dir == explicit.resolve()


def test_explicit_dir_beats_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "env"))
    assert ParquetBarStore(tmp_path / "arg").data_dir == (tmp_path / "arg").resolve()
