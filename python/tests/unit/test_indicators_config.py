"""Indicator configurability and parity with Jesse's compiled kernel (jesse-rust 1.2.0).

`tests/fixtures/jesse_golden.json` holds kernel outputs for several parameter sets;
regenerate with `tests/fixtures/gen_jesse_golden.py`.
"""
import json
from pathlib import Path

import pytest

from honba.strategies.indicators import (
    Atr, Bollinger, Ema, Kdj, Macd, Rsi, Sma, build_indicator, make_ma,
)

GOLDEN = json.loads((Path(__file__).parent.parent / "fixtures" / "jesse_golden.json").read_text())
H, L, C = GOLDEN["high"], GOLDEN["low"], GOLDEN["close"]


def _case_id(c):
    return c["kind"] + "-" + "-".join(f"{v}" for v in c["params"].values())


def _feed(kind, ind, h, l, c):
    """Yields one output dict (or None while warming up) per bar."""
    for hi, lo, cl in zip(h, l, c):
        if kind in ("sma", "ema", "rsi", "bollinger"):
            v = ind.update(cl)
        elif kind == "macd":
            v = ind.update(cl)
        elif kind == "atr":
            v = ind.update(hi, lo, cl)
        else:  # donchian, ichimoku
            v = ind.update(hi, lo)
        yield v


def _fields(kind, v):
    if v is None:
        return None
    if kind in ("sma", "ema", "rsi", "atr"):
        return {"value": v}
    if kind == "ichimoku":
        return {"span_a": v[0], "span_b": v[1]}
    return {k: getattr(v, k) for k in ("macd", "signal", "histogram", "upper", "middle", "lower")
            if hasattr(v, k)}


@pytest.mark.parametrize("case", [c for c in GOLDEN["cases"] if c["kind"] != "ichimoku"], ids=_case_id)
def test_matches_jesse_kernel(case):
    kind = case["kind"]
    ind = build_indicator(kind, **case["params"])
    got = [_fields(kind, v) for v in _feed(kind, ind, H, L, C)]
    for name, expected in case["out"].items():
        for i, exp in enumerate(expected):
            mine = None if got[i] is None else got[i][name]
            if exp is None:
                assert mine is None, f"{kind}.{name}[{i}] should still be warming up"
            else:
                assert mine is not None, f"{kind}.{name}[{i}] missing (jesse={exp})"
                assert mine == pytest.approx(exp, rel=1e-9, abs=1e-9), f"{kind}.{name}[{i}]"


@pytest.mark.parametrize("case", [c for c in GOLDEN["cases"] if c["kind"] == "ichimoku"], ids=_case_id)
def test_ichimoku_matches_jesse_kernel(case):
    ind = build_indicator("ichimoku", **case["params"])
    w = case["window"]
    last = None
    for v in _feed("ichimoku", ind, H[-w:], L[-w:], C[-w:]):
        last = v
    assert last is not None
    assert last[0] == pytest.approx(case["out"]["span_a"], abs=1e-9)
    assert last[1] == pytest.approx(case["out"]["span_b"], abs=1e-9)


# -- configurability ----------------------------------------------------------

def test_ema_seed_option():
    assert feed_all(Ema(3, seed="first"), [1, 2, 3, 4])[0] == 1
    assert feed_all(Ema(3, seed="sma"), [1, 2, 3, 4])[:2] == [None, None]
    with pytest.raises(ValueError):
        Ema(3, seed="bogus")


def feed_all(ind, xs):
    return [ind.update(x) for x in xs]


def test_atr_first_bar_option():
    with_first = Atr(2, include_first_bar=True)
    without = Atr(2)
    assert with_first.update(10, 8, 9) is None
    assert with_first.update(11, 9, 10) == pytest.approx(2.0)  # TRs 2 (high-low) and 2
    assert without.update(10, 8, 9) is None and without.update(11, 9, 10) is None


def test_bollinger_asymmetric_multipliers():
    b = Bollinger(3, mult=2.0, mult_lower=1.0)
    v = feed_all(b, [1, 2, 3])[-1]
    sd = (2 / 3) ** 0.5
    assert v.upper == pytest.approx(2 + 2 * sd) and v.lower == pytest.approx(2 - 1 * sd)


@pytest.mark.parametrize("kind,xs,expected", [
    ("sma", [1, 2, 3, 4], 3.0),
    ("wma", [1, 2, 3], 14 / 6),          # (1*1 + 2*2 + 3*3) / 6
    ("rma", [1, 2, 3, 4], 8 / 3),        # SMA seed 2, then (2*2 + 4) / 3
    ("ema", [1, 2, 3, 4], 3.0),          # SMA seed 2, alpha 0.5
])
def test_make_ma_kinds(kind, xs, expected):
    ma = make_ma(kind, 3)
    assert feed_all(ma, xs)[-1] == pytest.approx(expected)


def test_make_ma_rejects_unknown_kind():
    with pytest.raises(ValueError):
        make_ma("vwap", 3)


def test_kdj_smoothing_is_configurable():
    bars = [(5, 1, 3), (6, 2, 5), (7, 3, 4), (8, 4, 8), (9, 5, 6), (9, 3, 4)]
    sma_out = [Kdj(2, 3, 3).update(*b) for b in bars]  # fresh instances are not warm
    a, b = Kdj(2, 3, 3, slowk_ma="sma", slowd_ma="sma"), Kdj(2, 3, 3, slowk_ma="ema", slowd_ma="ema")
    ra = [a.update(*x) for x in bars]
    rb = [b.update(*x) for x in bars]
    assert ra[-1] is not None and rb[-1] is not None
    assert ra[-1] != pytest.approx(rb[-1])
    with pytest.raises(ValueError):
        Kdj(2, 3, 3, slowk_ma="nope")
    assert all(x is None for x in sma_out)


def test_macd_seed_reaches_first_bar():
    m = Macd(3, 6, 3, seed="first")
    assert m.update(10.0).macd == 0.0  # both EMAs equal the first price


def test_build_indicator_rejects_unknown_kind_and_params():
    with pytest.raises(ValueError):
        build_indicator("nope", period=3)
    with pytest.raises(TypeError):
        build_indicator("sma", period=3, bogus=1)


@pytest.mark.parametrize("kind,bad", [
    ("sma", {"period": 0}), ("ema", {"period": -1}), ("rsi", {"period": 0}), ("atr", {"period": 0}),
    ("bollinger", {"period": 0}), ("bollinger", {"period": 3, "mult": -1.0}),
    ("donchian", {"period": 0}), ("macd", {"fast": 26, "slow": 12}),
    ("ichimoku", {"tenkan": 0}), ("ichimoku", {"displacement": 0}), ("kdj", {"fastk": 0}),
])
def test_invalid_parameters_rejected(kind, bad):
    with pytest.raises(ValueError):
        build_indicator(kind, **bad)


@pytest.mark.parametrize("kind", ["sma", "ema", "rsi", "atr", "bollinger", "donchian", "macd", "ichimoku", "kdj"])
def test_every_indicator_builds_with_defaults(kind):
    assert build_indicator(kind) is not None


# -- families (TradingView-style taxonomy) -------------------------------------

def test_indicators_are_grouped_by_family():
    from honba.strategies.indicators import FAMILIES, indicator_family, list_indicators
    from honba.strategies.indicators import momentum, moving_average, trend, volatility

    assert FAMILIES == (
        "moving_average", "trend", "momentum", "volatility",
        "volume", "support_resistance", "breadth", "statistical",
    )
    assert moving_average.Sma is Sma and moving_average.make_ma is make_ma
    assert trend.Macd is Macd and momentum.Rsi is Rsi and momentum.Kdj is Kdj
    assert volatility.Atr is Atr and volatility.Bollinger is Bollinger
    assert indicator_family("sma") == "moving_average"
    assert indicator_family("macd") == "trend"
    assert indicator_family("ichimoku") == "trend"
    assert indicator_family("rsi") == "momentum"
    assert indicator_family("atr") == "volatility"
    assert {"ema", "rma", "sma", "wma", "hma", "dema"} <= set(list_indicators("moving_average"))
    assert {"ichimoku", "macd", "adx", "supertrend"} <= set(list_indicators("trend"))
    assert {"kdj", "rsi", "stochastic", "cci"} <= set(list_indicators("momentum"))
    assert {"atr", "bollinger", "donchian", "keltner"} <= set(list_indicators("volatility"))
    assert {"obv", "vwap", "money_flow_index"} <= set(list_indicators("volume"))
    assert {"pivot_points"} <= set(list_indicators("support_resistance"))
    assert {"trin", "advance_decline_line"} <= set(list_indicators("breadth"))
    assert {"beta", "zscore"} <= set(list_indicators("statistical"))
    assert all(list_indicators(f) for f in FAMILIES)  # every family has implementations
    assert len(list_indicators()) >= 74


def test_unknown_family_or_kind_rejected():
    from honba.strategies.indicators import indicator_family, list_indicators

    with pytest.raises(ValueError):
        list_indicators("astrology")
    with pytest.raises(ValueError):
        indicator_family("nope")
