"""Unit tests for PortfolioStrategy wiring (universe -> selector -> weighting -> schedule)."""

from __future__ import annotations

import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderSide
from honba.entities.trade import Trade
from honba.strategies import PortfolioStrategy, TargetWeightStrategy
from honba.strategies.context import LedgerContext
from honba.strategies.portfolio import (
    EqualWeight,
    EveryNDays,
    ScheduleState,
    SelectAll,
    StaticUniverse,
)

DAY = 86_400 * 10**9
A, B, C = (InstrumentId(s, "NSE") for s in "ABC")


def bar(iid, day, close=100.0):
    return Bar(iid, day * DAY, close, close, close, close, 1000.0)


def make(*args, cash=100_000.0, **kw):
    s = PortfolioStrategy(*args, **kw)
    s.bind(LedgerContext(cash=cash))
    return s


def feed(s, *bars):
    for b in bars:
        s.on_bar(b)
    return [(i.instrument_id.symbol, i.side.name, i.quantity) for i in s.drain_intents()]


def test_is_target_weight_strategy_with_defaults():
    s = make(StaticUniverse([A]))
    assert isinstance(s, TargetWeightStrategy)
    assert s.name == "portfolio"
    assert s.allocation == 0.98
    assert isinstance(s.schedule, EveryNDays) and s.schedule.n == 15
    assert isinstance(s.weighting, EqualWeight) and isinstance(s.selector, SelectAll)


def test_name_is_settable_per_instance_without_touching_class():
    s1 = PortfolioStrategy(StaticUniverse([A]), name="alpha30_ew")
    s2 = PortfolioStrategy(StaticUniverse([A]))
    assert s1.name == "alpha30_ew" and s2.name == "portfolio"
    assert PortfolioStrategy.name == "portfolio"
    assert s1.logger.name == "honba.strategy.alpha30_ew"


def test_blank_name_rejected():
    with pytest.raises(ValueError, match="name"):
        PortfolioStrategy(StaticUniverse([A]), name="  ")


def test_allocation_validated():
    for bad in (0.0, -0.1, 1.5, float("nan")):
        with pytest.raises(ValueError, match="allocation"):
            PortfolioStrategy(StaticUniverse([A]), allocation=bad)


def test_history_len_validated():
    with pytest.raises(ValueError, match="history_len"):
        PortfolioStrategy(StaticUniverse([A]), history_len=0)


def test_universe_goes_through_selector():
    class OnlyFirst:
        def select(self, members, view):
            return list(members)[:1]

    s = make(StaticUniverse([B, A]), selector=OnlyFirst())
    assert list(s.universe()) == [B]


def test_weights_go_through_weighting_scheme():
    class Fixed:
        def weights(self, selected, view):
            return {selected[0]: 0.5}

    s = make(StaticUniverse([A, B]), weighting=Fixed())
    assert dict(s.target_weights()) == {A: 0.5}


def test_selector_and_weighting_see_a_market_view_with_prices_and_history():
    seen = {}

    class Spy:
        def select(self, members, view):
            seen["sel"] = view
            return members

        def weights(self, selected, view):
            seen["w"] = view
            return {i: 0.5 for i in selected}

    s = make(StaticUniverse([A]), selector=Spy(), weighting=Spy(), history_len=2, schedule=Never())
    feed(s, bar(A, 0, 10.0), bar(A, 1, 11.0), bar(A, 2, 12.0))
    view = s._view()
    assert view.price(A) == 12.0
    assert view.recent_closes(A) == (11.0, 12.0)
    s.target_weights()
    assert seen["w"].recent_closes(A) == (11.0, 12.0)


class Never:
    def due(self, state):
        return False


class Recorder:
    def __init__(self):
        self.states: list[ScheduleState] = []

    def due(self, state):
        self.states.append(state)
        return False


def test_schedule_receives_state_from_bars():
    rec = Recorder()
    s = make(StaticUniverse([A, B]), schedule=rec)
    feed(s, bar(A, 19_000), bar(A, 19_001))
    first, second = rec.states
    assert str(first.bar_day) == "2022-01-08"
    assert not first.first_rebalance_done and not first.all_members_priced
    assert first.trading_days_since_rebalance == 0 and first.drift is None
    assert second.trading_days_since_rebalance == 1 and second.bar_day.isoformat() == "2022-01-09"


def test_first_rebalance_buys_equal_weights():
    s = make(StaticUniverse([A, B]), schedule=EveryNDays(2), allocation=0.9)
    out = feed(s, bar(A, 1), bar(B, 1))
    assert out == [("A", "BUY", 450), ("B", "BUY", 450)]


def test_membership_change_between_rebalances():
    u = StaticUniverse([A, B])
    s = make(u, schedule=EveryNDays(1), allocation=1.0)
    feed(s, bar(A, 1), bar(B, 1))
    s.ctx.apply_fill(Trade(A, OrderSide.BUY, 500, 100.0, 1))
    u.set_members([B, C])
    out = feed(s, bar(A, 2), bar(B, 2), bar(C, 2))
    assert ("A", "SELL", 500) in out
