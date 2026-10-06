import datetime as dt

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.screener import (
    FilterOp,
    MetricKeySpec,
    ScreenerFilterGroup,
    ScreenerFilterPredicate,
    ScreenerScanRequest,
    ScreenerSortSpec,
)
from honba.screener.ports import InMemoryBarStore, InMemoryMarketDataProvider
from honba.screener.service import DataService
from honba.screener.sources import LocalScreenerSource


def test_screener_source_scan():
    inst1 = InstrumentId("RELIANCE", "NSE")
    inst2 = InstrumentId("TCS", "NSE")

    provider = InMemoryMarketDataProvider()
    today = dt.date.today()  # noqa: DTZ011 - test data relative to now; asserts are date-agnostic
    bars1 = []
    bars2 = []
    for d in range(10):
        date_val = today - dt.timedelta(days=10 - d)
        ts = int(dt.datetime.combine(date_val, dt.time(9, 15)).timestamp() * 1e9)
        c1 = 2500.0 + d * 5
        bars1.append(Bar(inst1, ts, 2500.0, c1 + 10.0, 2480.0, c1, 10000.0))
        c2 = 3500.0 + d * 10
        bars2.append(Bar(inst2, ts, 3500.0, c2 + 10.0, 3450.0, c2, 5000.0))

    provider.add_bars(inst1, "1D", bars1)
    provider.add_bars(inst2, "1D", bars2)

    store = InMemoryBarStore()
    service = DataService(store=store, providers=[provider])

    source = LocalScreenerSource(
        data_service=service,
        default_instruments=[inst1, inst2],
    )

    # Scan for close > 3000 -> Only TCS should match
    pred = ScreenerFilterPredicate(key="close", op=FilterOp.GT, value=3000.0)
    group = ScreenerFilterGroup(operator="AND", items=[pred])
    req = ScreenerScanRequest(
        market="india",
        columns=[MetricKeySpec(key="close")],
        filters=group,
        sort=ScreenerSortSpec(key="close", dir="desc"),
    )

    resp = source.scan(req, asof=today)
    assert resp.total == 1
    assert len(resp.rows) == 1
    assert resp.rows[0].full_symbol == "TCS.NSE"
    assert resp.rows[0].values["close"] == 3590.0
