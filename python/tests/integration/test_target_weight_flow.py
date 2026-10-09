"""TargetWeightStrategy end to end: strategy -> runner -> next-open simulator -> ledger.

Synthetic, deterministic bars; no network, no wall clock. Each bar opens at the previous
close and prices only step on days that are far from a rebalance, so fills happen at the
same prices the strategy marked the portfolio with and the result can be checked exactly.
"""

from __future__ import annotations

from honba.backtest.simulated import NextOpenExecution, group_sessions
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.strategies import TargetWeightStrategy
from honba.strategies.context import LedgerContext
from honba.strategies.runner import StrategyRunner

DAY = 86_400 * 10**9
T0 = 1_704_067_200 * DAY // 86_400  # 2024-01-01 UTC in ns
BASE = {"AAA": 50.0, "BBB": 120.0, "CCC": 333.0, "DDD": 77.0, "EEE": 210.0}
IDS = {s: InstrumentId(s, "NSE") for s in BASE}
CASH = 1_000_000.0
REBALANCE_DAYS = 10
N_DAYS = 100


def price(symbol: str, day: int) -> float:
    k = list(BASE).index(symbol)
    return round(BASE[symbol] * (1 + 0.05 * (((day + 5) // 10 + k) % 4)), 2)


def make_bars(symbols) -> list[Bar]:
    bars = []
    for sym in symbols:
        for d in range(N_DAYS):
            close = price(sym, d)
            open_ = price(sym, d - 1) if d else close
            hi, lo = max(open_, close), min(open_, close)
            bars.append(Bar(IDS[sym], T0 + d * DAY, open_, hi, lo, close, 1_000.0))
    return bars


class Book(TargetWeightStrategy):
    name = "tw_flow"
    rebalance_days = REBALANCE_DAYS

    def __init__(self, switch_day=None, last="DDD"):
        self.switch_day = switch_day
        self.last = last
        self.snapshots: dict[int, tuple[dict, float]] = {}

    def universe(self):
        day = (self.ctx.now() - T0) // DAY
        if self.switch_day is not None and day >= self.switch_day:
            return [IDS[s] for s in ("AAA", "BBB", "CCC", "EEE")]
        return [IDS[s] for s in ("AAA", "BBB", "CCC", "DDD")]

    def on_bar(self, bar):
        super().on_bar(bar)
        day = (bar.ts - T0) // DAY
        if bar.instrument_id.symbol == self.last and day % 10 == 3:  # settled, prices flat
            self.snapshots[day] = (dict(self.ctx.positions()), self.ctx.cash().to_major())


def run(strategy, symbols):
    port = NextOpenExecution(cash=Money.from_major(CASH, Currency.INR), settlement_days=0)
    runner = StrategyRunner(strategy, port, ctx=LedgerContext(cash=CASH))
    return runner.run(group_sessions(make_bars(symbols)))


def equity(positions, cash, day):
    return cash + sum(q * price(i.symbol, day) for i, q in positions.items())


def assert_equal_weight(positions, cash, day, members):
    e = equity(positions, cash, day)
    target = e * 0.98 / len(members)
    assert set(positions) == {IDS[s] for s in members}
    for iid, qty in positions.items():
        p = price(iid.symbol, day)
        assert qty == int(qty)
        assert target - p < qty * p <= target + 1e-6


def test_equal_weight_after_every_rebalance():
    s = Book()
    result = run(s, list(BASE)[:4])
    assert not result.rejections and not result.order_rejections
    assert len(s.snapshots) >= 9
    for day, (positions, cash) in s.snapshots.items():
        assert_equal_weight(positions, cash, day, ("AAA", "BBB", "CCC", "DDD"))
    assert result.fills


def test_universe_change_exits_leaver_and_enters_joiner():
    s = Book(switch_day=45, last="EEE")
    result = run(s, list(BASE))
    before = s.snapshots[43]
    after = s.snapshots[53]  # the day-50 rebalance has settled
    assert IDS["DDD"] in before[0] and IDS["EEE"] not in before[0]
    assert_equal_weight(*after, 53, ("AAA", "BBB", "CCC", "EEE"))
    assert not result.rejections and not result.order_rejections
    exits = [
        f
        for f in result.fills
        if f.instrument_id == IDS["DDD"] and f.side.name == "SELL" and f.ts > T0 + 45 * DAY
    ]
    assert [(f.ts, f.quantity) for f in exits] == [(T0 + 51 * DAY, before[0][IDS["DDD"]])]
