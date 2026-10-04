import datetime as dt
import pytest
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.screener.coverage import (
    CoverageRecord,
    CoverageStatus,
    DateInterval,
)
from honba.screener.ports import (
    BarStore,
    InMemoryBarStore,
    InMemoryMarketDataProvider,
    MarketDataProvider,
    validate_bar,
)
from honba.screener.service import DataService, MissingDataPolicy


def test_validate_bar():
    inst = InstrumentId("RELIANCE", "NSE")
    # Valid bar
    valid = Bar(inst, 1700000000_000000000, 100.0, 105.0, 95.0, 102.0, 1000.0)
    assert validate_bar(valid) is True

    # Invalid high < low
    with pytest.raises(ValueError, match="high must be >= low"):
        validate_bar(Bar(inst, 1700000000_000000000, 100.0, 90.0, 95.0, 102.0, 1000.0))

    # Invalid volume < 0
    with pytest.raises(ValueError, match="volume must be >= 0"):
        validate_bar(Bar(inst, 1700000000_000000000, 100.0, 105.0, 95.0, 102.0, -10.0))


def test_in_memory_bar_store_roundtrip():
    store = InMemoryBarStore()
    inst = InstrumentId("RELIANCE", "NSE")
    timeframe = "1D"

    # Initially empty coverage
    assert store.coverage(inst, timeframe) == []

    # Create dummy bars for 2025-01-06 and 2025-01-07
    b1 = Bar(inst, int(dt.datetime(2025, 1, 6, 9, 15).timestamp() * 1e9), 100.0, 105.0, 95.0, 102.0, 1000.0)
    b2 = Bar(inst, int(dt.datetime(2025, 1, 7, 9, 15).timestamp() * 1e9), 102.0, 108.0, 101.0, 107.0, 1500.0)

    record = CoverageRecord(
        exchange="NSE",
        symbol="RELIANCE",
        timeframe=timeframe,
        interval=DateInterval(dt.date(2025, 1, 6), dt.date(2025, 1, 8)),
        status=CoverageStatus.FINAL,
        source="test_provider",
        row_count=2,
    )
    store.append(record, [b1, b2])

    cov = store.coverage(inst, timeframe)
    assert len(cov) == 1
    assert cov[0].interval == DateInterval(dt.date(2025, 1, 6), dt.date(2025, 1, 8))

    read_bars = store.read(inst, timeframe, DateInterval(dt.date(2025, 1, 6), dt.date(2025, 1, 8)))
    assert len(read_bars) == 2
    assert read_bars[0].close == 102.0
    assert read_bars[1].close == 107.0


def test_data_service_ensure_fills_gap():
    inst = InstrumentId("RELIANCE", "NSE")
    timeframe = "1D"
    provider = InMemoryMarketDataProvider()

    # Pre-populate provider with data for 2025-01-06 to 2025-01-10
    bars = []
    for d in range(6, 11):
        ts = int(dt.datetime(2025, 1, d, 9, 15).timestamp() * 1e9)
        bars.append(Bar(inst, ts, 100.0 + d, 105.0 + d, 95.0 + d, 102.0 + d, 1000.0))
    provider.add_bars(inst, timeframe, bars)

    store = InMemoryBarStore()
    service = DataService(store=store, providers=[provider])

    # Request range [2025-01-06, 2025-01-11)
    req = DateInterval(dt.date(2025, 1, 6), dt.date(2025, 1, 11))
    plan = service.plan([inst], timeframe, req)
    assert len(plan.gaps_by_instrument[inst]) == 1

    # Ensure should fetch gaps and store them
    result = service.ensure(plan)
    assert result.success is True
    assert len(result.bars[inst]) == 5

    # Check store coverage now reflects the filled gap
    cov = store.coverage(inst, timeframe)
    assert len(cov) == 1
    assert cov[0].interval == req

    # Second call is idempotent and should require 0 provider fetches
    plan2 = service.plan([inst], timeframe, req)
    assert plan2.total_gaps == 0
