"""Unit tests for the portfolio-construction components (universe, selection, weighting,
schedule, market view). Pure: no engine, no I/O."""

from __future__ import annotations

import dataclasses
import datetime as dt

import pytest

from honba.entities.instrument import InstrumentId
from honba.markets.india.universes import UNIVERSES
from honba.strategies.portfolio import (
    EqualWeight,
    EveryNDays,
    MarketView,
    NamedUniverse,
    PriceHistory,
    ScheduleState,
    SelectAll,
    StaticUniverse,
)

A, B, C = (InstrumentId(s, "NSE") for s in "ABC")
EMPTY = MarketView(last_prices={}, closes={})


def state(**kw):
    base = {
        "bar_day": dt.date(2024, 1, 1),
        "first_rebalance_done": False,
        "trading_days_since_rebalance": 0,
        "all_members_priced": False,
        "drift": None,
    }
    base.update(kw)
    return ScheduleState(**base)


# -- universe --------------------------------------------------------------------------
def test_static_universe_keeps_order_and_dedups():
    assert list(StaticUniverse([B, A, B, C, A]).members()) == [B, A, C]


def test_static_universe_ignores_as_of():
    u = StaticUniverse([A, B])
    assert list(u.members(dt.date(2020, 1, 1))) == [A, B]


def test_static_universe_is_mutable_via_set_members():
    u = StaticUniverse([A])
    u.set_members([B, C, B])
    assert list(u.members()) == [B, C]


def test_named_universe_resolves_through_engine(monkeypatch):
    monkeypatch.setitem(UNIVERSES, "pf_test", ("X", "Y"))
    assert list(NamedUniverse("pf_test").members()) == [
        InstrumentId("X", "NSE"),
        InstrumentId("Y", "NSE"),
    ]


def test_named_universe_exchange(monkeypatch):
    monkeypatch.setitem(UNIVERSES, "pf_test", ("X",))
    assert list(NamedUniverse("pf_test", exchange="BSE").members()) == [InstrumentId("X", "BSE")]


def test_named_universe_unknown_name_raises_clear_error():
    with pytest.raises(ValueError, match="no_such_universe"):
        NamedUniverse("no_such_universe").members()


def test_named_universe_ignores_as_of_by_default(monkeypatch):
    monkeypatch.setitem(UNIVERSES, "pf_test", ("X",))
    assert list(NamedUniverse("pf_test").members(dt.date(2020, 1, 1))) == [InstrumentId("X", "NSE")]


def test_named_universe_point_in_time_requires_history(monkeypatch):
    monkeypatch.setitem(UNIVERSES, "pf_test", ("X",))
    u = NamedUniverse("pf_test", point_in_time=True)
    with pytest.raises(ValueError, match="point-in-time"):
        u.members(dt.date(2020, 1, 1))
    assert list(u.members(None)) == [InstrumentId("X", "NSE")]


# -- selection -------------------------------------------------------------------------
def test_select_all_returns_members_in_order():
    assert list(SelectAll().select([C, A, B], EMPTY)) == [C, A, B]
    assert list(SelectAll().select([], EMPTY)) == []


# -- weighting -------------------------------------------------------------------------
def test_equal_weight():
    assert dict(EqualWeight().weights([A, B, C, A], EMPTY)) == {A: 1 / 3, B: 1 / 3, C: 1 / 3}


def test_equal_weight_empty():
    assert dict(EqualWeight().weights([], EMPTY)) == {}


# -- schedule --------------------------------------------------------------------------
def test_every_n_days_validates():
    for bad in (0, -1):
        with pytest.raises(ValueError, match="n must be >= 1"):
            EveryNDays(bad)


def test_state_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        state().drift = 1.0  # type: ignore[misc]


def test_first_rebalance_when_all_members_priced():
    assert EveryNDays(5).due(state(all_members_priced=True))


def test_first_rebalance_waits_without_prices_on_same_day():
    assert not EveryNDays(5).due(state())


def test_first_rebalance_on_day_rollover_even_if_unpriced():
    assert EveryNDays(5).due(state(trading_days_since_rebalance=1))


def test_after_first_every_n_days():
    s = EveryNDays(3)
    done = {"first_rebalance_done": True, "all_members_priced": True}
    assert not s.due(state(trading_days_since_rebalance=2, **done))
    assert s.due(state(trading_days_since_rebalance=3, **done))
    assert s.due(state(trading_days_since_rebalance=4, **done))


# -- market view -----------------------------------------------------------------------
def test_market_view_accessors_and_read_only():
    v = MarketView(last_prices={A: 10.0}, closes={A: (9.0, 10.0)})
    assert v.price(A) == 10.0 and v.price(B) is None
    assert v.recent_closes(A) == (9.0, 10.0) and v.recent_closes(B) == ()
    with pytest.raises(TypeError):
        v.last_prices[A] = 1.0  # type: ignore[index]
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.last_prices = {}  # type: ignore[misc]


def test_price_history_ring_buffer_bound():
    h = PriceHistory(maxlen=3)
    for i in range(1, 6):
        h.record(A, float(i))
    h.record(B, 7.0)
    v = h.view({A: 5.0, B: 7.0})
    assert v.recent_closes(A) == (3.0, 4.0, 5.0)
    assert v.recent_closes(B) == (7.0,)
    assert v.price(A) == 5.0


def test_price_history_view_is_a_snapshot():
    h = PriceHistory(maxlen=3)
    h.record(A, 1.0)
    v = h.view({A: 1.0})
    h.record(A, 2.0)
    assert v.recent_closes(A) == (1.0,)


def test_price_history_validates_maxlen():
    with pytest.raises(ValueError, match="maxlen must be >= 1"):
        PriceHistory(maxlen=0)
