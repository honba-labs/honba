"""Config-driven indicators: StrategyConfig [indicators.*] tables -> IndicatorBank."""

import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.strategies.config import StrategyConfig
from honba.strategies.indicators import Adx, IndicatorBank, Supertrend, Vwap, build_indicator

IID = InstrumentId("NIFTY50", "NSE")


def bars(n=60):
    out = []
    for i in range(n):
        c = 100 + i * 0.5 + (3 if i % 5 == 0 else 0)
        out.append(Bar(IID, (i + 1) * 3_600_000_000_000, c, c + 1, c - 1, c, 1000.0 + i))
    return out


SPECS = {
    "fast": {"kind": "sma", "period": 3},
    "slow": {"kind": "ema", "period": 5, "seed": "first"},
    "trend": {"kind": "supertrend", "atr_period": 4, "factor": 2.0},
}


def test_flat_imports_reach_new_indicators():
    assert Adx.kind == "adx" and Supertrend.kind == "supertrend" and Vwap.kind == "vwap"
    with pytest.raises(ImportError):
        from honba.strategies.indicators import NotAnIndicator  # noqa: F401


def test_bank_builds_from_specs_and_feeds_bars():
    bank = IndicatorBank(SPECS)
    assert bank.names == ["fast", "slow", "trend"]
    assert not bank.ready
    out = None
    for b in bars():
        out = bank.update(b)
    assert bank.ready
    assert set(out) == {"fast", "slow", "trend"}
    assert out["fast"] == bank["fast"] == pytest.approx(sum(b.close for b in bars()[-3:]) / 3)


def test_bank_matches_standalone_indicators():
    bank = IndicatorBank(SPECS)
    solo = build_indicator("supertrend", atr_period=4, factor=2.0)
    for b in bars():
        got = bank.update(b)["trend"]
        want = solo.update_bar(b)
        assert got == want


def test_none_until_each_indicator_warms_up():
    bank = IndicatorBank(SPECS)
    first = bank.update(bars(1)[0])
    assert first["fast"] is None and first["slow"] is not None  # ema seed="first" is valid at bar 1


def test_reset_restarts_all_indicators():
    bank = IndicatorBank(SPECS)
    a = [bank.update(b) for b in bars()]
    bank.reset()
    assert not bank.ready and [bank.update(b) for b in bars()] == a


def test_spec_errors_are_clear():
    with pytest.raises(ValueError, match="kind"):
        IndicatorBank({"x": {"period": 3}})
    with pytest.raises(ValueError, match="nope"):
        IndicatorBank({"x": {"kind": "nope"}})
    with pytest.raises(TypeError):
        IndicatorBank({"x": {"kind": "sma", "bogus": 1}})
    with pytest.raises(ValueError, match="benchmark"):
        IndicatorBank({"x": {"kind": "beta"}})  # needs a non-Bar input


def test_strategy_config_reads_indicator_tables(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        'name = "demo"\nsymbol = "RELIANCE"\n\n[params]\ncapital = 1000\n\n'
        '[indicators.fast]\nkind = "sma"\nperiod = 3\n\n[indicators.trend]\nkind = "supertrend"\nfactor = 2.5\n'
    )
    cfg = StrategyConfig.from_toml(p)
    assert cfg.indicators["trend"] == {"kind": "supertrend", "factor": 2.5}
    bank = IndicatorBank(cfg.indicators)
    assert bank.names == ["fast", "trend"]
    assert StrategyConfig(name="x", symbol="Y").indicators == {}
