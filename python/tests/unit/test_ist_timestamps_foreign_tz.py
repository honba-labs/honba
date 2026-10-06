"""Indian market dates map to IST-explicit epoch ns regardless of the machine's local TZ."""

import datetime as dt
import time

import pandas as pd
import pytest

from honba.data.loaders.yfinance import dataframe_to_bars
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.markets.india.calendar import ist_midnight_ns
from honba.screener.coverage import DateInterval
from honba.screener.ports import InMemoryBarStore, InMemoryMarketDataProvider

NS = 1_000_000_000
INST = InstrumentId("RELIANCE", "NSE")
# 2024-01-02 09:15 IST == 2024-01-02T03:45:00Z ; 2024-01-02 00:00 IST == 2024-01-01T18:30:00Z
OPEN_0915_NS = 1_704_167_100 * NS
MIDNIGHT_NS = 1_704_133_800 * NS
DAY = DateInterval(dt.date(2024, 1, 2), dt.date(2024, 1, 3))


@pytest.fixture(params=["America/New_York", "Pacific/Auckland"])
def foreign_tz(request, monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("tzset unavailable")
    monkeypatch.setenv("TZ", request.param)
    time.tzset()
    yield request.param
    monkeypatch.undo()
    time.tzset()


def _bar(ts: int) -> Bar:
    return Bar(INST, ts, 100.0, 105.0, 95.0, 102.0, 1000.0)


def test_ist_midnight_ns_helper(foreign_tz):
    assert ist_midnight_ns(dt.date(2024, 1, 2)) == MIDNIGHT_NS


def test_in_memory_bar_store_read_uses_ist_window(foreign_tz):
    from honba.screener.coverage import CoverageRecord, CoverageStatus

    store = InMemoryBarStore()
    rec = CoverageRecord("NSE", "RELIANCE", "1D", DAY, CoverageStatus.FINAL, "t", 1)
    store.append(rec, [_bar(OPEN_0915_NS)])
    assert [b.ts for b in store.read(INST, "1D", DAY)] == [OPEN_0915_NS]
    nxt = DateInterval(dt.date(2024, 1, 3), dt.date(2024, 1, 4))
    assert store.read(INST, "1D", nxt) == []


def test_in_memory_provider_fetch_uses_ist_window(foreign_tz):
    provider = InMemoryMarketDataProvider()
    provider.add_bars(INST, "1D", [_bar(OPEN_0915_NS)])
    assert [b.ts for b in provider.fetch(INST, "1D", DAY)] == [OPEN_0915_NS]


def _df(index):
    return pd.DataFrame(
        {"Open": [100.0], "High": [105.0], "Low": [95.0], "Close": [102.0], "Volume": [10.0]},
        index=index,
    )


def test_yfinance_daily_timestamp_with_interval_window(foreign_tz):
    df = _df(pd.DatetimeIndex(["2024-01-02"]))
    bars = dataframe_to_bars(df, INST, "1D", DAY)
    assert [b.ts for b in bars] == [OPEN_0915_NS]


def test_yfinance_date_index_without_india_alignment_is_ist_midnight(foreign_tz):
    df = _df([dt.date(2024, 1, 2)])
    bars = dataframe_to_bars(df, InstrumentId("AAPL", "NASDAQ"), "1D")
    assert [b.ts for b in bars] == [MIDNIGHT_NS]


def test_yfinance_naive_intraday_index_is_ist_for_india(foreign_tz):
    df = _df(pd.DatetimeIndex(["2024-01-02 09:15:00"]))
    bars = dataframe_to_bars(df, INST, "5m")
    assert [b.ts for b in bars] == [OPEN_0915_NS]
