"""Unit tests: inverse-vol weighting, monthly/drift/any-of schedules, TopN, scoring."""

from __future__ import annotations

import datetime as dt
import math
import statistics
from itertools import pairwise

import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.strategies import PortfolioStrategy
from honba.strategies.context import LedgerContext
from honba.strategies.portfolio import (
    AnyOf,
    DriftBand,
    EveryNDays,
    InverseVolatility,
    MarketView,
    MonthlyFirstSession,
    RebalanceSchedule,
    ScheduleState,
    StaticUniverse,
    TopN,
    low_volatility,
    momentum,
)

DAY = 86_400 * 10**9
A, B, C = (InstrumentId(s, "NSE") for s in "ABC")


def view(closes):
    return MarketView(last_prices={i: c[-1] for i, c in closes.items() if c}, closes=closes)


def state(**kw):
    base = {
        "bar_day": dt.date(2024, 1, 1),
        "first_rebalance_done": True,
        "trading_days_since_rebalance": 1,
        "all_members_priced": False,
        "drift": None,
        "previous_bar_day": dt.date(2023, 12, 31),
    }
    base.update(kw)
    return ScheduleState(**base)


def sample_vol(closes):
    rets = [b / a - 1 for a, b in pairwise(closes)]
    return statistics.stdev(rets)


# -- InverseVolatility -----------------------------------------------------------------
CALM = (100.0, 101.0, 100.0, 101.0, 100.0)
WILD = (100.0, 110.0, 99.0, 112.0, 98.0)


def test_inverse_vol_lower_weight_for_more_volatile_and_sums_to_one():
    w = InverseVolatility(5).weights([A, B], view({A: CALM, B: WILD}))
    assert w[A] > w[B]
    assert math.isclose(sum(w.values()), 1.0)
    expected = (1 / sample_vol(CALM)) / (1 / sample_vol(CALM) + 1 / sample_vol(WILD))
    assert math.isclose(w[A], expected)


def test_inverse_vol_uses_only_last_lookback_closes():
    long_a = (500.0, 1.0) + CALM
    w = InverseVolatility(5).weights([A, B], view({A: long_a, B: WILD}))
    assert math.isclose(w[A], InverseVolatility(5).weights([A, B], view({A: CALM, B: WILD}))[A])


def test_inverse_vol_insufficient_history_gets_mean_of_valid_inverse_vols():
    w = InverseVolatility(5).weights([A, B, C], view({A: CALM, B: WILD, C: (100.0, 101.0)}))
    inv_a, inv_b = 1 / sample_vol(CALM), 1 / sample_vol(WILD)
    inv_c = (inv_a + inv_b) / 2
    assert math.isclose(w[C], inv_c / (inv_a + inv_b + inv_c))
    assert math.isclose(sum(w.values()), 1.0)


def test_inverse_vol_zero_vol_and_nan_fall_back_to_mean():
    flat = (100.0,) * 5
    bad = (100.0, 0.0, 100.0, 100.0, 100.0)  # zero previous close -> invalid
    w = InverseVolatility(5).weights([A, B, C], view({A: CALM, B: flat, C: bad}))
    assert math.isclose(w[A], w[B]) and math.isclose(w[A], w[C])


def test_inverse_vol_no_valid_vol_is_equal_weight():
    w = InverseVolatility(5).weights([A, B], view({A: (1.0,), B: (100.0,) * 5}))
    assert w == {A: 0.5, B: 0.5}


def test_inverse_vol_dedups_and_handles_empty():
    iv = InverseVolatility(5)
    assert iv.weights([], view({})) == {}
    assert set(iv.weights([A, A], view({A: CALM}))) == {A}


@pytest.mark.parametrize("bad", [1, 0, -3])
def test_inverse_vol_validates_lookback(bad):
    with pytest.raises(ValueError, match="lookback"):
        InverseVolatility(bad)


def test_inverse_vol_default_lookback_is_20():
    assert InverseVolatility().lookback == 20


# -- schedules -------------------------------------------------------------------------
def test_monthly_initial_rebalance_matches_every_n_days():
    m, e = MonthlyFirstSession(), EveryNDays(5)
    for kw in (
        {"first_rebalance_done": False, "all_members_priced": True},
        {"first_rebalance_done": False, "trading_days_since_rebalance": 1},
        {"first_rebalance_done": False, "trading_days_since_rebalance": 0},
    ):
        assert m.due(state(**kw)) == e.due(state(**kw))


def test_monthly_due_only_on_first_bar_of_new_month():
    m = MonthlyFirstSession()
    jan = dt.date(2024, 1, 31)
    assert m.due(state(bar_day=dt.date(2024, 2, 1), previous_bar_day=jan))
    assert not m.due(state(bar_day=dt.date(2024, 1, 31), previous_bar_day=dt.date(2024, 1, 30)))
    # second bar of the same rollover day: a rebalance already reset the counter
    assert not m.due(
        state(bar_day=dt.date(2024, 2, 1), previous_bar_day=jan, trading_days_since_rebalance=0)
    )


def test_monthly_year_rollover_and_same_month_other_year():
    m = MonthlyFirstSession()
    assert m.due(state(bar_day=dt.date(2025, 1, 2), previous_bar_day=dt.date(2024, 12, 31)))
    assert m.due(state(bar_day=dt.date(2025, 1, 2), previous_bar_day=dt.date(2024, 1, 31)))


def test_monthly_unknown_previous_day_is_not_due():
    assert not MonthlyFirstSession().due(state(previous_bar_day=None))


def test_drift_band_due_at_or_above_tolerance():
    d = DriftBand(0.05)
    assert d.needs_drift is True
    assert d.due(state(drift=0.05)) and d.due(state(drift=0.2))
    assert not d.due(state(drift=0.049))
    assert not d.due(state(drift=None))


def test_drift_band_not_due_on_the_day_of_the_last_rebalance():
    d = DriftBand(0.05)
    assert not d.due(state(drift=0.5, trading_days_since_rebalance=0))


def test_drift_band_initial_rebalance():
    d = DriftBand(0.05)
    assert d.due(state(first_rebalance_done=False, all_members_priced=True))
    assert not d.due(state(first_rebalance_done=False, trading_days_since_rebalance=0))


@pytest.mark.parametrize("bad", [0.0, -0.1, float("nan"), float("inf")])
def test_drift_band_validates_tolerance(bad):
    with pytest.raises(ValueError, match="tolerance"):
        DriftBand(bad)


def test_needs_drift_defaults_false():
    assert EveryNDays().needs_drift is False
    assert MonthlyFirstSession().needs_drift is False


def test_any_of_due_if_any_due_and_propagates_needs_drift():
    a = AnyOf(MonthlyFirstSession(), DriftBand(0.05))
    assert a.needs_drift is True
    assert a.due(state(drift=0.1))
    assert a.due(state(bar_day=dt.date(2024, 2, 1), previous_bar_day=dt.date(2024, 1, 31)))
    assert not a.due(state(drift=0.01, previous_bar_day=dt.date(2024, 1, 1)))
    assert AnyOf(EveryNDays(3), MonthlyFirstSession()).needs_drift is False
    assert isinstance(a, RebalanceSchedule)


def test_any_of_evaluates_every_child_and_requires_one():
    seen = []

    class Spy:
        needs_drift = False

        def due(self, st):
            seen.append(1)
            return True

    assert AnyOf(Spy(), Spy()).due(state())
    assert len(seen) == 2
    with pytest.raises(ValueError, match="schedule"):
        AnyOf()


# -- scoring ---------------------------------------------------------------------------
def test_momentum_is_total_return_over_lookback_closes():
    f = momentum(3)
    assert math.isclose(f(A, view({A: (1.0, 100.0, 110.0, 121.0)})), 0.21)
    assert f(A, view({A: (100.0, 110.0)})) is None
    assert f(A, view({})) is None
    assert f.lookback == 3


def test_momentum_nonpositive_base_is_none_and_validates():
    assert momentum(2)(A, view({A: (0.0, 5.0)})) is None
    with pytest.raises(ValueError, match="lookback"):
        momentum(1)


def test_low_volatility_is_negative_stdev():
    f = low_volatility(5)
    assert math.isclose(f(A, view({A: CALM})), -sample_vol(CALM))
    assert f(A, view({A: CALM[:3]})) is None
    assert f(A, view({A: (0.0, 1.0, 2.0, 3.0, 4.0)})) is None
    assert f.lookback == 5
    with pytest.raises(ValueError, match="lookback"):
        low_volatility(1)


# -- TopN ------------------------------------------------------------------------------
def fixed(scores):
    return lambda iid, v: scores.get(iid)


def test_top_n_descending_by_default_and_ascending_option():
    v = view({})
    s = fixed({A: 1.0, B: 3.0, C: 2.0})
    assert list(TopN(2, s).select([A, B, C], v)) == [B, C]
    assert list(TopN(2, s, descending=False).select([A, B, C], v)) == [A, C]


def test_top_n_drops_none_and_nan_scores():
    s = fixed({A: None, B: 1.0, C: float("nan")})
    assert list(TopN(3, s).select([A, B, C], view({}))) == [B]
    assert list(TopN(3, s).select([], view({}))) == []


def test_top_n_ties_break_by_symbol_then_exchange():
    bse = InstrumentId("A", "BSE")
    s = fixed({C: 1.0, B: 1.0, A: 1.0, bse: 1.0})
    for order in ([C, bse, B, A], [A, B, bse, C]):
        assert list(TopN(4, s).select(order, view({}))) == [bse, A, B, C]
        assert list(TopN(4, s, descending=False).select(order, view({}))) == [bse, A, B, C]


def test_top_n_validates_n_and_passes_view():
    with pytest.raises(ValueError, match="n must"):
        TopN(0, fixed({}))
    v = view({A: (1.0,)})
    got = []
    TopN(1, lambda i, vw: got.append(vw) or 1.0).select([A], v)
    assert got == [v]


# -- strategy drift --------------------------------------------------------------------
def bar(iid, day, close=100.0):
    return Bar(iid, day * DAY, close, close, close, close, 1000.0)


class Recorder:
    def __init__(self, needs):
        self.needs_drift = needs
        self.states = []

    def due(self, st):
        self.states.append(st)
        return len(self.states) == 2  # rebalance on the second bar


def drift_strategy(needs, **kw):
    rec = Recorder(needs)
    s = PortfolioStrategy(StaticUniverse([A, B]), schedule=rec, allocation=1.0, **kw)
    s.bind(LedgerContext(cash=100_000.0))
    return s, rec


def test_drift_not_computed_unless_schedule_needs_it():
    s, rec = drift_strategy(False)
    for b in (bar(A, 1), bar(B, 1), bar(A, 2), bar(B, 2)):
        s.on_bar(b)
    assert all(st.drift is None for st in rec.states)


def test_drift_is_none_before_first_rebalance_and_zero_when_on_target():
    s, rec = drift_strategy(True)
    s.on_bar(bar(A, 1))
    assert rec.states[0].drift is None
    s.on_bar(bar(B, 1))  # second call -> rebalance
    s.drain_intents()
    from honba.entities.order import OrderSide
    from honba.entities.trade import Trade

    s.ctx.apply_fill(Trade(A, OrderSide.BUY, 500, 100.0, 1))
    s.ctx.apply_fill(Trade(B, OrderSide.BUY, 500, 100.0, 1))
    s.on_bar(bar(A, 2))
    assert rec.states[-1].drift == pytest.approx(0.0)


def test_drift_is_max_abs_weight_gap_on_cash_inclusive_value():
    from honba.entities.order import OrderSide
    from honba.entities.trade import Trade

    s, rec = drift_strategy(True)
    s.on_bar(bar(A, 1))
    s.on_bar(bar(B, 1))
    s.drain_intents()
    s.ctx.apply_fill(Trade(A, OrderSide.BUY, 500, 100.0, 1))  # cash 50k, A 50k, B none
    s.on_bar(bar(A, 2))
    # value 100k; A weight .5 vs target .5; B weight 0 vs target .5 -> drift .5
    assert rec.states[-1].drift == pytest.approx(0.5)


def test_schedule_state_carries_previous_bar_day():
    s, rec = drift_strategy(False)
    s.on_bar(bar(A, 19_000))
    s.on_bar(bar(B, 19_000))
    s.on_bar(bar(A, 19_001))
    assert [st.previous_bar_day for st in rec.states] == [
        None,
        None,
        dt.date(2022, 1, 8),
    ]


def test_initial_rebalance_waits_until_selector_keeps_something():
    # TopN(momentum 3) has no score until 3 closes exist: no empty "initial" rebalance.
    s = PortfolioStrategy(
        StaticUniverse([A]),
        schedule=EveryNDays(5),
        selector=TopN(1, momentum(3)),
        allocation=1.0,
    )
    s.bind(LedgerContext(cash=100_000.0))
    out = []
    for d in (1, 2, 3):
        s.on_bar(bar(A, d))
        out.append(list(s.drain_intents()))
    assert out[0] == [] and out[1] == []
    assert [(i.side.name, i.quantity) for i in out[2]] == [("BUY", 1000)]
