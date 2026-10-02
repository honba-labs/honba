"""Moving-average (hma..lsma) and trend (adx..linear_regression) indicators.

Hand-checked values are worked out in comments. Kernel parity (Jesse's compiled jesse-rust 1.2.0)
is tested for hma, vwma, kama, mcginley_dynamic, lsma, supertrend and vortex, whose definitions
match TradingView; dema/tema (Jesse seeds EMAs with the first value), adx, aroon and parabolic_sar
differ in seeding/window, so they rely on hand-checked textbook values only.
Regenerate the golden file with tests/fixtures/gen_jesse_golden_moving_average_trend.py.
"""

import json
import math
import random
from pathlib import Path

import pytest

from honba.strategies.indicators import build_indicator
from honba.strategies.indicators.moving_average import (
    Alma,
    Dema,
    Hma,
    Kama,
    Lsma,
    McGinleyDynamic,
    Tema,
    Vwma,
)
from honba.strategies.indicators.trend import (
    Adx,
    Aroon,
    ChandeKrollStop,
    LinearRegression,
    ParabolicSar,
    Supertrend,
    Vortex,
)

GOLDEN = json.loads(
    (
        Path(__file__).parent.parent / "fixtures" / "jesse_golden_moving_average_trend.json"
    ).read_text()
)
H, L, C, V = GOLDEN["high"], GOLDEN["low"], GOLDEN["close"], GOLDEN["volume"]


def feed(ind, *cols):
    return [ind.update(*row) for row in zip(*cols)]


def last(ind, *cols):
    return feed(ind, *cols)[-1]


rnd = random.Random(3)
_c = [100.0]
for _ in range(299):
    _c.append(_c[-1] + rnd.gauss(0, 1.5))
CLOSE = _c
HIGH = [c + rnd.random() * 2 for c in CLOSE]
LOW = [c - rnd.random() * 2 for c in CLOSE]
VOL = [1000 + rnd.random() * 500 for _ in CLOSE]


# ---------------------------------------------------------------- golden parity
def _case_id(c):
    return c["kind"] + "-" + "-".join(str(v) for v in c["params"].values())


@pytest.mark.parametrize("case", GOLDEN["cases"], ids=_case_id)
def test_matches_jesse_kernel(case):
    ind = build_indicator(case["kind"], **case["params"])
    cols = {"close": C, "high": H, "low": L, "volume": V}
    outs = feed(ind, *[cols[f] for f in ind.inputs])
    compared = 0
    for name, expected in case["out"].items():
        for got, exp in zip(outs, expected):
            if got is None or exp is None:
                continue
            g = got if isinstance(got, float) else getattr(got, name)
            assert g == pytest.approx(exp, rel=1e-9, abs=1e-9)
            compared += 1
    assert compared > 100


# ---------------------------------------------------------------- moving averages
def test_hma_hand_value_and_warmup():
    # period 4: half=2, root=2. Closes 1..5: WMA2(4,5)=(4+10)/3=14/3, WMA4(2..5)=(2+6+12+20)/10=4
    # raw = 2*14/3-4 = 16/3; previous raw (t=4) = 2*11/3-3 = 13/3; hull = WMA2 = (13/3 + 2*16/3)/3 = 5
    out = feed(Hma(4), [1, 2, 3, 4, 5])
    assert out[:4] == [None] * 4 and out[4] == pytest.approx(5.0)
    assert Hma(4).warmup == 5


def test_hma_validation_and_config():
    with pytest.raises(ValueError):
        Hma(0)
    assert last(Hma(9), CLOSE) != pytest.approx(last(Hma(16), CLOSE))


def test_vwma_hand_value_and_zero_volume():
    # (10*1 + 20*3) / (1+3) = 17.5
    assert last(Vwma(2), [10, 20], [1, 3]) == pytest.approx(17.5)
    assert last(Vwma(2), [10, 20], [0, 0]) == 20.0  # documented: zero volume -> latest close
    with pytest.raises(ValueError):
        Vwma(0)
    assert last(Vwma(5), CLOSE, VOL) != pytest.approx(last(Vwma(20), CLOSE, VOL))


def test_dema_hand_value():
    # period 2 (alpha 2/3) on 1,2,3: EMA1 = 1.5, 2.5 ; EMA2 seed = (1.5+2.5)/2 = 2 -> 2*2.5 - 2 = 3 (no lag on a ramp)
    out = feed(Dema(2), [1, 2, 3])
    assert out == [None, None, pytest.approx(3.0)]
    assert Dema(2).warmup == 3
    with pytest.raises(ValueError):
        Dema(0)
    assert last(Dema(5), CLOSE) != pytest.approx(last(Dema(20), CLOSE))


def test_tema_hand_value():
    # period 2 on 1..4: EMA1 = 1.5,2.5,3.5 ; EMA2 = 2 (seed), 3 ; EMA3 seed = 2.5
    # 3*3.5 - 3*3 + 2.5 = 4
    out = feed(Tema(2), [1, 2, 3, 4])
    assert out[:3] == [None] * 3 and out[3] == pytest.approx(4.0)
    assert Tema(2).warmup == 4
    with pytest.raises(ValueError):
        Tema(0)
    assert last(Tema(5), CLOSE) != pytest.approx(last(Tema(20), CLOSE))


def test_kama_hand_value_and_config():
    # period 2, fast 2, slow 30 on 10,11,13: er = |13-10| / (1+2) = 1 -> sc = (2/3)^2 = 4/9
    # seed = previous close 11 -> 11 + 4/9 * (13-11) = 107/9
    out = feed(Kama(2), [10, 11, 13])
    assert out[:2] == [None, None] and out[2] == pytest.approx(107 / 9)
    assert feed(Kama(2), [5, 5, 5, 5])[-1] == 5.0  # no movement: er = 0 handled
    for bad in ({"period": 0}, {"fast": 0}, {"fast": 30, "slow": 30}):
        with pytest.raises(ValueError):
            Kama(**bad)
    base = last(Kama(), CLOSE)
    assert last(Kama(period=5), CLOSE) != pytest.approx(base)
    assert last(Kama(fast=4), CLOSE) != pytest.approx(base)
    assert last(Kama(slow=60), CLOSE) != pytest.approx(base)


def test_mcginley_hand_value_and_config():
    # period 2, k 0.6 on 100,110: 100 + 10 / (0.6*2*(110/100)^4)
    out = feed(McGinleyDynamic(2), [100, 110])
    assert out[0] == 100 and out[1] == pytest.approx(100 + 10 / (1.2 * 1.1**4))
    assert McGinleyDynamic(2).update(0.0) == 0.0
    for bad in ({"period": 0}, {"k": 0.0}):
        with pytest.raises(ValueError):
            McGinleyDynamic(**bad)
    assert last(McGinleyDynamic(k=0.3), CLOSE) != pytest.approx(last(McGinleyDynamic(k=0.9), CLOSE))
    assert last(McGinleyDynamic(period=5), CLOSE) != pytest.approx(
        last(McGinleyDynamic(period=30), CLOSE)
    )
    assert McGinleyDynamic().update(50.0) == 50.0  # non-positive ratio guard


def test_alma_hand_value_and_config():
    # symmetric weights (offset 0.5) on the ramp 1,2,3 -> centre value 2
    assert last(Alma(3, offset=0.5), [1, 2, 3]) == pytest.approx(2.0)
    # offset 1 with a tiny sigma (s = n/sigma huge) flattens the weights -> SMA of 1,2,6 = 3
    assert last(Alma(3, offset=1.0, sigma=1e-6), [1, 2, 6]) == pytest.approx(3.0)
    # offset 1, sigma 3 (s=1, m=2): weights e^-2, e^-0.5, 1 on 1,2,6
    w = [math.exp(-2), math.exp(-0.5), 1.0]
    assert last(Alma(3, offset=1.0, sigma=3.0), [1, 2, 6]) == pytest.approx(
        (w[0] + 2 * w[1] + 6 * w[2]) / sum(w)
    )
    for bad in ({"period": 0}, {"offset": 1.5}, {"offset": -0.1}, {"sigma": 0}):
        with pytest.raises(ValueError):
            Alma(**bad)
    base = last(Alma(), CLOSE)
    assert last(Alma(offset=0.3), CLOSE) != pytest.approx(base)
    assert last(Alma(sigma=2.0), CLOSE) != pytest.approx(base)
    assert last(Alma(period=20), CLOSE) != pytest.approx(base)


def test_lsma_hand_value_and_config():
    # 1,2,4: slope (4-1)/2 = 1.5, mean 7/3 -> intercept 7/3 - 1.5 = 5/6; endpoint 5/6 + 3 = 23/6; offset 1 -> 5/6 + 1.5 = 7/3
    assert last(Lsma(3), [1, 2, 4]) == pytest.approx(23 / 6)
    assert last(Lsma(3, offset=1), [1, 2, 4]) == pytest.approx(7 / 3)
    for bad in ({"period": 1}, {"period": 0}):
        with pytest.raises(ValueError):
            Lsma(**bad)
    assert last(Lsma(offset=3), CLOSE) != pytest.approx(last(Lsma(), CLOSE))
    assert last(Lsma(10), CLOSE) != pytest.approx(last(Lsma(50), CLOSE))


# ---------------------------------------------------------------- trend
def test_adx_hand_value_and_config():
    # bars (h,l,c): (10,8,9) (12,9,11) (13,10,12); di_length 2, adx_smoothing 1
    # bar1: +DM 2, -DM 0 (down = 8-9 < 0), TR = max(3,3,0) = 3; bar2: +DM 1, -DM 0, TR = max(3,2,1) = 3
    # RMA2 seeds: TR 3, +DM 1.5, -DM 0 -> +DI 50, -DI 0 -> DX = 100*50/50 = 100 = ADX (RMA1)
    ind = Adx(2, 1)
    out = feed(ind, [10, 12, 13], [8, 9, 10], [9, 11, 12])
    assert out[:2] == [None, None] and ind.warmup == 3
    assert (out[2].adx, out[2].plus_di, out[2].minus_di) == pytest.approx((100.0, 50.0, 0.0))
    flat = last(Adx(2, 1), [5] * 6, [5] * 6, [5] * 6)  # zero range: all zero, finite
    assert (flat.adx, flat.plus_di, flat.minus_di) == (0.0, 0.0, 0.0)
    for bad in ({"di_length": 0}, {"adx_smoothing": 0}):
        with pytest.raises(ValueError):
            Adx(**bad)
    base = last(Adx(), HIGH, LOW, CLOSE)
    assert last(Adx(di_length=7), HIGH, LOW, CLOSE).plus_di != pytest.approx(base.plus_di)
    assert last(Adx(adx_smoothing=30), HIGH, LOW, CLOSE).adx != pytest.approx(base.adx)
    assert Adx(14, 20).warmup == 34


def test_aroon_hand_value_and_config():
    # length 2, last 3 bars. highs 1,3,2 -> highest is 1 bar ago -> up = 100*(2-1)/2 = 50
    # lows 5,6,4 -> lowest is now -> down = 100; oscillator = -50
    out = last(Aroon(2), [1, 3, 2], [5, 6, 4])
    assert (out.up, out.down, out.oscillator) == (50.0, 100.0, -50.0)
    assert Aroon(2).warmup == 3
    # ties resolve to the most recent bar
    assert last(Aroon(2), [3, 3, 3], [1, 1, 1]).up == 100.0
    with pytest.raises(ValueError):
        Aroon(0)
    assert last(Aroon(5), HIGH, LOW) != last(Aroon(40), HIGH, LOW)


def test_parabolic_sar_hand_values_and_config():
    # (h,l): (10,8) (11,9) (12,10) (13,11). bar1: up move 1 > 0 -> long, SAR = 8, EP = 11, AF .02
    # bar2: 8 + .02*(11-8) = 8.06, clamped to min(prev lows 9, 8) = 8; new high 12 -> EP 12, AF .04
    # bar3: 8 + .04*(12-8) = 8.16 (below lows 10, 9)
    out = feed(ParabolicSar(), [10, 11, 12, 13], [8, 9, 10, 11])
    assert out[0] is None and out[1:3] == [8.0, 8.0] and out[3] == pytest.approx(8.16)
    # reversal: bar2 low 7 < SAR 8 -> SAR jumps to the extreme point 11 (short)
    assert last(ParabolicSar(), [10, 11, 9.5], [8, 9, 7]) == 11.0
    # falling start -> short: SAR = first high
    assert feed(ParabolicSar(), [10, 9], [8, 6])[1] == 10.0
    for bad in ({"start": 0}, {"increment": 0}, {"start": 0.3, "maximum": 0.2}):
        with pytest.raises(ValueError):
            ParabolicSar(**bad)
    base = last(ParabolicSar(), HIGH, LOW)
    assert last(ParabolicSar(start=0.01), HIGH, LOW) != pytest.approx(base)
    assert last(ParabolicSar(maximum=0.05), HIGH, LOW) != pytest.approx(base)
    assert last(ParabolicSar(increment=0.005), HIGH, LOW) != pytest.approx(base)


def test_supertrend_hand_values_and_config():
    # atr_period 1, factor 1 (ATR = TR). bar0 (10,8,9): TR 2, mid 9 -> bands 7/11, starts down -> (11, -1)
    # bar1 (12,10,11.5): TR 3, mid 11 -> bands 8/14; lower ratchets up to 8; upper stays 11 (prev close 9 <= 11)
    # close 11.5 > 11 -> flips up: value = lower band 8
    out = feed(Supertrend(1, 1.0), [10, 12], [8, 10], [9, 11.5])
    assert (out[0].value, out[0].direction) == (11.0, -1.0)
    assert (out[1].value, out[1].direction) == (8.0, 1.0)
    for bad in ({"atr_period": 0}, {"factor": 0}):
        with pytest.raises(ValueError):
            Supertrend(**bad)
    base = last(Supertrend(), HIGH, LOW, CLOSE)
    assert last(Supertrend(factor=1.0), HIGH, LOW, CLOSE).value != pytest.approx(base.value)
    assert last(Supertrend(atr_period=20, factor=1.0), HIGH, LOW, CLOSE).value != pytest.approx(
        last(Supertrend(factor=1.0), HIGH, LOW, CLOSE).value
    )


def test_vortex_hand_value_and_config():
    # period 1, bars (10,8,9) (12,9,11): VM+ = |12-8| = 4, VM- = |9-10| = 1, TR = max(3,3,0) = 3
    out = last(Vortex(1), [10, 12], [8, 9], [9, 11])
    assert (out.plus, out.minus) == pytest.approx((4 / 3, 1 / 3))
    assert last(Vortex(3), [5] * 5, [5] * 5, [5] * 5).plus == 0.0  # zero range -> 0
    assert Vortex(14).warmup == 15
    with pytest.raises(ValueError):
        Vortex(0)
    assert last(Vortex(7), HIGH, LOW, CLOSE).plus != pytest.approx(
        last(Vortex(21), HIGH, LOW, CLOSE).plus
    )


def test_chande_kroll_stop_hand_values_and_config():
    # p=1, x=1 (ATR = TR): bar0 (10,8,9): TR 2 -> high stop 10-2 = 8, low stop 8+2 = 10
    # bar1 (12,10,11): TR 3 -> high stop 12-3 = 9, low stop 10+3 = 13; q=2: long = max(8,9) = 9, short = min(10,13) = 10
    out = feed(ChandeKrollStop(1, 1.0, 2), [10, 12], [8, 10], [9, 11])
    assert out[0] is None
    assert (out[1].long_stop, out[1].short_stop) == (9.0, 10.0)
    one = last(ChandeKrollStop(1, 1.0, 1), [10], [8], [9])
    assert (one.long_stop, one.short_stop) == (8.0, 10.0)
    assert ChandeKrollStop(10, 1.0, 9).warmup == 18
    for bad in ({"p": 0}, {"q": 0}, {"x": 0}):
        with pytest.raises(ValueError):
            ChandeKrollStop(**bad)
    base = last(ChandeKrollStop(), HIGH, LOW, CLOSE)
    assert last(ChandeKrollStop(x=2.0), HIGH, LOW, CLOSE).long_stop != pytest.approx(base.long_stop)
    assert last(ChandeKrollStop(p=20), HIGH, LOW, CLOSE).long_stop != pytest.approx(base.long_stop)
    assert last(ChandeKrollStop(q=3), HIGH, LOW, CLOSE).short_stop != pytest.approx(base.short_stop)


def test_linear_regression_hand_values_and_config():
    # 1,2,4: slope 1.5, endpoint 23/6; residuals 1/6, -1/3, 1/6 -> var = (1/36+1/9+1/36)/3 = 1/18
    out = last(LinearRegression(3, 2.0, 1.0), [1, 2, 4])
    std = math.sqrt(1 / 18)
    assert (out.value, out.slope) == pytest.approx((23 / 6, 1.5))
    assert out.upper == pytest.approx(23 / 6 + 2 * std)
    assert out.lower == pytest.approx(23 / 6 - 1 * std)
    perfect = last(LinearRegression(4), [1, 2, 3, 4])  # exact line: zero-width channel
    assert perfect.upper == pytest.approx(perfect.value) == pytest.approx(4.0)
    for bad in ({"length": 1}, {"upper_deviation": -1}, {"lower_deviation": -1}):
        with pytest.raises(ValueError):
            LinearRegression(**bad)
    base = last(LinearRegression(50), CLOSE)
    assert last(LinearRegression(20), CLOSE).slope != pytest.approx(base.slope)
    assert last(LinearRegression(50, upper_deviation=1.0), CLOSE).upper != pytest.approx(base.upper)
    assert last(LinearRegression(50, lower_deviation=1.0), CLOSE).lower != pytest.approx(base.lower)
    assert last(LinearRegression(50, lower_deviation=1.0), CLOSE).upper == pytest.approx(base.upper)


def test_alma_offset_is_floored_like_pine():
    """Pine ta.alma: m = floor(offset * (length - 1)); (9, 0.85) -> m = 6, not 6.8."""
    from honba.strategies.indicators import build_indicator

    def one_hot(k, n=9):
        a = build_indicator("alma", period=n, offset=0.85, sigma=6.0)
        out = None
        for i in range(n):
            out = a.update(1.0 if i == k else 0.0)
        return out  # the weight of window position k

    weights = [one_hot(k) for k in range(9)]
    assert max(range(9), key=lambda k: weights[k]) == 6
    assert weights[6] > weights[7] > weights[8]
    assert sum(weights) == pytest.approx(1.0)
    # exact value of the peak weight for m = 6, s = 1.5: 1 / sum(exp(-(i-6)^2 / 4.5))
    import math

    tot = sum(math.exp(-((i - 6) ** 2) / (2 * 1.5**2)) for i in range(9))
    assert weights[6] == pytest.approx(1 / tot)
