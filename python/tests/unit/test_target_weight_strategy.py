"""Unit tests for the portfolio-level TargetWeightStrategy."""

import datetime as dt
import logging
import math

import pytest

from honba.domain.money import Money
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderSide
from honba.entities.trade import Trade
from honba.strategies import TargetWeightStrategy
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext

DAY = 86_400 * 10**9
A, B, C, D = (InstrumentId(s, "NSE") for s in "ABCD")


def bar(iid, day, close=100.0, offset=0):
    ts = day * DAY + offset
    return Bar(iid, ts, close, close, close, close, 1000.0)


class Eq(TargetWeightStrategy):
    name = "tw_eq"

    def __init__(self, members=(A, B), cash=100_000.0, **attrs):
        self.members = list(members)
        self.weights = None
        self.ctx_cash = cash
        for k, v in attrs.items():
            setattr(self, k, v)
        self.bind(LedgerContext(cash=cash))

    def universe(self):
        return self.members

    def target_weights(self):
        return self.weights if self.weights is not None else super().target_weights()


def hold(s, iid, qty, price=100.0):
    s.ctx.apply_fill(Trade(iid, OrderSide.BUY, qty, price, 1))


def set_cash(s, amount):
    s.ctx._cash = Money.from_major(amount, s.ctx.currency)


def feed(s, *bars):
    for b in bars:
        s.on_bar(b)
    return s.drain_intents()


def summary(intents):
    return [(i.instrument_id.symbol, i.side.name, i.quantity) for i in intents]


def test_is_strategy_and_exported():
    assert issubclass(TargetWeightStrategy, Strategy)


def test_universe_must_be_overridden():
    class NoUniverse(TargetWeightStrategy):
        name = "tw_none"

    s = NoUniverse()
    with pytest.raises(NotImplementedError, match="universe"):
        s.universe()


def test_default_equal_weights():
    s = Eq(members=[A, B, C, D])
    assert s.target_weights() == {A: 0.25, B: 0.25, C: 0.25, D: 0.25}


def test_default_equal_weights_empty_universe():
    assert Eq(members=[]).target_weights() == {}


@pytest.mark.parametrize(
    "weights, match",
    [
        ({A: -0.1}, "shorts are not supported"),
        ({A: math.nan}, "finite"),
        ({A: math.inf}, "finite"),
        ({A: 0.6, B: 0.5}, "sum"),
    ],
)
def test_weight_validation(weights, match):
    s = Eq()
    s.weights = weights
    with pytest.raises(ValueError, match=match):
        feed(s, bar(A, 0), bar(B, 0))


def test_weights_outside_universe_rejected():
    s = Eq()
    s.weights = {C: 0.5}
    with pytest.raises(ValueError, match="universe"):
        feed(s, bar(A, 0), bar(B, 0))


def test_weights_summing_to_one_accepted():
    s = Eq()
    s.weights = {A: 0.5, B: 0.5}
    assert feed(s, bar(A, 0), bar(B, 0))


def test_custom_weights_and_allocation_buffer():
    s = Eq(cash=100_000.0)
    s.weights = {A: 0.5, B: 0.25}
    out = feed(s, bar(A, 0), bar(B, 0))
    # 100k * 0.98 * w / 100
    assert summary(out) == [("A", "BUY", 490), ("B", "BUY", 245)]


def test_allocation_settable_in_init():
    s = Eq(cash=100_000.0, allocation=0.5)
    assert summary(feed(s, bar(A, 0), bar(B, 0))) == [("A", "BUY", 250), ("B", "BUY", 250)]


def test_no_super_init_needed():
    assert Eq().rebalance_days == 15 and Eq().allocation == 0.98


def test_first_rebalance_waits_for_all_prices():
    s = Eq()
    assert feed(s, bar(A, 0)) == []
    assert len(feed(s, bar(B, 0))) == 2


def test_first_rebalance_falls_back_to_next_day():
    s = Eq(members=[A, B])
    assert feed(s, bar(A, 0), bar(A, 0, offset=5)) == []
    out = feed(s, bar(A, 1))
    assert summary(out) == [("A", "BUY", 490)]  # B has no price: skipped


def test_cadence_every_rebalance_days():
    s = Eq(rebalance_days=3)
    assert feed(s, bar(A, 0), bar(B, 0))  # initial
    for d in (1, 2):
        assert feed(s, bar(A, d), bar(B, d)) == []
    # simulate fills so the next rebalance has nothing to do except maybe drift
    assert s.drain_intents() == []
    called = []
    orig = s.should_rebalance
    s.should_rebalance = lambda b: (called.append(b.ts), orig(b))[1]
    s.on_bar(bar(A, 3))
    assert s._days_since_rebalance == 0  # rebalanced on day 3


def test_day_counter_ignores_intraday_bars_and_multiple_instruments():
    s = Eq(rebalance_days=2)
    feed(s, bar(A, 0), bar(B, 0))
    feed(s, bar(A, 0, offset=1), bar(B, 0, offset=2), bar(A, 1), bar(B, 1, offset=9))
    assert s._days_since_rebalance == 1


def test_day_from_bar_time_not_wall_clock(monkeypatch):
    import time

    monkeypatch.setattr(time, "time", lambda: 0.0)
    s = Eq(rebalance_days=1)
    feed(s, bar(A, 100), bar(B, 100))
    start = dt.date(1970, 1, 1) + dt.timedelta(days=101)
    assert start.year == 1970
    assert s._days_since_rebalance == 0
    s.on_bar(bar(A, 101))
    assert s._days_since_rebalance == 0  # rolled over and rebalanced (rebalance_days=1)


def test_exits_leavers_first_then_trims_then_buys():
    s = Eq(members=[A, B, C])
    hold(s, D, 50)  # leaver
    hold(s, A, 500)  # overweight -> trim
    hold(s, B, 10)  # underweight -> buy
    set_cash(s, 0.0)  # value = 5000 + 50000 + 1000 = 56000
    s.weights = {A: 0.3, B: 0.3, C: 0.3}
    out = feed(s, bar(D, 0), bar(A, 0), bar(B, 0), bar(C, 0))
    assert summary(out) == [
        ("D", "SELL", 50),
        ("A", "SELL", 500 - 164),
        ("B", "BUY", 164 - 10),
        ("C", "BUY", 164),
    ]


def test_zero_weight_member_is_exited():
    s = Eq(members=[A, B])
    hold(s, A, 100)
    set_cash(s, 10_000.0)
    s.weights = {A: 0.0, B: 0.5}
    out = feed(s, bar(A, 0), bar(B, 0))
    assert summary(out)[0] == ("A", "SELL", 100)


def test_deterministic_symbol_order():
    s = Eq(members=[D, B, C, A])
    out = feed(s, bar(D, 0), bar(B, 0), bar(C, 0), bar(A, 0))
    assert [i.instrument_id.symbol for i in out] == ["A", "B", "C", "D"]


def test_busy_instrument_skipped():
    s = Eq()
    s.buy(A, 1)  # unfilled -> busy
    s.drain_intents()
    out = feed(s, bar(A, 0), bar(B, 0))
    assert summary(out) == [("B", "BUY", 490)]


def test_busy_leaver_not_exited():
    s = Eq(members=[B])
    hold(s, A, 10)
    set_cash(s, 10_000.0)
    s.sell(A, 1)
    s.drain_intents()
    out = feed(s, bar(A, 0), bar(B, 0))
    assert all(i.instrument_id != A for i in out)


def test_missing_price_skipped():
    s = Eq(members=[A, B, C], rebalance_days=1)
    out = feed(s, bar(A, 0), bar(B, 0), bar(A, 1))
    assert {i.instrument_id for i in out} == {A, B}


def test_missing_price_position_excluded_from_value():
    s = Eq(members=[A])
    hold(s, C, 1000)  # no price for C -> not valued; leaver is still exited
    set_cash(s, 10_000.0)
    out = feed(s, bar(A, 0))
    assert summary(out) == [("C", "SELL", 1000), ("A", "BUY", 98)]


def test_difference_below_one_share_ignored():
    s = Eq(members=[A])
    hold(s, A, 980.5)  # target 980 shares; diff -0.5
    set_cash(s, 100_000.0 - 98_050.0)
    assert feed(s, bar(A, 0)) == []


def test_non_positive_portfolio_value_does_nothing():
    s = Eq(members=[A], cash=0.0)
    assert feed(s, bar(A, 0)) == []


def test_membership_change_logging(caplog):
    s = Eq(members=[A, B], rebalance_days=1)
    with caplog.at_level(logging.INFO, logger="honba.strategy.tw_eq"):
        feed(s, bar(A, 0), bar(B, 0))
        s.members = [B, C]
        feed(s, bar(C, 0), bar(A, 1))
    events = [
        (r.event_type, r.event_data["symbols"])
        for r in caplog.records
        if r.event_type.startswith("EVENT_MEMBERSHIP")
    ]
    assert events == [
        ("EVENT_MEMBERSHIP_ADD", ["A", "B"]),
        ("EVENT_MEMBERSHIP_ADD", ["C"]),
        ("EVENT_MEMBERSHIP_DEL", ["A"]),
    ]


def test_custom_should_rebalance_override():
    class Always(Eq):
        def should_rebalance(self, bar):
            return True

    s = Always()
    assert len(feed(s, bar(A, 0))) == 1  # B has no price yet
    assert s._days_since_rebalance == 0
