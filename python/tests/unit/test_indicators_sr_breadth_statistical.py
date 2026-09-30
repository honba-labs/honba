"""Support/resistance, breadth and statistical indicators.

No jesse_rust kernel golden tests: none of these has a verified TradingView-identical kernel,
so numeric checks are hand-computed textbook values (arithmetic shown in comments).
"""
import math

import pytest

from honba.strategies.indicators import build_indicator

DAY = 86_400_000_000_000
H6 = 6 * 3_600_000_000_000  # 06:00 UTC = 11:30 IST, same IST day


def ts(d, hours=0):
    return d * DAY + H6 + hours * 3_600_000_000_000


def feed(kind, rows, **params):
    ind = build_indicator(kind, **params)
    return [ind.update(*r) for r in rows]


# ---- pivot_points: day0 bars H=12/10, L=9/8, last C=11 -> H=12 L=8 C=11 ----
def pivot(mode):
    rows = [(12, 9, 10, ts(0)), (10, 8, 11, ts(0, 1)), (13, 10, 12, ts(1))]
    out = feed("pivot_points", rows, mode=mode)
    assert out[0] is None and out[1] is None
    return out[2]


def test_pivot_standard():
    # P=(12+8+11)/3=10.3333; R=4; R1=2P-L=12.6667; S1=2P-H=8.6667; R2=P+4; S2=P-4
    # R3=H+2(P-L)=16.6667; S3=L-2(H-P)=4.6667
    v = pivot("standard")
    p = 31 / 3
    assert (v.pp, v.r1, v.s1, v.r2, v.s2, v.r3, v.s3) == pytest.approx(
        (p, 2 * p - 8, 2 * p - 12, p + 4, p - 4, 12 + 2 * (p - 8), 8 - 2 * (12 - p)))
    assert v.r1 == pytest.approx(12.6667, abs=1e-4) and v.s3 == pytest.approx(4.6667, abs=1e-4)


def test_pivot_fibonacci():
    # P=31/3; R1=P+.382*4, R2=P+.618*4, R3=P+4
    v = pivot("fibonacci")
    p = 31 / 3
    assert (v.r1, v.r2, v.r3, v.s1, v.s3) == pytest.approx(
        (p + 1.528, p + 2.472, p + 4, p - 1.528, p - 4))


def test_pivot_camarilla():
    # C=11, R=4, k=1.1*4=4.4; R1=11+4.4/12, R2=11+4.4/6, R3=11+4.4/4, S mirror
    v = pivot("camarilla")
    assert (v.r1, v.r2, v.r3, v.s1, v.s2, v.s3) == pytest.approx(
        (11 + 4.4 / 12, 11 + 4.4 / 6, 12.1, 11 - 4.4 / 12, 11 - 4.4 / 6, 9.9))
    assert v.pp == pytest.approx(31 / 3)


def test_pivot_woodie():
    # P=(12+8+2*11)/4=10.5; R1=2P-L=13; S1=2P-H=9; R2=P+4=14.5; R3=12+2*(10.5-8)=17; S3=8-2*(12-10.5)=5
    v = pivot("woodie")
    assert (v.pp, v.r1, v.s1, v.r2, v.s2, v.r3, v.s3) == pytest.approx((10.5, 13, 9, 14.5, 6.5, 17, 5))


def test_pivot_session_semantics():
    # levels constant through session 1, then re-derived from session 1's H/L/C
    rows = [(12, 8, 11, ts(0)), (20, 1, 5, ts(1)), (30, 2, 6, ts(1, 1)), (9, 9, 9, ts(2))]
    a, b, c, d = feed("pivot_points", rows)
    assert a is None and b == c and d != c
    assert d.pp == pytest.approx((30 + 1 + 6) / 3)  # H=30 L=1 C=6 of session 1


def test_pivot_validation_and_mode_changes_output():
    with pytest.raises(ValueError):
        build_indicator("pivot_points", mode="x")
    assert pivot("standard") != pivot("fibonacci") != pivot("camarilla")


# ---- fibonacci_retracement ----
def test_fib_retracement():
    # highs max 110, lows min 100, range 10: l236=110-2.36, l500=105, l1000=100
    out = feed("fibonacci_retracement", [(105, 100), (110, 102), (108, 101)], length=3)
    assert out[:2] == [None, None]
    v = out[2]
    assert (v.l0, v.l236, v.l382, v.l500, v.l618, v.l786, v.l1000) == pytest.approx(
        (110, 107.64, 106.18, 105, 103.82, 102.14, 100))


def test_fib_validation_and_length_config():
    with pytest.raises(ValueError):
        build_indicator("fibonacci_retracement", length=0)
    rows = [(10, 5), (20, 6), (12, 7), (13, 8)]
    assert feed("fibonacci_retracement", rows, length=4)[-1].l0 == 20
    assert feed("fibonacci_retracement", rows, length=2)[-1].l0 == 13  # window drops the 20


# ---- williams_fractals ----
def test_fractals():
    # highs 1,2,5,2,1 -> bar3 (5) is an up fractal, confirmed on bar 5; lows 5,4,1,4,5 -> down
    out = feed("williams_fractals", [(1, 5), (2, 4), (5, 1), (2, 4), (1, 5)])
    assert out[:4] == [None] * 4 and (out[4].up, out[4].down) == (1.0, 1.0)
    out = feed("williams_fractals", [(1, 5), (2, 4), (2, 4), (2, 4), (1, 5)])  # ties are not fractals
    assert (out[4].up, out[4].down) == (0.0, 0.0)


# ---- breadth ----
def test_advance_decline_line():
    # nets: +10, -5, +3 -> 10, 5, 8
    assert feed("advance_decline_line", [(30, 20), (10, 15), (13, 10)]) == [10, 5, 8]


def test_trin():
    # (2000/1000)/(3e6/1.5e6)... adv/dec=2, advvol/decvol=2 -> 1.0 ; (3/1)/(1/1)=3.0
    assert feed("trin", [(20, 10, 200, 100), (30, 10, 100, 100)]) == [1.0, 3.0]
    assert feed("trin", [(5, 0, 10, 10)]) == [1.0]  # undefined -> neutral


def test_mcclellan():
    # nets 10 then 20: fast alpha=2/4=.5 (n=3), slow alpha=2/6=1/3 (n=5), seeds = first value
    # bar1: 0; bar2: fast=15, slow=10+10/3=13.333 -> 1.6667
    out = feed("mcclellan_oscillator", [(20, 10), (30, 10)], fast=3, slow=5)
    assert out[0] == 0.0 and out[1] == pytest.approx(15 - 40 / 3)


def test_mcclellan_validation_and_config():
    with pytest.raises(ValueError):
        build_indicator("mcclellan_oscillator", fast=39, slow=19)
    with pytest.raises(ValueError):
        build_indicator("mcclellan_oscillator", fast=0)
    rows = [(20 + i % 7, 10) for i in range(30)]
    assert feed("mcclellan_oscillator", rows)[-1] != feed("mcclellan_oscillator", rows, fast=5, slow=10)[-1]


def test_updown_volume_ratio():
    assert feed("updown_volume_ratio", [(300, 100), (5, 0)]) == [3.0, 1.0]


def test_put_call_ratio():
    # raw: 80/100=.8, 120/100=1.2; SMA2 = 1.0
    assert feed("put_call_ratio", [(80, 100), (120, 100)]) == pytest.approx([0.8, 1.2])
    assert feed("put_call_ratio", [(80, 100), (120, 100)], smoothing=2) == [None, pytest.approx(1.0)]
    assert feed("put_call_ratio", [(1, 0)]) == [1.0]
    with pytest.raises(ValueError):
        build_indicator("put_call_ratio", smoothing=0)


# ---- statistical ----
def test_correlation():
    # prices perfectly linear -> +1; reversed -> -1; flat -> 0
    assert feed("correlation", [(1, 2), (2, 4), (3, 6)], length=3)[-1] == pytest.approx(1.0)
    assert feed("correlation", [(1, 3), (2, 2), (3, 1)], length=3)[-1] == pytest.approx(-1.0)
    assert feed("correlation", [(5, 1), (5, 2), (5, 3)], length=3)[-1] == 0.0
    with pytest.raises(ValueError):
        build_indicator("correlation", length=1)


def test_correlation_length_config():
    rows = [(math.sin(i), math.sin(i / 2) + 0.1 * i) for i in range(30)]
    assert feed("correlation", rows, length=5)[-1] != feed("correlation", rows, length=20)[-1]


def test_beta_and_covariance():
    # bench 100,110,99 -> returns +.1,-.1 ; asset 100,120,96 -> +.2,-.2
    # cov (n-1)=(.02+.02)/1=.04 ; var_bench pop=.01 -> beta=cov_pop/var_pop=.02/.01=2
    rows = [(100, 100), (120, 110), (96, 99)]
    b = feed("beta", rows, length=2)
    assert b[:2] == [None, None] and b[2] == pytest.approx(2.0)
    c = feed("covariance", rows, length=2)
    assert c[:2] == [None, None] and c[2] == pytest.approx(0.04)
    assert feed("beta", [(1, 5), (2, 5), (3, 5)], length=2)[-1] == 0.0  # flat benchmark
    for k in ("beta", "covariance"):
        with pytest.raises(ValueError):
            build_indicator(k, length=1)


def test_beta_length_config():
    rows = [(100 + math.sin(i) * 3 + i * 0.1, 100 + math.cos(i / 2) * 2) for i in range(80)]
    assert feed("beta", rows, length=10)[-1] != feed("beta", rows, length=60)[-1]
    assert feed("covariance", rows, length=5)[-1] != feed("covariance", rows, length=20)[-1]


def test_zscore():
    # [1,2,3]: mean 2, pop var 2/3 -> (3-2)/sqrt(2/3)=1.2247
    assert feed("zscore", [(1,), (2,), (3,)], length=3)[-1] == pytest.approx(math.sqrt(1.5))
    assert feed("zscore", [(4,), (4,)], length=2)[-1] == 0.0
    with pytest.raises(ValueError):
        build_indicator("zscore", length=1)
    xs = [(math.sin(i) * 5,) for i in range(30)]
    assert feed("zscore", xs, length=5)[-1] != feed("zscore", xs, length=20)[-1]


def test_hurst():
    # increments +1 x4 then -1 x4: mean 0, S=1, cum max 4 min 0 -> R=4, H=ln4/ln8=2/3
    closes = [0, 1, 2, 3, 4, 3, 2, 1, 0]
    assert feed("hurst_exponent", [(c,) for c in closes], length=9)[-1] == pytest.approx(2 / 3)
    # alternating: R=1,S=1 -> H=0
    alt = [0, 1] * 4 + [0]
    assert feed("hurst_exponent", [(c,) for c in alt], length=9)[-1] == pytest.approx(0.0)
    # flat / linear: S=0 -> 0.5 convention
    assert feed("hurst_exponent", [(c,) for c in range(9)], length=9)[-1] == 0.5
    with pytest.raises(ValueError):
        build_indicator("hurst_exponent", length=4)


def test_hurst_length_config():
    xs = [(math.sin(i / 3) * 5 + (i % 5),) for i in range(150)]
    assert feed("hurst_exponent", xs, length=20)[-1] != feed("hurst_exponent", xs, length=100)[-1]
