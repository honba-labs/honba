"""Parquet and ledger backed BarStore implementation (Design.md Section 12.2).

Stores append-only immutable bar segments as Parquet files partitioned by:
<data_dir>/catalog/<timeframe>/<exchange>/<symbol>/<year>.parquet

Stores coverage intervals in a JSON/DuckDB ledger at:
<data_dir>/coverage_ledger.json
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.screener.coverage import CoverageRecord, CoverageStatus, DateInterval
from honba.screener.ports import validate_bar

logger = logging.getLogger(__name__)

BAR_SCHEMA = pa.schema(
    [
        ("ts", pa.int64()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
    ]
)


def find_data_root(start_path: Path | None = None) -> Path:
    """Find the project root data directory (`honba/data` or `<root>/data`).

    If no project root is found (not a git repo or source tree), falls back to `./data`.
    """
    cur = (start_path or Path.cwd()).resolve()
    for p in [cur, *cur.parents]:
        # Check if this directory is the repo root containing 'data' or 'honba/data'
        if (p / "honba" / "data").is_dir():
            return (p / "honba" / "data").resolve()
        if (p / "data").is_dir() and (
            (p / "crates").is_dir()
            or (p / "Cargo.toml").is_file()
            or (p / "pyproject.toml").is_file()
        ):
            return (p / "data").resolve()

    fallback = cur / "data"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


class ParquetBarStore:
    """Persistent BarStore backed by Parquet files and a coverage ledger."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = (data_dir or find_data_root()).resolve()
        self.catalog_dir = self.data_dir / "catalog"
        self.catalog_dir.mkdir(parents=True, exist_ok=True)
        self.ledger_file = self.data_dir / "coverage_ledger.json"

    def _load_ledger(self) -> dict[str, list[dict[str, Any]]]:
        if not self.ledger_file.exists():
            return {}
        try:
            return json.loads(self.ledger_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read ledger %s: %s", self.ledger_file, exc)
            return {}

    def _save_ledger(self, data: dict[str, list[dict[str, Any]]]) -> None:
        self.ledger_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self.ledger_file.with_suffix(".tmp")
        tmp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp_file.replace(self.ledger_file)

    def coverage(self, instrument: InstrumentId, timeframe: str) -> list[CoverageRecord]:
        key = f"{instrument.exchange.upper()}:{instrument.symbol.upper()}:{timeframe.upper()}"
        ledger = self._load_ledger()
        raw_records = ledger.get(key, [])
        records: list[CoverageRecord] = []
        for r in raw_records:
            records.append(
                CoverageRecord(
                    exchange=r.get("exchange"),
                    symbol=r["symbol"],
                    timeframe=r["timeframe"],
                    interval=DateInterval(
                        dt.date.fromisoformat(r["start"]),
                        dt.date.fromisoformat(r["end"]),
                    ),
                    status=CoverageStatus(r["status"]),
                    source=r.get("source", "unknown"),
                    row_count=r.get("row_count", 0),
                    checksum=r.get("checksum"),
                    fetched_at_ns=r.get("fetched_at_ns", 0),
                )
            )
        return records

    def _file_path(self, instrument: InstrumentId, timeframe: str, year: int) -> Path:
        return (
            self.catalog_dir
            / timeframe.upper()
            / instrument.exchange.upper()
            / instrument.symbol.upper()
            / f"{year}.parquet"
        )

    def read(self, instrument: InstrumentId, timeframe: str, interval: DateInterval) -> list[Bar]:
        """Read and deduplicate bars from year-partitioned parquet files."""
        start_year = interval.start.year
        end_year = (
            (interval.end - dt.timedelta(days=1)).year
            if interval.end > interval.start
            else interval.start.year
        )

        start_ns = int(dt.datetime.combine(interval.start, dt.time.min).timestamp() * 1e9)
        end_ns = int(dt.datetime.combine(interval.end, dt.time.min).timestamp() * 1e9)

        bars_by_ts: dict[int, Bar] = {}

        for y in range(start_year, end_year + 1):
            fpath = self._file_path(instrument, timeframe, y)
            if not fpath.exists():
                continue
            try:
                table = pq.read_table(fpath)
                ts_arr = table.column("ts").to_pylist()
                op_arr = table.column("open").to_pylist()
                hi_arr = table.column("high").to_pylist()
                lo_arr = table.column("low").to_pylist()
                cl_arr = table.column("close").to_pylist()
                vo_arr = table.column("volume").to_pylist()

                for ts, op, hi, lo, cl, vo in zip(ts_arr, op_arr, hi_arr, lo_arr, cl_arr, vo_arr):
                    if start_ns <= ts < end_ns:
                        bars_by_ts[ts] = Bar(
                            instrument_id=instrument,
                            ts=ts,
                            open=float(op),
                            high=float(hi),
                            low=float(lo),
                            close=float(cl),
                            volume=float(vo),
                        )
            except (OSError, pa.ArrowException) as exc:
                logger.error("Error reading %s: %s", fpath, exc)

        return [bars_by_ts[t] for t in sorted(bars_by_ts.keys())]

    def append(self, record: CoverageRecord, bars: Sequence[Bar]) -> None:
        """Append bars into partitioned parquet files and update coverage ledger."""
        inst = InstrumentId(record.symbol, record.exchange)
        tf = record.timeframe.upper()

        # Group bars by year
        bars_by_year: dict[int, list[Bar]] = {}
        for b in bars:
            validate_bar(b)
            bar_date = dt.datetime.fromtimestamp(b.ts / 1e9, tz=dt.timezone.utc).date()
            bars_by_year.setdefault(bar_date.year, []).append(b)

        # Write each year partition
        for year, year_bars in bars_by_year.items():
            fpath = self._file_path(inst, tf, year)
            fpath.parent.mkdir(parents=True, exist_ok=True)

            existing_bars: dict[int, Bar] = {}
            if fpath.exists():
                try:
                    table = pq.read_table(fpath)
                    for ts, op, hi, lo, cl, vo in zip(
                        table.column("ts").to_pylist(),
                        table.column("open").to_pylist(),
                        table.column("high").to_pylist(),
                        table.column("low").to_pylist(),
                        table.column("close").to_pylist(),
                        table.column("volume").to_pylist(),
                    ):
                        existing_bars[ts] = Bar(
                            instrument_id=inst,
                            ts=ts,
                            open=float(op),
                            high=float(hi),
                            low=float(lo),
                            close=float(cl),
                            volume=float(vo),
                        )
                except (OSError, pa.ArrowException) as exc:
                    logger.warning("Could not read existing parquet %s: %s", fpath, exc)

            for b in year_bars:
                existing_bars[b.ts] = b

            sorted_ts = sorted(existing_bars.keys())
            table = pa.Table.from_arrays(
                [
                    pa.array([existing_bars[t].ts for t in sorted_ts], type=pa.int64()),
                    pa.array([existing_bars[t].open for t in sorted_ts], type=pa.float64()),
                    pa.array([existing_bars[t].high for t in sorted_ts], type=pa.float64()),
                    pa.array([existing_bars[t].low for t in sorted_ts], type=pa.float64()),
                    pa.array([existing_bars[t].close for t in sorted_ts], type=pa.float64()),
                    pa.array([existing_bars[t].volume for t in sorted_ts], type=pa.float64()),
                ],
                schema=BAR_SCHEMA,
            )

            tmp_path = fpath.with_suffix(".tmp")
            pq.write_table(table, tmp_path, compression="snappy")
            tmp_path.replace(fpath)

        # Update ledger and merge contiguous/overlapping intervals
        key = f"{inst.exchange.upper()}:{inst.symbol.upper()}:{tf}"
        ledger = self._load_ledger()
        records_list = ledger.setdefault(key, [])
        records_list.append(
            {
                "exchange": record.exchange,
                "symbol": record.symbol,
                "timeframe": tf,
                "start": record.interval.start.isoformat(),
                "end": record.interval.end.isoformat(),
                "status": record.status.value,
                "source": record.source,
                "row_count": record.row_count,
                "checksum": record.checksum,
                "fetched_at_ns": record.fetched_at_ns,
            }
        )

        # Merge contiguous/overlapping records for clean ledger bookkeeping
        merged_records = self._merge_ledger_entries(records_list)
        ledger[key] = merged_records
        self._save_ledger(ledger)

    def _merge_ledger_entries(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge adjacent and overlapping ledger entries having the same status and exchange/symbol/tf."""
        if not entries:
            return []

        # Sort by start date
        sorted_entries = sorted(entries, key=lambda x: (x["start"], x["end"]))
        merged: list[dict[str, Any]] = [sorted_entries[0].copy()]

        for cur in sorted_entries[1:]:
            prev = merged[-1]
            prev_end = dt.date.fromisoformat(prev["end"])
            cur_start = dt.date.fromisoformat(cur["start"])
            cur_end = dt.date.fromisoformat(cur["end"])

            # If same status and contiguous or overlapping
            if prev.get("status") == cur.get("status") and cur_start <= prev_end:
                if cur_end > prev_end:
                    prev["end"] = cur["end"]
                prev["row_count"] = prev.get("row_count", 0) + cur.get("row_count", 0)
                prev["fetched_at_ns"] = max(
                    prev.get("fetched_at_ns", 0), cur.get("fetched_at_ns", 0)
                )
            else:
                merged.append(cur.copy())

        return merged
