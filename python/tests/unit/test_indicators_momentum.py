"""Momentum-family indicators: hand-checked values, validation, configurability, kernel golden data.

Golden data (stochastic, williams_r, cci, cmo) comes from Jesse's kernel, verified equal to the
TradingView/textbook definitions. The others (stoch_rsi, tsi, ao, uo, fisher, kst, coppock,
connors_rsi, rvgi, roc, momentum) differ from or are absent in Jesse, so they rely on hand-checked
textbook arithmetic below.
"""

import inspect
import json
import math
import random
from pathlib import Path

import pytest

from honba.strategies.indicators import build_indicator

GOLDEN = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "jesse_golden_momentum.json").read_text()
)


def feed(kind, rows, **params):
    ind = build_indicator(kind, **params)
    return [ind.update(*r) for r in rows]


def last(kind, rows, **params):
    return feed(kind, rows, **params)[-1]


def approx(a, b):
    return a == pytest.approx(b, rel=1e-9, abs=1e-9)


def ohlc(n=120, seed=3):
    rnd = random.Random(seed)
    c, rows = 100.0, []
    for _ in range(n):
        o = c + rnd.uniform(-1, 1)
        c = o + rnd.uniform(-2, 2)
        rows.append((o, max(o, c) + rnd.random(), min(o, c) - rnd.random(), c))
    return rows


DATA = ohlc()
TOL = {"open": 0, "high": 1, "low": 2, "close": 3}


def rows_for(kind):
    ins = build_indicator(kind).inputs
    return [tuple(r[TOL[i]] for i in ins) for r in DATA]


# ---------- hand-verifiable values ----------


def test_stochastic_hand():
    # window of 3: hh=12, ll=7, close=10 -> 100*(10-7)/(12-7)=60
    out = last(
        "stochastic", [(10, 8, 9), (12, 9, 11), (11, 7, 10)], k_length=3, k_smooth=1, d_smooth=1
    )
    assert approx(out.k, 60.0) and approx(out.d, 60.0)


def test_stochastic_flat_window_is_zero():
    out = last("stochastic", [(5, 5, 5)] * 3, k_length=3, k_smooth=1, d_smooth=1)
    assert out.k == 0.0


def test_stoch_rsi_hand():
    # RSI(2) on 10,11,10,11,10: u3 50 (gain .5/loss .5); u4 gain .75/loss .25 -> 75; u5 gain .375/loss .625 -> 37.5
    # stoch window 2: u4 [50,75] cur 75 -> 100 ; u5 [75,37.5] cur 37.5 -> 0
    outs = feed(
        "stoch_rsi",
        [(x,) for x in (10, 11, 10, 11, 10)],
        rsi_length=2,
        stoch_length=2,
        k_smooth=1,
        d_smooth=1,
    )
    assert outs[:3] == [None] * 3
    assert approx(outs[3].k, 100.0) and approx(outs[4].k, 0.0)


def test_williams_r_hand():
    # hh=12, ll=7, close=10 -> 100*(10-12)/5 = -40
    assert approx(last("williams_r", [(10, 8, 9), (12, 9, 11), (11, 7, 10)], length=3), -40.0)
    assert last("williams_r", [(5, 5, 5)] * 3, length=3) == -50.0


def test_cci_hand():
    # tp 10, 14: mean 12, MAD 2 -> (14-12)/(0.015*2) = 66.666...
    assert approx(last("cci", [(10, 10, 10), (14, 14, 14)], length=2), 2 / 0.03)
    assert last("cci", [(3, 3, 3)] * 2, length=2) == 0.0


def test_roc_and_momentum_hand():
    # 10,12,15 with length 2: roc = 100*(15-10)/10 = 50 ; momentum = 15-10 = 5
    assert approx(last("roc", [(10,), (12,), (15,)], length=2), 50.0)
    assert approx(last("momentum", [(10,), (12,), (15,)], length=2), 5.0)
    assert last("roc", [(0,), (1,)], length=1) == 0.0  # zero base convention


def test_tsi_hand():
    # strictly +1 per bar: dx=1 always, EMA ratio = 1 -> tsi = 100, signal 100; down-trend -> -100
    up = last(
        "tsi", [(float(i),) for i in range(20)], long_length=3, short_length=2, signal_length=2
    )
    assert approx(up.tsi, 100.0) and approx(up.signal, 100.0)
    dn = last(
        "tsi", [(float(-i),) for i in range(20)], long_length=3, short_length=2, signal_length=2
    )
    assert approx(dn.tsi, -100.0)
    assert last("tsi", [(5.0,)] * 20, long_length=3, short_length=2, signal_length=2).tsi == 0.0


def test_awesome_oscillator_hand():
    # medians 1,3,5,7 ; SMA2 = 6, SMA3 = 5 -> 1
    rows = [(2, 0), (4, 2), (6, 4), (8, 6)]
    assert approx(last("awesome_oscillator", rows, fast=2, slow=3), 1.0)


def test_ultimate_oscillator_hand():
    # bars (h,l,c): (10,8,9) skipped; (11,9,10) bp1 tr2; (12,10,11) bp1 tr2; (14,11,13) bp2 tr3
    # fast1: 2/3 ; middle2: (1+2)/(2+3)=3/5 ; slow3: 4/7 -> 100*(4*2/3 + 2*3/5 + 4/7)/7
    rows = [(10, 8, 9), (11, 9, 10), (12, 10, 11), (14, 11, 13)]
    exp = 100 * (4 * 2 / 3 + 2 * 3 / 5 + 4 / 7) / 7
    assert approx(last("ultimate_oscillator", rows, fast=1, middle=2, slow=3), exp)


def test_fisher_transform_hand():
    # length 1: range 0 -> denominator 0.001; v1 = .66*(0-.5) = -.33 ; f1 = .5*ln(.67/1.33), trigger 0
    # v2 = -.33 + .67*(-.33) = -.5511 ; f2 = .5*ln((1-.5511)/(1.5511)) + .5*f1 ; trigger2 = f1
    o1, o2 = feed("fisher_transform", [(10, 10), (10, 10)], length=1)
    f1 = 0.5 * math.log(0.67 / 1.33)
    v2 = -0.33 + 0.67 * -0.33
    f2 = 0.5 * math.log((1 + v2) / (1 - v2)) + 0.5 * f1
    assert approx(o1.fisher, f1) and o1.trigger == 0.0
    assert approx(o2.fisher, f2) and approx(o2.trigger, f1)


def test_kst_hand():
    # all lengths 1: kst = (1+2+3+4)*ROC1 = 10 * 10% = 100 ; signal SMA1 = 100
    out = last(
        "kst",
        [(100,), (110,)],
        roc1=1,
        roc2=1,
        roc3=1,
        roc4=1,
        sma1=1,
        sma2=1,
        sma3=1,
        sma4=1,
        signal=1,
    )
    assert approx(out.kst, 100.0) and approx(out.signal, 100.0)


def test_coppock_hand():
    # 100,110,121: ROC(2)=21, ROC(1)=10 -> WMA(1) = 31
    assert approx(
        last("coppock_curve", [(100,), (110,), (121,)], wma_length=1, long_roc=2, short_roc=1), 31.0
    )


def test_cmo_hand():
    # changes +2, -1: 100*(2-1)/(2+1) = 33.33
    assert approx(last("cmo", [(10,), (12,), (11,)], length=2), 100 / 3)
    assert last("cmo", [(5,)] * 4, length=2) == 0.0


def test_connors_rsi_hand():
    # closes 10,11,12,11 with (2,2,2): RSI(2)=50 ; streaks +1,+2,-1 -> RSI(2)=25 ;
    # ROC 10, 9.09, -8.33: rank of -8.33 among previous two = 0 -> (50+25+0)/3 = 25
    outs = feed(
        "connors_rsi", [(10,), (11,), (12,), (11,)], rsi_length=2, streak_length=2, rank_length=2
    )
    assert outs[:3] == [None] * 3 and approx(outs[3], 25.0)


def test_rvgi_hand():
    # o=0 h=2 l=0 c=1 always: SWMA(c-o)=1, SWMA(h-l)=2 -> 0.5 ; signal SWMA of 0.5s = 0.5
    out = last("relative_vigor_index", [(0, 2, 0, 1)] * 7, length=1)
    assert approx(out.rvgi, 0.5) and approx(out.signal, 0.5)
    assert last("relative_vigor_index", [(1, 1, 1, 1)] * 7, length=1).rvgi == 0.0


# ---------- validation ----------

INT_PARAMS = {
    "stochastic": ("k_length", "k_smooth", "d_smooth"),
    "stoch_rsi": ("rsi_length", "stoch_length", "k_smooth", "d_smooth"),
    "williams_r": ("length",),
    "cci": ("length",),
    "roc": ("length",),
    "momentum": ("length",),
    "tsi": ("long_length", "short_length", "signal_length"),
    "awesome_oscillator": ("fast", "slow"),
    "ultimate_oscillator": ("fast", "middle", "slow"),
    "fisher_transform": ("length",),
    "kst": ("roc1", "roc2", "roc3", "roc4", "sma1", "sma2", "sma3", "sma4", "signal"),
    "coppock_curve": ("wma_length", "long_roc", "short_roc"),
    "cmo": ("length",),
    "connors_rsi": ("rsi_length", "streak_length", "rank_length"),
    "relative_vigor_index": ("length",),
}


@pytest.mark.parametrize("kind,param", [(k, p) for k, ps in INT_PARAMS.items() for p in ps])
def test_non_positive_periods_rejected(kind, param):
    with pytest.raises(ValueError):
        build_indicator(kind, **{param: 0})
    with pytest.raises(ValueError):
        build_indicator(kind, **{param: -3})


# ---------- configurability: every parameter changes the output ----------


def _sample(out):
    o = out[-1]
    return (o,) if isinstance(o, float) else tuple(getattr(o, f) for f in o.__slots__)


@pytest.mark.parametrize("kind,param", [(k, p) for k, ps in INT_PARAMS.items() for p in ps])
def test_each_parameter_changes_output(kind, param):
    default = inspect.signature(type(build_indicator(kind)).__init__).parameters[param].default
    rows = rows_for(kind)
    base = _sample(feed(kind, rows))
    alt = _sample(feed(kind, rows, **{param: default + 3}))
    assert base != alt


def test_stochastic_k_smooth_default_is_raw_k():
    rows = rows_for("stochastic")
    raw = feed("stochastic", rows, k_smooth=1)[-1].k
    smooth = feed("stochastic", rows, k_smooth=3)[-1].k
    assert raw != smooth


# ---------- Jesse kernel golden ----------


def _case_id(c):
    return c["kind"] + "-" + "-".join(str(v) for v in c["params"].values())


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=_case_id)
def test_matches_jesse_kernel(case):
    h, l, c = GOLDEN["high"], GOLDEN["low"], GOLDEN["close"]
    ind = build_indicator(case["kind"], **case["params"])
    for i in range(len(c)):
        args = (c[i],) if ind.inputs == ("close",) else (h[i], l[i], c[i])
        out = ind.update(*args)
        if out is None:
            continue  # we emit only once every output is valid (the kernel emits %D earlier, from a partial window)
        for name, series in case["out"].items():
            assert series[i] is not None
            got = out if name == "value" else getattr(out, name)
            assert got == pytest.approx(series[i], rel=1e-7, abs=1e-7), f"bar {i} {name}"
