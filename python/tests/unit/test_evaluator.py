import datetime as dt

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.screener import (
    FilterOp,
    ScreenerFilterGroup,
    ScreenerFilterPredicate,
)
from honba.screener.evaluator import (
    evaluate_group_on_bars,
    evaluate_predicate_on_bars,
)


def make_sample_bars(n=10, start_price=100.0, trend=1.0):
    inst = InstrumentId("RELIANCE", "NSE")
    bars = []
    base_ts = int(dt.datetime(2025, 1, 1, 9, 15).timestamp() * 1e9)
    for i in range(n):
        price = start_price + i * trend
        b = Bar(
            instrument_id=inst,
            ts=base_ts + i * 86400 * 1_000_000_000,
            open=price,
            high=price + 2.0,
            low=price - 2.0,
            close=price,
            volume=1000.0 + i * 100,
        )
        bars.append(b)
    return bars


def test_evaluate_basic_comparison():
    bars = make_sample_bars(n=5, start_price=100.0, trend=10.0)
    # Latest close is 140.0
    pred_gt = ScreenerFilterPredicate(key="close", op=FilterOp.GT, value=130.0)
    assert evaluate_predicate_on_bars(pred_gt, bars) is True

    pred_lt = ScreenerFilterPredicate(key="close", op=FilterOp.LT, value=130.0)
    assert evaluate_predicate_on_bars(pred_lt, bars) is False

    pred_between = ScreenerFilterPredicate(key="close", op=FilterOp.BETWEEN, value=[135.0, 145.0])
    assert evaluate_predicate_on_bars(pred_between, bars) is True


def test_evaluate_moving_averages():
    # 25 bars: first 20 at 100.0, next 5 at 200.0
    inst = InstrumentId("RELIANCE", "NSE")
    bars = []
    base_ts = int(dt.datetime(2025, 1, 1, 9, 15).timestamp() * 1e9)
    for i in range(20):
        bars.append(
            Bar(inst, base_ts + i * 86400 * 1_000_000_000, 100.0, 100.0, 100.0, 100.0, 1000.0)
        )
    for i in range(20, 25):
        bars.append(
            Bar(inst, base_ts + i * 86400 * 1_000_000_000, 200.0, 200.0, 200.0, 200.0, 1000.0)
        )

    # SMA20 of last 20 bars: 15 bars of 100 + 5 bars of 200 = (1500 + 1000) / 20 = 125.0
    pred_sma = ScreenerFilterPredicate(key="SMA20", op=FilterOp.GT, value=120.0)
    assert evaluate_predicate_on_bars(pred_sma, bars) is True


def test_evaluate_crossover():
    # 3 bars where metric crosses above threshold or another metric
    inst = InstrumentId("RELIANCE", "NSE")
    base_ts = int(dt.datetime(2025, 1, 1, 9, 15).timestamp() * 1e9)
    b1 = Bar(inst, base_ts, 90.0, 90.0, 90.0, 90.0, 100.0)
    b2 = Bar(inst, base_ts + 86400 * 1_000_000_000, 110.0, 110.0, 110.0, 110.0, 100.0)

    pred_cross = ScreenerFilterPredicate(key="close", op=FilterOp.CROSSES_ABOVE, value=100.0)
    assert evaluate_predicate_on_bars(pred_cross, [b1, b2]) is True


def test_evaluate_group():
    bars = make_sample_bars(n=5, start_price=100.0, trend=10.0)
    # latest close = 140.0, volume = 1400.0
    p1 = ScreenerFilterPredicate(key="close", op=FilterOp.GT, value=100.0)
    p2 = ScreenerFilterPredicate(key="volume", op=FilterOp.GTE, value=1500.0)

    # AND: p1 is True, p2 is False -> False
    group_and = ScreenerFilterGroup(operator="AND", items=[p1, p2])
    assert evaluate_group_on_bars(group_and, bars) is False

    # OR: p1 is True, p2 is False -> True
    group_or = ScreenerFilterGroup(operator="OR", items=[p1, p2])
    assert evaluate_group_on_bars(group_or, bars) is True
