"""Portfolio construction extensions end to end: strategy -> runner -> next-open simulator.

Synthetic daily bars (no network, no wall clock). Each bar opens at the previous close, so
fills land one day after the decision at the price the strategy marked.
"""

from __future__ import annotations

import datetime as dt

from honba.backtest.simulated import NextOpenExecution, group_sessions
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.strategies import PortfolioStrategy, StaticUniverse
from honba.strategies.context import LedgerContext
from honba.strategies.portfolio import (
    AnyOf,
    DriftBand,
    EveryNDays,
    InverseVolatility,
    MonthlyFirstSession,
    TopN,
    build_portfolio_strategy,
    momentum,
)
from honba.strategies.runner import StrategyRunner

DAY = 86_400 * 10**9
START = dt.date(2024, 1, 1)
T0 = (START - dt.date(1970, 1, 1)).days * DAY
CASH = 1_000_000.0
IDS = {s: InstrumentId(s, "NSE") for s in ("AAA", "BBB", "CCC", "DDD")}


def bars_from(series: dict[str, list[float]]) -> list[Bar]:
    bars = []
    for sym, closes in series.items():
        for d, close in enumerate(closes):
            open_ = closes[d - 1] if d else close
            hi, lo = max(open_, close), min(open_, close)
            bars.append(Bar(IDS[sym], T0 + d * DAY, open_, hi, lo, close, 1_000.0))
    return bars


def run(strategy, series):
    port = NextOpenExecution(cash=Money.from_major(CASH, Currency.INR), settlement_days=0)
    runner = StrategyRunner(strategy, port, ctx=LedgerContext(cash=CASH))
    return runner.run(group_sessions(bars_from(series)))


def fill_key(result):
    return sorted((f.ts, f.instrument_id.symbol, f.side.name, f.quantity) for f in result.fills)


def fill_days(result, symbol=None, side=None):
    return sorted(
        {
            (f.ts - T0) // DAY
            for f in result.fills
            if (symbol is None or f.instrument_id.symbol == symbol)
            and (side is None or f.side.name == side)
        }
    )


class Logged:
    """Schedule wrapper recording the bar days on which the inner schedule was due."""

    def __init__(self, inner):
        self.inner = inner
        self.needs_drift = getattr(inner, "needs_drift", False)
        self.due_days: list[int] = []

    def due(self, state):
        due = self.inner.due(state)
        if due:
            self.due_days.append((state.bar_day - START).days)
        return due


def zigzag(base, amp, n):
    return [base + (amp if d % 2 else 0.0) for d in range(n)]


def assert_clean(result):
    assert not result.rejections and not result.order_rejections


def test_inverse_vol_book_weights_calm_name_more_and_rebalances_on_schedule():
    series = {"AAA": zigzag(100.0, 12.0, 45), "BBB": zigzag(100.0, 1.0, 45)}
    sched = Logged(EveryNDays(10))
    s = PortfolioStrategy(
        StaticUniverse([IDS["AAA"], IDS["BBB"]]),
        InverseVolatility(10),
        sched,
        allocation=0.98,
        history_len=10,
    )
    result = run(s, series)
    assert_clean(result)
    assert sched.due_days == [0, 10, 20, 30, 40]
    last = 44
    notional = {i.symbol: q * series[i.symbol][last] for i, q in result.ctx.positions().items()}
    assert notional["BBB"] > 3 * notional["AAA"]


def test_monthly_first_session_rebalances_on_first_bar_of_each_month():
    n = 121  # 2024-01-01 .. 2024-04-30
    series = {
        "AAA": [100.0 + 0.5 * d for d in range(n)],
        "BBB": [100.0 + (3.0 if d % 7 < 3 else 0.0) for d in range(n)],
    }
    sched = Logged(MonthlyFirstSession())
    s = PortfolioStrategy(StaticUniverse([IDS["AAA"], IDS["BBB"]]), schedule=sched)
    result = run(s, series)
    assert_clean(result)
    months = [START + dt.timedelta(days=d) for d in sched.due_days]
    assert months == [
        dt.date(2024, 1, 1),
        dt.date(2024, 2, 1),
        dt.date(2024, 3, 1),
        dt.date(2024, 4, 1),
    ]
    assert [d for d in fill_days(result) if d > 1] == [32, 61, 92]


def test_drift_band_holds_within_tolerance_and_rebalances_when_exceeded():
    n = 40
    calm = [100.0 + (0.5 if d % 2 else 0.0) for d in range(n)]
    moved = [100.0] * 20 + [140.0] * 20  # AAA jumps +40% on day 20
    series = {"AAA": moved, "BBB": calm}
    sched = Logged(DriftBand(0.05))
    s = PortfolioStrategy(StaticUniverse([IDS["AAA"], IDS["BBB"]]), schedule=sched, allocation=0.98)
    result = run(s, series)
    assert_clean(result)
    assert sched.due_days == [0, 20]
    assert fill_days(result) == [1, 21]


def test_drift_band_alone_never_rebalances_when_prices_stay_in_band():
    flat = {"AAA": [100.0] * 30, "BBB": [100.0] * 30}
    sched = Logged(AnyOf(DriftBand(0.05)))
    s = PortfolioStrategy(StaticUniverse([IDS["AAA"], IDS["BBB"]]), schedule=sched)
    result = run(s, flat)
    assert sched.due_days == [0]
    assert fill_days(result) == [1]


def test_top_n_momentum_exits_dropped_names_and_enters_new_ones():
    n = 60
    rise = [100.0 * 1.01**d for d in range(n)]
    fade = [100.0 * 1.01 ** min(d, 25) * 0.97 ** max(0, d - 25) for d in range(n)]
    late = [100.0] * 25 + [100.0 * 1.04 ** (d - 25) for d in range(25, n)]
    series = {"AAA": rise, "BBB": fade, "CCC": late, "DDD": [100.0 * 0.995**d for d in range(n)]}
    s = PortfolioStrategy(
        StaticUniverse([IDS[x] for x in ("AAA", "BBB", "CCC", "DDD")]),
        schedule=EveryNDays(10),
        selector=TopN(2, momentum(5)),
        allocation=0.9,
        history_len=5,
    )
    result = run(s, series)
    assert_clean(result)
    assert {i.symbol for i in result.ctx.positions()} == {"AAA", "CCC"}
    assert fill_days(result, "BBB", "BUY") and fill_days(result, "BBB", "SELL")
    assert not fill_days(result, "DDD")
    first_ccc = fill_days(result, "CCC", "BUY")[0]
    assert first_ccc > 25


def test_book_built_from_params_matches_book_built_in_code():
    n = 121
    series = {
        sym: [100.0 * (1 + 0.002 * (k + 1)) ** d + 3.0 * ((d * (k + 2)) % 5) for d in range(n)]
        for k, sym in enumerate(("AAA", "BBB", "CCC", "DDD"))
    }
    names = ["AAA", "BBB", "CCC", "DDD"]
    params = {
        "universe": names,
        "weighting": "inverse_vol:15",
        "schedule": "monthly:first_session+drift:0.04",
        "select": "top:3:momentum:20",
        "allocation": 0.95,
    }
    by_params = build_portfolio_strategy(params, name="pf")
    by_code = PortfolioStrategy(
        StaticUniverse([IDS[x] for x in names]),
        InverseVolatility(15),
        AnyOf(MonthlyFirstSession(), DriftBand(0.04)),
        TopN(3, momentum(20)),
        allocation=0.95,
        name="pf",
        history_len=64,
    )
    a, b = run(by_params, series), run(by_code, series)
    assert_clean(a)
    assert a.fills and fill_key(a) == fill_key(b)
    assert a.ctx.positions() == b.ctx.positions() and a.ctx.cash() == b.ctx.cash()
