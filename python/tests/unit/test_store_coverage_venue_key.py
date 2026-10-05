"""Test that ParquetBarStore.coverage() accepts both "venue" and "exchange" keys.

The persisted ledger on disk was written with "venue" by an older version of the
writer. A reader that hard-codes r["exchange"] raises KeyError. The fix
tolerates either key.
"""

from __future__ import annotations

import json
import pathlib
import tempfile

from honba.entities.instrument import InstrumentId
from honba.screener.store import ParquetBarStore


def test_coverage_reads_either_venue_or_exchange_key() -> None:
    with tempfile.TemporaryDirectory() as td:
        # Write a ledger entry using the OLD "venue" key
        ledger = {
            "NSE:RELIANCE:1D": [
                {
                    "venue": "NSE",  # old key
                    "symbol": "RELIANCE",
                    "timeframe": "1D",
                    "start": "2022-01-01",
                    "end": "2026-10-03",
                    "status": "final",
                    "source": "nse_bhavcopy",
                    "row_count": 1228,
                    "checksum": None,
                    "fetched_at_ns": 1790959336407291904,
                }
            ]
        }
        ledger_path = pathlib.Path(td) / "coverage_ledger.json"
        ledger_path.write_text(json.dumps(ledger))

        # Also need a data_dir with catalog structure (can be empty)
        data_dir = pathlib.Path(td)
        (data_dir / "catalog").mkdir()

        store = ParquetBarStore(data_dir=data_dir)
        # Override the ledger file location
        store.ledger_file = ledger_path

        recs = store.coverage(InstrumentId("RELIANCE", "NSE"), "1D")
        assert len(recs) == 1
        assert recs[0].exchange == "NSE"
        assert recs[0].symbol == "RELIANCE"


def test_coverage_prefers_venue_when_both_present() -> None:
    """If both keys exist, "venue" should win (it's what the writer uses)."""
    with tempfile.TemporaryDirectory() as td:
        ledger = {
            "NSE:TCS:1D": [
                {
                    "venue": "NSE",
                    "exchange": "BSE",  # ignored
                    "symbol": "TCS",
                    "timeframe": "1D",
                    "start": "2022-01-01",
                    "end": "2026-10-03",
                    "status": "final",
                    "source": "nse_bhavcopy",
                    "row_count": 1228,
                    "checksum": None,
                    "fetched_at_ns": 0,
                }
            ]
        }
        ledger_path = pathlib.Path(td) / "coverage_ledger.json"
        ledger_path.write_text(json.dumps(ledger))

        data_dir = pathlib.Path(td)
        (data_dir / "catalog").mkdir()

        store = ParquetBarStore(data_dir=data_dir)
        store.ledger_file = ledger_path

        recs = store.coverage(InstrumentId("TCS", "NSE"), "1D")
        assert recs[0].exchange == "NSE"


def test_coverage_falls_back_to_exchange_key() -> None:
    """New ledgers written by the current code use "exchange"; must still work."""
    with tempfile.TemporaryDirectory() as td:
        ledger = {
            "BSE:INFY:1D": [
                {
                    "exchange": "BSE",  # new key
                    "symbol": "INFY",
                    "timeframe": "1D",
                    "start": "2022-01-01",
                    "end": "2026-10-03",
                    "status": "final",
                    "source": "nse_bhavcopy",
                    "row_count": 1228,
                    "checksum": None,
                    "fetched_at_ns": 0,
                }
            ]
        }
        ledger_path = pathlib.Path(td) / "coverage_ledger.json"
        ledger_path.write_text(json.dumps(ledger))

        data_dir = pathlib.Path(td)
        (data_dir / "catalog").mkdir()

        store = ParquetBarStore(data_dir=data_dir)
        store.ledger_file = ledger_path

        recs = store.coverage(InstrumentId("INFY", "BSE"), "1D")
        assert recs[0].exchange == "BSE"
