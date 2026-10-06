"""Screener data path (provider -> DataService -> store) is identical under any local TZ."""

import datetime as dt
import time

import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.screener.coverage import DateInterval
from honba.screener.ports import InMemoryBarStore, InMemoryMarketDataProvider
from honba.screener.service import DataService

INST = InstrumentId("RELIANCE", "NSE")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _run() -> list[tuple[int, float]]:
    provider = InMemoryMarketDataProvider()
    bars = []
    for d in range(1, 6):
        ts = int(dt.datetime(2024, 1, d, 9, 15, tzinfo=IST).timestamp() * 1e9)
        bars.append(Bar(INST, ts, 100.0 + d, 105.0 + d, 95.0 + d, 102.0 + d, 1000.0))
    provider.add_bars(INST, "1D", bars)
    service = DataService(store=InMemoryBarStore(), providers=[provider])
    req = DateInterval(dt.date(2024, 1, 1), dt.date(2024, 1, 6))
    result = service.ensure(service.plan([INST], "1D", req))
    assert result.success
    return [(b.ts, b.close) for b in result.bars[INST]]


def _under(tz: str, monkeypatch) -> list[tuple[int, float]]:
    monkeypatch.setenv("TZ", tz)
    time.tzset()
    try:
        return _run()
    finally:
        monkeypatch.undo()
        time.tzset()


def test_data_service_identical_under_new_york_and_kolkata(monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("tzset unavailable")
    ist = _under("Asia/Kolkata", monkeypatch)
    ny = _under("America/New_York", monkeypatch)
    assert len(ist) == 5
    assert ny == ist
