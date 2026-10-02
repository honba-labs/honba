"""Volatility-family indicators: hand-checked values, validation, configurability, Jesse-kernel parity
(bollinger_bandwidth, standard_deviation, choppiness_index only; the rest follow TradingView and have no
faithful kernel equivalent)."""

import json
import math
from pathlib import Path

import pytest

from honba.strategies.indicators import build_indicator

G = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "jesse_golden_volatility.json").read_text()
)
H, L, C = G["high"], G["low"], G["close"]


def feed(ind, *cols):
    return [ind.update(*row) for row in zip(*cols)]


def mk(kind, **kw):
    return build_indicator(kind, **kw)


# -- kernel parity ------------------------------------------------------------


@pytest.mark.parametrize(
    "case", G["cases"], ids=lambda c: c["kind"] + str(list(c["params"].values()))
)
def test_matches_jesse_kernel(case):
    ind = mk(case["kind"], **case["params"])
    cols = (H, L, C) if case["kind"] == "choppiness_index" else (C,)
    got = feed(ind, *cols)
    for i, exp in enumerate(case["out"]["value"]):
        if exp is None:
            assert got[i] is None
        else:
            assert got[i] == pytest.approx(exp, rel=1e-9, abs=1e-9)


# -- keltner ------------------------------------------------------------------


def test_keltner_hand_values():
    # EMA(2) SMA-seeded on closes 10,12,14: seed 11, then 14*(2/3)+11/3 = 13. ATR(1): TR bar2=max(4-... see below
    k = mk("keltner", length=2, atr_length=1, mult=2.0)
    bars = [(11, 9, 10), (13, 11, 12), (15, 13, 14)]
    out = feed(k, *zip(*bars))
    assert out[0] is None
    # bar2: EMA=11; TR=max(2,|13-10|,|11-10|)=3 -> ATR(1)=3 -> 11 +/- 6
    assert (out[1].upper, out[1].middle, out[1].lower) == pytest.approx((17, 11, 5))
    # bar3: EMA=13; TR=max(2,3,1)=3 -> 13 +/- 6
    assert (out[2].upper, out[2].middle, out[2].lower) == pytest.approx((19, 13, 7))


def test_keltner_config_and_validation():
    bars = list(zip(H[:40], L[:40], C[:40]))
    a = feed(mk("keltner"), *zip(*bars))[-1]
    assert feed(mk("keltner", mult=1.0), *zip(*bars))[-1].upper < a.upper
    assert feed(mk("keltner", atr_length=5), *zip(*bars))[-1].upper != a.upper
    assert feed(mk("keltner", length=10), *zip(*bars))[-1].middle != a.middle
    assert mk("keltner").warmup == 20 and mk("keltner", atr_length=30).warmup == 31
    for kw in ({"length": 0}, {"atr_length": 0}, {"mult": -1}):
        with pytest.raises(ValueError):
            mk("keltner", **kw)


# -- bollinger %B / bandwidth -------------------------------------------------


def test_percent_b_hand_values():
    # window 1,2,3: mean 2, pop sd sqrt(2/3)=0.8165; mult 1 -> lower 1.1835 upper 2.8165; close 3 -> 1.8165/1.633
    sd = math.sqrt(2 / 3)
    out = feed(mk("bollinger_percent_b", period=3, mult=1.0), [1, 2, 3])
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx((3 - (2 - sd)) / (2 * sd))
    assert feed(mk("bollinger_percent_b", period=3), [5, 5, 5])[-1] == 0.5  # flat -> 0.5 convention


def test_bandwidth_hand_values():
    # window 1,2,3: 100 * 2*mult*sd/mean = 100*2*1*0.8165/2
    sd = math.sqrt(2 / 3)
    assert feed(mk("bollinger_bandwidth", period=3, mult=1.0), [1, 2, 3])[-1] == pytest.approx(
        100 * sd
    )
    assert feed(mk("bollinger_bandwidth", period=3), [-1, 0, 1])[-1] == 0.0  # zero mean convention


@pytest.mark.parametrize("kind", ["bollinger_percent_b", "bollinger_bandwidth"])
def test_bollinger_variants_config_and_validation(kind):
    a = feed(mk(kind), C)[-1]
    assert feed(mk(kind, period=10), C)[-1] != a
    assert feed(mk(kind, mult=1.0), C)[-1] != a
    for kw in ({"period": 0}, {"mult": -0.1}):
        with pytest.raises(ValueError):
            mk(kind, **kw)


# -- envelope -----------------------------------------------------------------


def test_envelope_hand_values_and_ma_type():
    # SMA(3) of 1,2,3,4 = 3; +/-10% -> 3.3 / 2.7. EMA(3): seed 2, alpha .5 -> 3.
    out = feed(mk("envelope", length=3), [1, 2, 3, 4])[-1]
    assert (out.upper, out.middle, out.lower) == pytest.approx((3.3, 3.0, 2.7))
    e = feed(mk("envelope", length=3, ma_type="ema", percent=50), [1, 2, 3, 5])[-1]
    # EMA(3): SMA seed (1+2+3)/3=2, then 5*0.5 + 2*0.5 = 3.5; +50% -> 5.25
    assert e.middle == pytest.approx(3.5) and e.upper == pytest.approx(5.25)


def test_envelope_ema_differs_and_validation():
    s = feed(mk("envelope", length=5), C)[-1]
    e = feed(mk("envelope", length=5, ma_type="ema"), C)[-1]
    assert s.middle != e.middle
    assert feed(mk("envelope", length=5, percent=5), C)[-1].upper < s.upper
    for kw in ({"length": 0}, {"percent": -1}, {"ma_type": "wma"}):
        with pytest.raises(ValueError):
            mk("envelope", **kw)


# -- standard deviation / historical volatility -------------------------------


def test_standard_deviation_hand_value_and_validation():
    # 2,4,4,4,5,5,7,9 -> mean 5, population variance 4 -> sd 2
    assert feed(mk("standard_deviation", length=8), [2, 4, 4, 4, 5, 5, 7, 9])[-1] == pytest.approx(
        2.0
    )
    assert feed(mk("standard_deviation", length=2), [1, 3])[-1] == pytest.approx(1.0)
    assert (
        feed(mk("standard_deviation", length=3), C)[-1]
        != feed(mk("standard_deviation", length=6), C)[-1]
    )
    with pytest.raises(ValueError):
        mk("standard_deviation", length=0)


def test_historical_volatility_hand_value_and_config():
    # closes 1,2,1 -> log returns ln2, -ln2; mean 0; pop sd = ln2; x sqrt(252) x 100
    out = feed(mk("historical_volatility", length=2), [1, 2, 1])
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(100 * math.log(2) * math.sqrt(252))
    assert feed(mk("historical_volatility", length=2, periods_per_year=365), [1, 2, 1])[
        2
    ] == pytest.approx(100 * math.log(2) * math.sqrt(365))
    assert (
        feed(mk("historical_volatility", length=5), C)[-1]
        != feed(mk("historical_volatility", length=9), C)[-1]
    )
    assert mk("historical_volatility").warmup == 11
    for kw in ({"length": 0}, {"periods_per_year": 0}):
        with pytest.raises(ValueError):
            mk("historical_volatility", **kw)


# -- chaikin volatility -------------------------------------------------------


def test_chaikin_volatility_hand_value():
    # ranges 2,4,6: EMA(2) seed (2+4)/2=3 at bar2, bar3 = 6*(2/3)+3/3 = 5. ROC(1) = 100*(5-3)/3
    out = feed(mk("chaikin_volatility", length=2, roc_length=1), [12, 14, 16], [10, 10, 10])
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(100 * 2 / 3)


def test_chaikin_volatility_config_validation():
    a = feed(mk("chaikin_volatility"), H, L)[-1]
    assert feed(mk("chaikin_volatility", length=5), H, L)[-1] != a
    assert feed(mk("chaikin_volatility", roc_length=5), H, L)[-1] != a
    assert mk("chaikin_volatility").warmup == 20
    assert feed(mk("chaikin_volatility", length=1, roc_length=1), [5, 5, 5], [5, 5, 5])[-1] == 0.0
    for kw in ({"length": 0}, {"roc_length": 0}):
        with pytest.raises(ValueError):
            mk("chaikin_volatility", **kw)


# -- choppiness ---------------------------------------------------------------


def test_choppiness_hand_value():
    # 2 bars: (h,l,c) (10,8,9), (11,9,10): TR1=2 (h-l), TR2=max(2,2,0)=2 -> sum 4; range 11-8=3
    out = feed(mk("choppiness_index", length=2), [10, 11], [8, 9], [9, 10])
    assert out[1] == pytest.approx(100 * math.log10(4 / 3) / math.log10(2))
    assert feed(mk("choppiness_index", length=2), [5, 5], [5, 5], [5, 5])[-1] == 0.0


def test_choppiness_config_validation():
    assert (
        feed(mk("choppiness_index", length=5), H, L, C)[-1]
        != feed(mk("choppiness_index"), H, L, C)[-1]
    )
    for n in (0, 1):
        with pytest.raises(ValueError):
            mk("choppiness_index", length=n)


# -- volatility stop ----------------------------------------------------------


def test_volatility_stop_hand_values():
    # length 1 (ATR = TR, first TR = h-l), mult 1. Bar1 (h11,l9,c10): ATR=2, seed stop=10-2=8, up.
    # Bar2 (h13,l11,c12): TR=3; max=12; stop=max(8,12-3)=9; up. Bar3 (h9,l7,c8): TR=max(2,|9-12|,|7-12|)=5;
    # stop=max(9,12-5)=9; close-stop=-1<0 -> flip down: stop=8+5=13, direction -1.
    v = mk("volatility_stop", length=1, mult=1.0)
    out = feed(v, [11, 13, 9], [9, 11, 7], [10, 12, 8])
    assert (out[0].value, out[0].direction) == (8, 1)
    assert (out[1].value, out[1].direction) == (9, 1)
    assert (out[2].value, out[2].direction) == (13, -1)


def test_volatility_stop_config_validation():
    a = feed(mk("volatility_stop"), H, L, C)
    b = feed(mk("volatility_stop", mult=1.0), H, L, C)
    assert [x.value for x in a[19:]] != [x.value for x in b[19:]]
    assert {x.direction for x in a[19:]} <= {1.0, -1.0}
    for kw in ({"length": 0}, {"mult": 0}):
        with pytest.raises(ValueError):
            mk("volatility_stop", **kw)


def test_volatility_stop_seeds_below_close_not_at_close():
    """First stop is close - mult*ATR (9.4 - 2*0.1 = 9.2), so a small dip does not flip it."""
    from honba.strategies.indicators import build_indicator

    v = build_indicator("volatility_stop", length=3, mult=2.0)
    outs = [v.update(c, c, c) for c in (9.5, 9.6, 9.4)]
    assert outs[:2] == [None, None]
    assert outs[2].value == pytest.approx(9.2) and outs[2].direction == 1.0
    dip = v.update(9.3, 9.3, 9.3)  # buggy seeding (stop = close) flipped to a downtrend here
    assert dip.direction == 1.0 and dip.value == pytest.approx(9.2)
