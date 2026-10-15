"""OCO cancel end to end: runner + L2 simulator, and the replay harness."""

from __future__ import annotations

from honba.backtest import L2Execution
from honba.domain.money import Currency, Money
from honba.domain.tick import AggressorSide, QuoteTick, TradeTick
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderStatus
from honba.strategies import DeclarativeStrategy, Entry
from honba.strategies.runner import StrategyRunner
from honba.strategies.testing import replay

IID = InstrumentId("AAA", "NSE")
CASH = Money.from_major(1_000_000, Currency.INR)


class Swing(DeclarativeStrategy):
    name = "swing_oco"

    def __init__(self, exit_after: int | None = None):
        self.exit_after = exit_after
        self.bars = 0

    def should_long(self, bar):
        return self.bars == 0

    def go_long(self, bar):
        return Entry(5, stop_loss=95, take_profit=110)

    def should_exit(self, bar):
        return self.exit_after is not None and self.bars >= self.exit_after

    def on_bar(self, bar):
        super().on_bar(bar)
        self.bars += 1


def _quote(ts, bid=99.5, ask=100.5):
    return QuoteTick(IID, ts, bid, ask, 1000, 1000)


def _print(ts, price):
    return TradeTick(IID, ts, price, 50.0, AggressorSide.SELLER, f"T{ts}")


def _bar(ts, c):
    return Bar(IID, ts, c, c, c, c, 1000.0)


def _run(events, strategy):
    sim = L2Execution(cash=CASH, backend="python")
    runner = StrategyRunner(strategy, sim)
    runner.run(events)
    return runner, sim


def _stop_hit_events():
    return [
        (_quote(1), 1),
        (_bar(2, 100.0), 2),  # entry decided, filled at the ask
        (_quote(3), 3),  # protectives are sent
        (_print(4, 94.0), 4),  # trade through the stop
        (_quote(5, 93.5, 94.5), 5),
        (_bar(6, 94.0), 6),
    ]


def test_stop_hit_cancels_take_profit_in_the_simulator():
    runner, sim = _run(_stop_hit_events(), Swing())
    assert sim.working_orders == []
    assert dict(sim.positions).get(IID, 0.0) == 0.0
    assert not runner.ctx.busy(IID)
    statuses = {sub.order_id: runner.order_state(sub.order_id).status for sub in runner.intents}
    assert statuses["swing_oco-0"] is OrderStatus.FILLED
    assert statuses["swing_oco-1"] is OrderStatus.FILLED  # stop
    assert statuses["swing_oco-2"] is OrderStatus.CANCELLED  # take-profit
    assert [r.order_id for r in runner.order_rejections] == ["swing_oco-2"]
    assert len(runner.fills) == 2


def test_take_profit_hit_cancels_stop():
    events = [
        (_quote(1), 1),
        (_bar(2, 100.0), 2),
        (_quote(3), 3),
        (_print(4, 111.0), 4),
        (_bar(5, 111.0), 5),
    ]
    runner, sim = _run(events, Swing())
    assert sim.working_orders == []
    assert runner.order_state("swing_oco-1").status is OrderStatus.CANCELLED  # stop
    assert runner.order_state("swing_oco-2").status is OrderStatus.FILLED


def test_manual_exit_cancels_resting_protectives():
    events = [
        (_quote(1), 1),
        (_bar(2, 100.0), 2),
        (_quote(3), 3),
        (_bar(4, 101.0), 4),  # exit_after=1 -> should_exit while both rest
        (_quote(5), 5),
    ]
    runner, sim = _run(events, Swing(exit_after=1))
    assert sim.working_orders == []
    assert dict(sim.positions).get(IID, 0.0) == 0.0
    states = [runner.order_state(f"swing_oco-{i}").status for i in range(4)]
    assert states == [
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.CANCELLED,
        OrderStatus.FILLED,
    ]


def test_run_is_deterministic():
    def once():
        runner, sim = _run(_stop_hit_events(), Swing())
        return (
            [(s.order_id, s.intent) for s in runner.intents],
            [(f.order_id, f.quantity, f.price, f.ts) for f in runner.fills],
            [(r.order_id, r.reason) for r in runner.order_rejections],
            sim.cash,
        )

    assert once() == once()


def test_replay_skips_cancelled_sibling():
    bars = [_bar(i + 1, c) for i, c in enumerate([100, 101, 102])]
    result = replay(Swing(), bars)
    # entry, then stop and take-profit are marketable at the close: the stop fills first
    # and cancels the take-profit before it can fill.
    assert [(f.side.value, f.quantity) for f in result.fills] == [("buy", 5), ("sell", 5)]


def test_replay_delayed_is_deterministic():
    bars = [_bar(i + 1, c) for i, c in enumerate([100, 101, 102, 103, 104])]
    a = replay(Swing(), bars, fill_delay=1)
    b = replay(Swing(), bars, fill_delay=1)
    assert a.fills == b.fills and a.intents == b.intents
    assert len(a.fills) == 2
