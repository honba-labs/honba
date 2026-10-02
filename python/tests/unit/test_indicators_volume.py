"""Volume-family indicators: hand-checked values, validation, configurability, textbook/TradingView values (no Jesse kernel golden: its efi/adosc/emv seed differently)."""

import math

import pytest

from honba.strategies.indicators import build_indicator
from honba.strategies.indicators.volume import (
    AccumulationDistribution,
    ChaikinMoneyFlow,
    ChaikinOscillator,
    EaseOfMovement,
    ForceIndex,
    KlingerOscillator,
    MoneyFlowIndex,
    Obv,
    PriceVolumeTrend,
    VolumeOscillator,
    Vwap,
)

DAY = 86_400_000_000_000
IST = 19_800_000_000_000


def feed(ind, rows):
    return [ind.update(*r) for r in rows]


def approx(x):
    return pytest.approx(x, rel=1e-9, abs=1e-9)


def test_obv():
    # closes 10,11,11,9 vols 5,7,3,4 -> 0, +7, +7 (unchanged), 7-4=3
    assert feed(Obv(), [(10, 5), (11, 7), (11, 3), (9, 4)]) == [0.0, 7.0, 7.0, 3.0]


def test_accumulation_distribution():
    # bar1: H=12 L=8 C=11 -> CLV=((3)-(1))/4=0.5, V=10 -> 5; bar2: H=L -> 0; bar3: C=L -> CLV=-1, V=2 -> 3
    out = feed(AccumulationDistribution(), [(12, 8, 11, 10), (5, 5, 5, 99), (10, 6, 6, 2)])
    assert out == [5.0, 5.0, 3.0]


def test_chaikin_money_flow():
    # bar1 CLV*V=5, V=10 ; bar2 CLV=-1,V=2 -> -2 ; CMF(2) = (5-2)/(10+2) = 0.25
    out = feed(ChaikinMoneyFlow(2), [(12, 8, 11, 10), (10, 6, 6, 2)])
    assert out[0] is None and out[1] == approx(0.25)
    assert ChaikinMoneyFlow(2).update(1, 1, 1, 0) is None
    z = ChaikinMoneyFlow(1)
    assert z.update(2, 1, 2, 0) == 0.0  # zero volume convention


def test_money_flow_index():
    # hlc3 = 10, 11, 10.5 (h=l=c); vols 1,2,4. flows: up 11*2=22 ; down 10.5*4=42
    # MFI(2) = 100 - 100/(1+22/42)
    out = feed(MoneyFlowIndex(2), [(10, 10, 10, 1), (11, 11, 11, 2), (10.5, 10.5, 10.5, 4)])
    assert out[:2] == [None, None]
    assert out[2] == approx(100 - 100 / (1 + 22 / 42))
    assert feed(MoneyFlowIndex(1), [(1, 1, 1, 1), (2, 2, 2, 1)])[1] == 100.0
    assert feed(MoneyFlowIndex(1), [(1, 1, 1, 1), (1, 1, 1, 1)])[1] == 50.0


def test_volume_oscillator():
    # volumes 10,20,30 ; short=2 (alpha 2/3), long=3 (SMA seed 20 at bar3)
    # short seed at bar2 = 15 ; bar3 = 30*2/3 + 15/3 = 25 ; osc = 100*(25-20)/20 = 25
    out = feed(VolumeOscillator(2, 3), [(10,), (20,), (30,)])
    assert out[:2] == [None, None] and out[2] == approx(25.0)
    assert VolumeOscillator(1, 1).update(0.0) == 0.0


def test_klinger_hand_value():
    # fast=1,slow=2,signal=1: hlc3 = 10,11,10 ; sv (bars2,3) = +V2, -V3 = +4, -6
    # bar3: ema1 = -6, ema2 = SMA(4,-6) = -1 -> klinger -5 ; signal(1) = -5
    k = KlingerOscillator(1, 2, 1)
    out = feed(k, [(10, 10, 10, 1), (11, 11, 11, 4), (10, 10, 10, 6)])
    assert out[:2] == [None, None]
    assert (out[2].klinger, out[2].signal) == (approx(-5.0), approx(-5.0))
    assert k.warmup == 3


def test_ease_of_movement():
    # bar1 hl2=10 ; bar2 H=12 L=10 hl2=11 V=2 -> 10000*1*2/2 = 10000 ; bar3 H=13 L=11 hl2=12 V=4 -> 10000*1*2/4=5000
    out = feed(EaseOfMovement(2), [(10, 10, 10, 1), (12, 10, 11, 2), (13, 11, 12, 4)])
    assert out[:2] == [None, None] and out[2] == approx(7500.0)
    assert feed(EaseOfMovement(1), [(1, 1, 1, 1), (3, 2, 2, 0)])[1] == 0.0


def test_force_index():
    # closes 10,12,11,15 vols 1,2,3,4 ; raw = 2*2=4, -1*3=-3, 4*4=16 ; period 2: seed SMA(4,-3)=0.5 ; then 16*2/3+0.5/3
    out = feed(ForceIndex(2), [(10, 1), (12, 2), (11, 3), (15, 4)])
    assert out[:2] == [None, None] and out[2] == approx(0.5) and out[3] == approx(32 / 3 + 0.5 / 3)


def test_price_volume_trend():
    # closes 100,110,99 vols 5,10,20: 0 ; +0.1*10=1 ; 1 + (-11/110)*20 = -1
    out = feed(PriceVolumeTrend(), [(100, 5), (110, 10), (99, 20)])
    assert out == [0.0, approx(1.0), approx(-1.0)]
    assert PriceVolumeTrend().update(0, 5) == 0.0


def test_chaikin_oscillator():
    # A/D = 5 then 5 (H=L) : all EMAs of a constant -> 0 ; then A/D=-5 (H=12,L=8,C=8,V=10 -> -10 => -5)
    ind = ChaikinOscillator(1, 2)
    out = feed(ind, [(12, 8, 11, 10), (5, 5, 5, 1), (12, 8, 8, 10)])
    # fast(1)=ad ; slow(2) SMA seed at bar2 = 5, bar3 = (-5)*2/3 + 5/3 = -5/3 ; osc = -5 + 5/3
    assert out[0] is None and out[1] == approx(0.0) and out[2] == approx(-5 + 5 / 3)


def test_vwap_session_reset_and_bands():
    t0 = DAY * 100 + 4 * 3_600_000_000_000 - IST + IST  # 04:00 UTC = 09:30 IST same IST day
    v = Vwap(1.0)
    # bar1 tp=10 V=1 ; bar2 tp=20 V=1 -> vwap 15, var = (100+400)/2-225 = 25, sd 5
    a = v.update(10, 10, 10, 1, t0)
    b = v.update(20, 20, 20, 1, t0 + 60_000_000_000)
    assert (a.vwap, a.upper, a.lower) == (10.0, 10.0, 10.0)
    assert (b.vwap, b.upper, b.lower) == (approx(15), approx(20), approx(10))
    c = v.update(30, 30, 30, 5, t0 + DAY)  # next IST day: reset
    assert c.vwap == 30.0
    # crossing IST midnight (18:30 UTC) resets even within one UTC day
    w = Vwap()
    w.update(10, 10, 10, 1, DAY * 100 + 18 * 3_600_000_000_000)
    assert w.update(20, 20, 20, 1, DAY * 100 + 19 * 3_600_000_000_000).vwap == 20.0
    assert Vwap().update(7, 7, 7, 0, t0).vwap == 7.0  # zero-volume convention


@pytest.mark.parametrize(
    "cls,kw",
    [
        (ChaikinMoneyFlow, {"period": 0}),
        (MoneyFlowIndex, {"period": 0}),
        (VolumeOscillator, {"short": 0}),
        (VolumeOscillator, {"long": 0}),
        (KlingerOscillator, {"fast": 0}),
        (KlingerOscillator, {"slow": 0}),
        (KlingerOscillator, {"signal": 0}),
        (EaseOfMovement, {"period": 0}),
        (EaseOfMovement, {"divisor": 0}),
        (ForceIndex, {"period": 0}),
        (ChaikinOscillator, {"fast": 0}),
        (ChaikinOscillator, {"slow": 0}),
        (Vwap, {"band_mult": -1}),
    ],
)
def test_param_validation(cls, kw):
    with pytest.raises(ValueError):
        cls(**kw)


def _bars(n=80):
    out = []
    for i in range(n):
        c = 100 + 5 * math.sin(i / 4) + 0.1 * i
        out.append(
            (
                c + 1 + (i % 3) * 0.2,
                c - 1,
                c + 0.3 * math.cos(i),
                500 + 37 * (i % 7),
                DAY * 50 + i * 60_000_000_000,
            )
        )
    return out


def _last(ind, cols):
    rows = _bars()
    idx = {"high": 0, "low": 1, "close": 2, "volume": 3, "ts": 4}
    r = None
    for row in rows:
        r = ind.update(*[row[idx[c]] for c in ind.inputs])
    return r


@pytest.mark.parametrize(
    "kind,a,b",
    [
        ("chaikin_money_flow", {"period": 5}, {"period": 20}),
        ("money_flow_index", {"period": 5}, {"period": 14}),
        ("volume_oscillator", {"short": 3, "long": 10}, {"short": 5, "long": 20}),
        (
            "klinger_oscillator",
            {"fast": 5, "slow": 10, "signal": 3},
            {"fast": 8, "slow": 20, "signal": 5},
        ),
        ("ease_of_movement", {"period": 5}, {"period": 14}),
        ("ease_of_movement", {"divisor": 1000.0}, {"divisor": 10000.0}),
        ("force_index", {"period": 3}, {"period": 13}),
        ("chaikin_oscillator", {"fast": 2, "slow": 5}, {"fast": 3, "slow": 10}),
        ("vwap", {"band_mult": 1.0}, {"band_mult": 2.0}),
    ],
)
def test_configurability(kind, a, b):
    ra, rb = _last(build_indicator(kind, **a), None), _last(build_indicator(kind, **b), None)
    assert ra != rb


def test_ease_of_movement_divisor_scales_linearly():
    a = _last(EaseOfMovement(divisor=1000.0), None)
    b = _last(EaseOfMovement(divisor=10000.0), None)
    assert b == approx(10 * a)


def test_vwap_band_mult_scales_band_width():
    r1, r2 = _last(Vwap(1.0), None), _last(Vwap(2.0), None)
    assert r2.vwap == approx(r1.vwap)
    assert r2.upper - r2.vwap == approx(2 * (r1.upper - r1.vwap))
