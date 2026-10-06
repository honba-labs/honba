"""Settlement-cycle tests for the India market pack and StrategyConfig override."""

import importlib
from datetime import date, datetime, timezone

import pytest

from honba.markets.india import nse_equity_settlement_days, settlement_days_for
from honba.strategies.config import StrategyConfig

importlib.import_module("honba._honba")


def test_native_getter_reports_current_india_equity_t_plus_one():
    _honba = importlib.import_module("honba._honba")
    assert _honba.nse_equity_settlement_days() == 1


def test_native_getter_is_date_aware():
    _honba = importlib.import_module("honba._honba")
    assert _honba.nse_equity_settlement_days("2023-01-26") == 2
    assert _honba.nse_equity_settlement_days("2023-01-27") == 1


def test_native_getter_rejects_a_malformed_date():
    _honba = importlib.import_module("honba._honba")
    with pytest.raises(ValueError):
        _honba.nse_equity_settlement_days("26/01/2023")


def test_python_helper_matches_native_getter():
    _honba = importlib.import_module("honba._honba")
    assert nse_equity_settlement_days() == _honba.nse_equity_settlement_days()
    assert nse_equity_settlement_days(date(2020, 1, 1)) == 2


@pytest.mark.parametrize("exchange", ["NSE", "BSE", "nse"])
def test_india_exchanges_settle_t_plus_one_today(exchange):
    assert settlement_days_for(exchange) == 1


@pytest.mark.parametrize("exchange", ["NSE", "BSE", "nse"])
@pytest.mark.parametrize(
    ("as_of", "days"),
    [
        (date(2019, 6, 3), 2),
        ("2023-01-26", 2),
        (date(2023, 1, 26), 2),
        (datetime(2023, 1, 26, 15, 30, tzinfo=timezone.utc), 2),
        (date(2023, 1, 27), 1),
        ("2023-01-27", 1),
        (date(2025, 1, 1), 1),
    ],
)
def test_india_settlement_cycle_depends_on_the_date(exchange, as_of, days):
    assert settlement_days_for(exchange, as_of=as_of) == days


@pytest.mark.parametrize("exchange", ["NYSE", "BATS", "LSE"])
def test_other_markets_settle_t_plus_one(exchange):
    assert settlement_days_for(exchange) == 1
    assert settlement_days_for(exchange, as_of=date(2019, 1, 1)) == 1


def test_non_equity_kinds_are_resolvable():
    assert settlement_days_for("NSE", kind="future") == 1
    assert settlement_days_for("NSE", kind="future", as_of=date(2020, 1, 1)) == 2


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        settlement_days_for("NSE", kind="banana")


def test_strategy_config_round_trips_settlement_override():
    cfg = StrategyConfig(name="s", symbol="RELIANCE", settlement_days=1)
    assert cfg.settlement_days == 1
    assert cfg.model_dump()["settlement_days"] == 1
    assert StrategyConfig(name="s", symbol="RELIANCE").settlement_days is None
    assert StrategyConfig(name="s", symbol="RELIANCE").settlement_calendar is None


@pytest.mark.parametrize("bad", [-1, 6])
def test_strategy_config_rejects_out_of_range_settlement(bad):
    with pytest.raises(ValueError):
        StrategyConfig(name="s", symbol="RELIANCE", settlement_days=bad)


def test_strategy_config_loads_settlement_from_toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'name = "s"\nsymbol = "RELIANCE"\nsettlement_days = 3\nsettlement_calendar = "nse"\n'
    )
    cfg = StrategyConfig.from_toml(path)
    assert cfg.settlement_days == 3
    assert cfg.settlement_calendar == "nse"
