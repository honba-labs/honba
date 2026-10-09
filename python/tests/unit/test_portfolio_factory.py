"""Unit tests for the config-driven portfolio factory (plain TOML-friendly params)."""

from __future__ import annotations

import pytest

from honba.entities.instrument import InstrumentId
from honba.markets.india.universes import UNIVERSES
from honba.strategies import PortfolioStrategy
from honba.strategies.portfolio import (
    AnyOf,
    DriftBand,
    EqualWeight,
    EveryNDays,
    InverseVolatility,
    MonthlyFirstSession,
    NamedUniverse,
    SelectAll,
    StaticUniverse,
    TopN,
    build_portfolio_strategy,
)

BASE = {"universe": ["AAA", "BBB"]}


def build(**kw):
    return build_portfolio_strategy({**BASE, **kw})


def test_minimal_params_use_defaults():
    s = build()
    assert isinstance(s, PortfolioStrategy) and s.name == "portfolio"
    assert isinstance(s.universe_source, StaticUniverse)
    assert isinstance(s.weighting, EqualWeight) and isinstance(s.selector, SelectAll)
    assert isinstance(s.schedule, EveryNDays) and s.schedule.n == 15
    assert s.allocation == 0.98


def test_universe_list_symbols_and_exchange_forms():
    s = build(universe=["AAA", "BBB:BSE", "CCC"], exchange="NFO")
    assert list(s.universe_source.members()) == [
        InstrumentId("AAA", "NFO"),
        InstrumentId("BBB", "BSE"),
        InstrumentId("CCC", "NFO"),
    ]
    assert next(iter(build().universe_source.members())) == InstrumentId("AAA", "NSE")


def test_universe_name_gives_named_universe(monkeypatch):
    monkeypatch.setitem(UNIVERSES, "fx3", ("AAA", "BBB", "CCC"))
    s = build_portfolio_strategy({"universe": "fx3", "point_in_time": True, "exchange": "BSE"})
    u = s.universe_source
    assert isinstance(u, NamedUniverse)
    assert (u.name, u.exchange, u.point_in_time) == ("fx3", "BSE", True)
    assert build_portfolio_strategy({"universe": "fx3"}).universe_source.point_in_time is False


def test_name_argument_sets_strategy_name_and_from_params_delegates():
    s = PortfolioStrategy.from_params(BASE, name="my_book")
    assert isinstance(s, PortfolioStrategy) and s.name == "my_book"
    assert PortfolioStrategy.from_params(BASE).name == "portfolio"


def test_weighting_forms():
    assert isinstance(build(weighting="equal").weighting, EqualWeight)
    assert build(weighting="inverse_vol").weighting.lookback == 20
    assert build(weighting="inverse_vol:30").weighting.lookback == 30
    assert isinstance(build(weighting="inverse_vol:30").weighting, InverseVolatility)


def test_schedule_forms():
    assert build(schedule="every:7d").schedule.n == 7
    assert isinstance(build(schedule="monthly:first_session").schedule, MonthlyFirstSession)
    d = build(schedule="drift:0.05").schedule
    assert isinstance(d, DriftBand) and d.tolerance == 0.05


def test_schedule_combined_with_plus():
    s = build(schedule="monthly:first_session+drift:0.05").schedule
    assert isinstance(s, AnyOf) and s.needs_drift
    assert [type(x) for x in s.schedules] == [MonthlyFirstSession, DriftBand]
    assert build(schedule=" every:3d + drift:0.1 ").schedule.needs_drift


def test_select_forms():
    s = build(select="top:10:momentum:126").selector
    assert isinstance(s, TopN) and s.n == 10 and s.descending
    assert s.score.lookback == 126
    assert build(select="top:3:low_vol:60").selector.score.lookback == 60


def test_allocation_float_and_int():
    assert build(allocation=0.9).allocation == 0.9
    assert build(allocation=1).allocation == 1.0


def test_history_len_auto_sizes_to_longest_lookback_with_floor_64():
    assert build()._history.maxlen == 64
    assert build(weighting="inverse_vol:20")._history.maxlen == 64
    assert build(select="top:2:momentum:126")._history.maxlen == 126
    assert build(weighting="inverse_vol:200", select="top:2:low_vol:100")._history.maxlen == 200


def test_history_len_explicit_ok_or_too_small_raises():
    assert build(history_len=30)._history.maxlen == 30
    assert build(history_len=126, select="top:2:momentum:126")._history.maxlen == 126
    with pytest.raises(ValueError, match="history_len"):
        build(history_len=50, select="top:2:momentum:126")
    with pytest.raises(ValueError, match="history_len"):
        build(history_len=10, weighting="inverse_vol:20")


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("weighting", "risk_parity"),
        ("weighting", "inverse_vol:abc"),
        ("weighting", "inverse_vol:1"),
        ("weighting", "inverse_vol:20:5"),
        ("weighting", "equal:3"),
        ("weighting", 3),
        ("schedule", "every:0d"),
        ("schedule", "every:15"),
        ("schedule", "every:xd"),
        ("schedule", "monthly:last_session"),
        ("schedule", "monthly"),
        ("schedule", "drift:abc"),
        ("schedule", "drift:0"),
        ("schedule", "drift:-0.1"),
        ("schedule", "daily"),
        ("schedule", "every:3d+"),
        ("schedule", ""),
        ("schedule", 5),
        ("select", "top:10"),
        ("select", "top:0:momentum:5"),
        ("select", "top:x:momentum:5"),
        ("select", "top:3:value:5"),
        ("select", "top:3:momentum:abc"),
        ("select", "top:3:momentum:1"),
        ("select", "bottom:3:momentum:5"),
        ("select", "top:3:momentum:5:6"),
        ("allocation", 0),
        ("allocation", 1.5),
        ("allocation", "high"),
        ("allocation", True),
        ("history_len", 0),
        ("history_len", "64"),
        ("history_len", 2.5),
        ("history_len", True),
        ("point_in_time", "yes"),
        ("exchange", ""),
        ("exchange", 3),
    ],
)
def test_malformed_values_name_key_and_value(key, value):
    with pytest.raises(ValueError, match=key) as exc:
        build(**{key: value})
    assert repr(value) in str(exc.value) or str(value) in str(exc.value)


@pytest.mark.parametrize(
    "universe", [[], "", ["AAA", ""], ["AAA:"], [":NSE"], ["A:B:C"], ["AAA", 3], 5, None]
)
def test_malformed_universe(universe):
    with pytest.raises(ValueError, match="universe"):
        build_portfolio_strategy({"universe": universe})


def test_missing_universe_and_unknown_keys_rejected():
    with pytest.raises(ValueError, match="universe"):
        build_portfolio_strategy({})
    with pytest.raises(ValueError, match="rebalance"):
        build(rebalance="monthly")
    with pytest.raises(ValueError, match="weigthing"):
        build(weigthing="equal")


def test_invalid_blank_name_propagates():
    with pytest.raises(ValueError, match="name"):
        build_portfolio_strategy(BASE, name=" ")
