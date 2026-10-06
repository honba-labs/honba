"""NseBhavcopyProvider emits IST-anchored bar timestamps from a cached bhavcopy fixture."""

import datetime as dt
import time

import pytest

from honba.entities.instrument import InstrumentId
from honba.research.data_loader.nse import NseBhavcopyProvider
from honba.screener.coverage import DateInterval

UDIFF = """TradDt,TckrSymb,SctySrs,OpnPric,HghPric,LwPric,ClsPric,TtlTradgVol
2024-01-02,RELIANCE,EQ,100.0,110.0,90.0,105.0,1000
"""


def test_provider_fetch_ts_is_ist_under_foreign_tz(tmp_path, monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("tzset unavailable")
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    try:
        (tmp_path / "bhavcopy_20240102.csv").write_text(UDIFF, encoding="utf-8")
        provider = NseBhavcopyProvider(cache_dir=tmp_path)
        interval = DateInterval(dt.date(2024, 1, 2), dt.date(2024, 1, 3))
        bars = provider.fetch(InstrumentId("RELIANCE", "NSE"), "1D", interval)
        assert [b.ts for b in bars] == [1_704_167_100 * 1_000_000_000]
    finally:
        monkeypatch.undo()
        time.tzset()
