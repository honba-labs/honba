"""O(1) plain-sum indicators (batch 4) vs. the original O(window) oracles.

Seven Python indicators moved their rolling window to the shared ``RollingSum`` helper (or a
``deque`` for chaikin volatility): VWMA, Chaikin money flow, money flow index, vortex, chande
momentum, ultimate oscillator and chaikin volatility.  This module keeps the original O(window)
implementations as oracles and adds an exact ``Fraction`` oracle for the pure-sum indicators,
plus warm-up, non-finite/overflow, cascade, flat-window, ``update_bar``/``reset`` and amortised
cost coverage.

Tolerance: rel/abs 1e-9, as batches 1-2.  The streamed sums differ from the recomputed oracle by
float rounding only, bounded by the periodic exact rebuilds; 1e-9 is several orders above that.
"""

import math
import random
from collections import deque
from fractions import Fraction

import pytest

from honba.strategies.indicators import build_indicator
from honba.strategies.indicators.moving_average.averages import Ema

N_BARS = 12_000
TOL = {"rel": 1e-9, "abs": 1e-9}
NAN, INF = math.nan, math.inf


# -- seeded series (the same ones as test_indicators_rolling_o1) ----------------


def _series() -> dict[str, list[float]]:
    r = random.Random(1234)
    walk, p = [], 100.0
    for _ in range(N_BARS):
        p = max(1.0, p + r.gauss(0, 1))
        walk.append(p)
    return {
        "walk": walk,
        "high_level_low_var": [20000 + r.gauss(0, 1e-3) for _ in range(N_BARS)],
        "high_level_drift": [20000 + 0.01 * i + r.gauss(0, 1e-2) for i in range(N_BARS)],
        "flat_then_noise": [5.0] * 500
        + [5.0 + r.gauss(0, 1) for _ in range(500)]
        + [7.25] * 500
        + [r.uniform(-1, 1) for _ in range(N_BARS - 1500)],
    }


SERIES = _series()
SIDS = list(SERIES)


def _rows(xs):
    """Derive ``(high, low, close, volume)`` columns with distinct, varying values."""
    hi = [x + 0.5 + 0.3 * ((i % 5) - 2) for i, x in enumerate(xs)]
    lo = [x - 0.5 - 0.2 * ((i % 7) - 3) for i, x in enumerate(xs)]
    vol = [1000.0 + 7.0 * (i % 13) for i in range(len(xs))]
    return hi, lo, list(xs), vol


# -- comparison helpers ---------------------------------------------------------


def _flat(v):
    if v is None:
        return None
    if hasattr(v, "__dataclass_fields__"):
        return tuple(float(getattr(v, f)) for f in v.__dataclass_fields__)
    if isinstance(v, tuple):
        return tuple(float(x) for x in v)
    return (float(v),)


def _cmp(a, b, **tol):
    if a is None or b is None:
        return a is b
    if isinstance(a, tuple) and isinstance(b, tuple):
        return len(a) == len(b) and all(_cmp(x, y, **tol) for x, y in zip(a, b))
    if not math.isfinite(b):
        return (not math.isfinite(a)) or (math.isnan(a) and math.isnan(b))
    return a == pytest.approx(b, **tol)


def _all_same(got, want):
    assert len(got) == len(want)
    for i, (a, b) in enumerate(zip(got, want)):
        assert _cmp(_flat(a), _flat(b), **TOL), (i, a, b)


def _run(kind, kw, cols):
    ind = build_indicator(kind, **kw)
    return [ind.update(*row) for row in zip(*cols)]


# -- old O(window) oracles ------------------------------------------------------


def _clv(high, low, close, volume):
    rng = high - low
    return 0.0 if rng == 0 else ((close - low) - (high - close)) / rng * volume


def old_vwma(close, vol, period):
    w, out = deque(maxlen=period), []
    for c, v in zip(close, vol):
        w.append((c, v))
        if len(w) < period:
            out.append(None)
            continue
        sv = sum(x[1] for x in w)
        out.append(float(c) if sv == 0.0 else sum(a * b for a, b in w) / sv)
    return out


def old_cmf(hi, lo, close, vol, period):
    w, out = deque(maxlen=period), []
    for h, l, c, v in zip(hi, lo, close, vol):
        w.append((_clv(h, l, c, v), v))
        if len(w) < period:
            out.append(None)
            continue
        sv = sum(x[1] for x in w)
        out.append(sum(x[0] for x in w) / sv if sv else 0.0)
    return out


def old_mfi(hi, lo, close, vol, period):
    prev, w, out = None, deque(maxlen=period), []
    for h, l, c, v in zip(hi, lo, close, vol):
        tp = (h + l + c) / 3.0
        p, prev = prev, tp
        if p is None:
            out.append(None)
            continue
        raw = tp * v
        w.append((raw if tp > p else 0.0, raw if tp < p else 0.0))
        if len(w) < period:
            out.append(None)
            continue
        pos, neg = sum(x[0] for x in w), sum(x[1] for x in w)
        if neg == 0:
            out.append(50.0 if pos == 0 else 100.0)
        else:
            out.append(100.0 - 100.0 / (1.0 + pos / neg))
    return out


def old_vortex(hi, lo, close, period):
    prev, w, out = None, deque(maxlen=period), []
    for h, l, c in zip(hi, lo, close):
        p, prev = prev, (h, l, c)
        if p is None:
            out.append(None)
            continue
        ph, pl, pc = p
        w.append((abs(h - pl), abs(l - ph), max(h - l, abs(h - pc), abs(l - pc))))
        if len(w) < period:
            out.append(None)
            continue
        tr = sum(t for _, _, t in w)
        if tr <= 0:
            out.append((0.0, 0.0))
        else:
            out.append((sum(a for a, _, _ in w) / tr, sum(b for _, b, _ in w) / tr))
    return out


def old_cmo(close, length):
    prev, w, out = None, deque(maxlen=length), []
    for c in close:
        p, prev = prev, c
        if p is None:
            out.append(None)
            continue
        w.append(c - p)
        if len(w) < length:
            out.append(None)
            continue
        up, dn = sum(x for x in w if x > 0), sum(-x for x in w if x < 0)
        out.append(100.0 * (up - dn) / (up + dn) if up + dn != 0 else 0.0)
    return out


def old_uo(hi, lo, close, fast, middle, slow):
    n = max(fast, middle, slow)
    prev, bp, tr, out = None, deque(maxlen=n), deque(maxlen=n), []
    for h, l, c in zip(hi, lo, close):
        p, prev = prev, c
        if p is None:
            out.append(None)
            continue
        bp.append(c - min(l, p))
        tr.append(max(h, p) - min(l, p))
        if len(tr) < n:
            out.append(None)
            continue

        def avg(k):
            t = sum(list(tr)[-k:])
            return sum(list(bp)[-k:]) / t if t != 0 else 0.0

        out.append(100.0 * (4 * avg(fast) + 2 * avg(middle) + avg(slow)) / 7.0)
    return out


def old_cv(hi, lo, length, roc_length):
    ema, hist, out = Ema(length), [], []
    for h, l in zip(hi, lo):
        e = ema.update(h - l)
        if e is None:
            out.append(None)
            continue
        hist.append(e)
        if len(hist) > roc_length + 1:
            hist.pop(0)
        if len(hist) <= roc_length:
            out.append(None)
            continue
        old = hist[0]
        out.append(0.0 if old == 0.0 else 100.0 * (e - old) / old)
    return out


# -- exact Fraction oracles -----------------------------------------------------


def exact_vwma(close, vol, start, end):
    fc = [Fraction(close[j]) for j in range(start, end + 1)]
    fv = [Fraction(vol[j]) for j in range(start, end + 1)]
    sv = sum(fv)
    if sv == 0:
        return float(close[end])
    return float(sum(a * b for a, b in zip(fc, fv)) / sv)


def exact_cmf(hi, lo, close, vol, start, end):
    mf = Fraction(0)
    for j in range(start, end + 1):
        rng = Fraction(hi[j]) - Fraction(lo[j])
        if rng != 0:
            clv = (
                (Fraction(close[j]) - Fraction(lo[j])) - (Fraction(hi[j]) - Fraction(close[j]))
            ) / rng
            mf += clv * Fraction(vol[j])
    sv = sum(Fraction(vol[j]) for j in range(start, end + 1))
    return float(mf / sv) if sv != 0 else 0.0


def exact_mfi(hi, lo, close, vol, start, end):
    pos = neg = Fraction(0)
    for j in range(start, end + 1):
        tp = (Fraction(hi[j]) + Fraction(lo[j]) + Fraction(close[j])) / 3
        pv = (Fraction(hi[j - 1]) + Fraction(lo[j - 1]) + Fraction(close[j - 1])) / 3
        raw = tp * Fraction(vol[j])
        if tp > pv:
            pos += raw
        elif tp < pv:
            neg += raw
    if neg == 0:
        return 50.0 if pos == 0 else 100.0
    return float(100 - Fraction(100) / (1 + pos / neg))


def exact_cmo(close, start, end):
    up = dn = Fraction(0)
    for j in range(start, end + 1):
        d = Fraction(close[j]) - Fraction(close[j - 1])
        if d > 0:
            up += d
        elif d < 0:
            dn += -d
    if up + dn == 0:
        return 0.0
    return float(100 * (up - dn) / (up + dn))


def exact_vortex(hi, lo, close, start, end):
    plus = minus = tr = Fraction(0)
    for j in range(start, end + 1):
        ph, pl, pc = Fraction(hi[j - 1]), Fraction(lo[j - 1]), Fraction(close[j - 1])
        h, l = Fraction(hi[j]), Fraction(lo[j])
        plus += abs(h - pl)
        minus += abs(l - ph)
        tr += max(h - l, abs(h - pc), abs(l - pc))
    if tr <= 0:
        return (0.0, 0.0)
    return (float(plus / tr), float(minus / tr))


def exact_uo(hi, lo, close, fast, middle, slow, start, end):
    fps, trs = [], []
    for j in range(start, end + 1):
        pc = Fraction(close[j - 1])
        l = min(Fraction(lo[j]), pc)
        h = max(Fraction(hi[j]), pc)
        fps.append(Fraction(close[j]) - l)
        trs.append(h - l)

    def avg(k):
        t = sum(trs[-k:])
        return sum(fps[-k:]) / t if t != 0 else Fraction(0)

    return float(Fraction(100) * (4 * avg(fast) + 2 * avg(middle) + avg(slow)) / 7)


# -- 1. indicators vs the old O(window) oracle on the shared series -------------


@pytest.mark.slow
@pytest.mark.parametrize("sid", SIDS)
def test_vwma_matches_old_oracle(sid):
    _, _, close, vol = _rows(SERIES[sid])
    period = 20
    _all_same(_run("vwma", {"period": period}, (close, vol)), old_vwma(close, vol, period))


@pytest.mark.slow
@pytest.mark.parametrize("sid", SIDS)
def test_chaikin_money_flow_matches_old_oracle(sid):
    hi, lo, close, vol = _rows(SERIES[sid])
    period = 20
    got = _run("chaikin_money_flow", {"period": period}, (hi, lo, close, vol))
    _all_same(got, old_cmf(hi, lo, close, vol, period))


@pytest.mark.slow
@pytest.mark.parametrize("sid", SIDS)
def test_money_flow_index_matches_old_oracle(sid):
    hi, lo, close, vol = _rows(SERIES[sid])
    period = 14
    got = _run("money_flow_index", {"period": period}, (hi, lo, close, vol))
    _all_same(got, old_mfi(hi, lo, close, vol, period))


@pytest.mark.slow
@pytest.mark.parametrize("sid", SIDS)
def test_vortex_matches_old_oracle(sid):
    hi, lo, close, _ = _rows(SERIES[sid])
    period = 14
    _all_same(
        _run("vortex", {"period": period}, (hi, lo, close)), old_vortex(hi, lo, close, period)
    )


@pytest.mark.slow
@pytest.mark.parametrize("sid", SIDS)
def test_chande_momentum_matches_old_oracle(sid):
    _, _, close, _ = _rows(SERIES[sid])
    length = 9
    _all_same(_run("cmo", {"length": length}, (close,)), old_cmo(close, length))


@pytest.mark.slow
@pytest.mark.parametrize("sid", SIDS)
def test_ultimate_oscillator_matches_old_oracle(sid):
    hi, lo, close, _ = _rows(SERIES[sid])
    fast, middle, slow = 7, 14, 28
    got = _run(
        "ultimate_oscillator", {"fast": fast, "middle": middle, "slow": slow}, (hi, lo, close)
    )
    _all_same(got, old_uo(hi, lo, close, fast, middle, slow))


@pytest.mark.slow
@pytest.mark.parametrize("sid", SIDS)
def test_chaikin_volatility_matches_old_oracle(sid):
    hi, lo, _, _ = _rows(SERIES[sid])
    length, roc_length = 10, 10
    got = _run("chaikin_volatility", {"length": length, "roc_length": roc_length}, (hi, lo))
    _all_same(got, old_cv(hi, lo, length, roc_length))


# -- 2. exact Fraction oracles on short distinct-value windows ------------------


@pytest.mark.parametrize("period", [2, 3, 4])
def test_vwma_exact_fraction(period):
    close = [10.0, 11.5, 9.25, 12.75, 8.0, 13.0, 10.5]
    vol = [3.0, 7.0, 2.0, 5.0, 11.0, 4.0, 6.0]
    ind = build_indicator("vwma", period=period)
    for i, (c, v) in enumerate(zip(close, vol)):
        got = ind.update(c, v)
        if i + 1 < period:
            assert got is None
            continue
        assert got == pytest.approx(exact_vwma(close, vol, i - period + 1, i), rel=1e-9, abs=1e-9)


@pytest.mark.parametrize("period", [2, 3, 4])
def test_chaikin_money_flow_exact_fraction(period):
    close = [10.0, 11.5, 9.25, 12.75, 8.0, 13.0, 10.5]
    hi = [10.6, 12.1, 9.8, 13.3, 8.5, 13.6, 11.0]
    lo = [9.4, 10.9, 8.7, 12.2, 7.6, 12.5, 10.0]
    vol = [3.0, 7.0, 2.0, 5.0, 11.0, 4.0, 6.0]
    ind = build_indicator("chaikin_money_flow", period=period)
    for i in range(len(close)):
        got = ind.update(hi[i], lo[i], close[i], vol[i])
        if i + 1 < period:
            assert got is None
            continue
        assert got == pytest.approx(
            exact_cmf(hi, lo, close, vol, i - period + 1, i), rel=1e-9, abs=1e-9
        )


@pytest.mark.parametrize("period", [2, 3, 4])
def test_money_flow_index_exact_fraction(period):
    close = [10.0, 11.5, 9.25, 12.75, 8.0, 13.0, 10.5]
    hi = [10.6, 12.1, 9.8, 13.3, 8.5, 13.6, 11.0]
    lo = [9.4, 10.9, 8.7, 12.2, 7.6, 12.5, 10.0]
    vol = [3.0, 7.0, 2.0, 5.0, 11.0, 4.0, 6.0]
    ind = build_indicator("money_flow_index", period=period)
    for i in range(len(close)):
        got = ind.update(hi[i], lo[i], close[i], vol[i])
        if i + 1 < period + 1:
            assert got is None
            continue
        assert got == pytest.approx(
            exact_mfi(hi, lo, close, vol, i - period + 1, i), rel=1e-9, abs=1e-9
        )


@pytest.mark.parametrize("length", [2, 3, 4])
def test_chande_momentum_exact_fraction(length):
    close = [10.0, 11.5, 9.25, 12.75, 8.0, 13.0, 10.5]
    ind = build_indicator("cmo", length=length)
    for i, c in enumerate(close):
        got = ind.update(c)
        if i + 1 < length + 1:
            assert got is None
            continue
        assert got == pytest.approx(exact_cmo(close, i - length + 1, i), rel=1e-9, abs=1e-9)


@pytest.mark.parametrize("period", [2, 3, 4])
def test_vortex_exact_fraction(period):
    close = [10.0, 11.5, 9.25, 12.75, 8.0, 13.0, 10.5]
    hi = [10.6, 12.1, 9.8, 13.3, 8.5, 13.6, 11.0]
    lo = [9.4, 10.9, 8.7, 12.2, 7.6, 12.5, 10.0]
    ind = build_indicator("vortex", period=period)
    for i in range(len(close)):
        got = ind.update(hi[i], lo[i], close[i])
        if i + 1 < period + 1:
            assert got is None
            continue
        want = exact_vortex(hi, lo, close, i - period + 1, i)
        assert (got.plus, got.minus) == pytest.approx(want, rel=1e-9, abs=1e-9)


@pytest.mark.parametrize("length", [2, 3, 4])
def test_ultimate_oscillator_exact_fraction(length):
    close = [10.0, 11.5, 9.25, 12.75, 8.0, 13.0, 10.5]
    hi = [10.6, 12.1, 9.8, 13.3, 8.5, 13.6, 11.0]
    lo = [9.4, 10.9, 8.7, 12.2, 7.6, 12.5, 10.0]
    ind = build_indicator("ultimate_oscillator", fast=length, middle=length, slow=length)
    for i in range(len(close)):
        got = ind.update(hi[i], lo[i], close[i])
        if i + 1 < length + 1:
            assert got is None
            continue
        want = exact_uo(hi, lo, close, length, length, length, i - length + 1, i)
        assert got == pytest.approx(want, rel=1e-9, abs=1e-9)


# -- 3. warm-up, reset, update_bar ----------------------------------------------


@pytest.mark.parametrize(
    "kind,kw,warm",
    [
        ("vwma", {"period": 20}, 20),
        ("chaikin_money_flow", {"period": 20}, 20),
        ("money_flow_index", {"period": 14}, 15),
        ("vortex", {"period": 14}, 15),
        ("cmo", {"length": 9}, 10),
        ("ultimate_oscillator", {"fast": 7, "middle": 14, "slow": 28}, 29),
        ("chaikin_volatility", {"length": 10, "roc_length": 10}, 20),
    ],
)
def test_warmup_unchanged(kind, kw, warm):
    hi, lo, close, vol = _rows(SERIES["walk"])
    cols = {"close": close, "high": hi, "low": lo, "volume": vol}
    ind = build_indicator(kind, **kw)
    rows = list(zip(*[cols[f] for f in ind.inputs]))
    outs = [ind.update(*r) for r in rows[: warm + 3]]
    assert all(o is None for o in outs[: warm - 1])
    assert outs[warm - 1] is not None
    assert ind.warmup == warm


class _Bar:
    def __init__(self, close, high, low, volume):
        self.close, self.high, self.low, self.volume = close, high, low, volume


@pytest.mark.parametrize(
    "kind,kw",
    [
        ("vwma", {"period": 10}),
        ("chaikin_money_flow", {"period": 10}),
        ("money_flow_index", {"period": 14}),
        ("vortex", {"period": 14}),
        ("cmo", {"length": 9}),
        ("ultimate_oscillator", {"fast": 7, "middle": 14, "slow": 28}),
        ("chaikin_volatility", {"length": 10, "roc_length": 10}),
    ],
)
def test_update_bar_and_reset(kind, kw):
    hi, lo, close, vol = _rows(SERIES["walk"])
    field = {"high": hi, "low": lo, "close": close, "volume": vol}
    a, b = build_indicator(kind, **kw), build_indicator(kind, **kw)
    rows = []
    for i in range(400):
        bar = _Bar(close=close[i], high=hi[i], low=lo[i], volume=vol[i])
        row = tuple(field[f][i] for f in b.inputs)
        rows.append(row)
        assert _cmp(_flat(a.update_bar(bar)), _flat(b.update(*row)))
    c = build_indicator(kind, **kw)
    first = [c.update(*r) for r in rows]
    c.reset()
    again = [c.update(*r) for r in rows]
    for x, y in zip(again, first):
        assert _cmp(_flat(x), _flat(y))


def test_flat_windows_degenerate_results():
    ind = build_indicator("vwma", period=3)
    assert [ind.update(5.0, 0.0) for _ in range(3)][-1] == 5.0  # zero volume -> latest close
    ind = build_indicator("chaikin_money_flow", period=3)
    assert [ind.update(5.0, 5.0, 5.0, 100.0) for _ in range(3)][-1] == 0.0
    ind = build_indicator("money_flow_index", period=3)
    assert [ind.update(5.0, 5.0, 5.0, 100.0) for _ in range(4)][-1] == 50.0
    ind = build_indicator("vortex", period=3)
    out = [ind.update(5.0, 5.0, 5.0) for _ in range(4)][-1]
    assert (out.plus, out.minus) == (0.0, 0.0)
    ind = build_indicator("cmo", length=3)
    assert [ind.update(5.0) for _ in range(4)][-1] == 0.0
    ind = build_indicator("ultimate_oscillator", fast=3, middle=3, slow=3)
    assert [ind.update(5.0, 5.0, 5.0) for _ in range(5)][-1] == 0.0
    ind = build_indicator("chaikin_volatility", length=2, roc_length=2)
    assert [ind.update(5.0, 5.0) for _ in range(6)][-1] == 0.0


# -- 4. non-finite / overflow convention ----------------------------------------


@pytest.mark.parametrize("bad", [NAN, INF, -INF, 1e160, -1e160])
def test_vwma_bad_close_nan_while_in_window_then_recovers(bad):
    period, at = 5, 10
    r = random.Random(11)
    close = [100.0 + r.gauss(0, 1) for _ in range(60)]
    vol = [1000.0 + r.random() for _ in range(60)]
    close[at] = bad
    ind = build_indicator("vwma", period=period)
    for i in range(len(close)):
        got = ind.update(close[i], vol[i])
        if i + 1 < period:
            assert got is None
        elif at <= i < at + period:
            assert math.isnan(got), (i, got)
        elif i >= at + period:
            assert got == pytest.approx(
                exact_vwma(close, vol, i - period + 1, i), rel=1e-9, abs=1e-9
            )


@pytest.mark.parametrize("bad", [NAN, INF, 1e160])
def test_chaikin_money_flow_bad_volume_nan_then_recovers(bad):
    period, at = 4, 12
    r = random.Random(12)
    close = [100.0 + r.gauss(0, 1) for _ in range(60)]
    hi = [c + 1.0 for c in close]
    lo = [c - 1.0 for c in close]
    vol = [1000.0 + r.random() for _ in range(60)]
    vol[at] = bad
    ind = build_indicator("chaikin_money_flow", period=period)
    for i in range(len(close)):
        got = ind.update(hi[i], lo[i], close[i], vol[i])
        if i + 1 < period:
            assert got is None
        elif at <= i < at + period:
            assert math.isnan(got), (i, got)
        elif i >= at + period:
            assert got == pytest.approx(
                exact_cmf(hi, lo, close, vol, i - period + 1, i), rel=1e-9, abs=1e-9
            )


@pytest.mark.parametrize("bad", [NAN, INF, 1e160])
def test_money_flow_index_bad_volume_nan_then_recovers(bad):
    period, at = 4, 12
    close = [100.0 + (1.0 if j % 2 else 0.0) for j in range(60)]
    hi = [c + 0.5 for c in close]
    lo = [c - 0.5 for c in close]
    vol = [1000.0 + 10.0 * j for j in range(60)]
    vol[at] = bad
    ind = build_indicator("money_flow_index", period=period)
    for i in range(len(close)):
        got = ind.update(hi[i], lo[i], close[i], vol[i])
        if i + 1 < period + 1:
            assert got is None
        elif at <= i < at + period:
            assert math.isnan(got), (i, got)
        elif i >= at + period:
            assert got == pytest.approx(
                exact_mfi(hi, lo, close, vol, i - period + 1, i), rel=1e-9, abs=1e-9
            )


@pytest.mark.parametrize("bad", [NAN, INF, 1e160])
def test_vortex_bad_high_nan_then_recovers(bad):
    period, at = 4, 12
    r = random.Random(13)
    close = [100.0 + r.gauss(0, 1) for _ in range(60)]
    hi = [c + 1.0 for c in close]
    lo = [c - 1.0 for c in close]
    hi[at] = bad
    ind = build_indicator("vortex", period=period)
    for i in range(len(close)):
        got = ind.update(hi[i], lo[i], close[i])
        if i + 1 < period + 1:
            assert got is None
        elif at <= i <= at + period:  # flow at poisons plus, flow at+1 poisons minus
            assert math.isnan(got.plus) or math.isnan(got.minus), (i, got)
        else:
            want = exact_vortex(hi, lo, close, i - period + 1, i)
            assert (got.plus, got.minus) == pytest.approx(want, rel=1e-9, abs=1e-9)


@pytest.mark.parametrize("bad", [INF, 1e308, -1e308])
def test_chande_momentum_bad_close_overflow_recovers(bad):
    length, at = 4, 12
    r = random.Random(14)
    close = [100.0 + r.gauss(0, 1) for _ in range(60)]
    close[at] = bad
    ind = build_indicator("cmo", length=length)
    for i in range(len(close)):
        got = ind.update(close[i])
        if i + 1 < length + 1:
            assert got is None
        elif at <= i < at + length + 1:
            assert math.isnan(got), (i, got)
        elif i >= at + length + 1:
            assert got == pytest.approx(exact_cmo(close, i - length + 1, i), rel=1e-9, abs=1e-9)


@pytest.mark.parametrize("bad", [NAN, INF, 1e160])
def test_ultimate_oscillator_bad_high_nan_then_recovers(bad):
    length, at = 4, 12
    r = random.Random(15)
    close = [100.0 + r.gauss(0, 1) for _ in range(60)]
    hi = [c + 1.0 for c in close]
    lo = [c - 1.0 for c in close]
    hi[at] = bad  # max(high, prev) keeps a NaN first argument, so a NaN high is not masked
    ind = build_indicator("ultimate_oscillator", fast=length, middle=length, slow=length)
    for i in range(len(close)):
        got = ind.update(hi[i], lo[i], close[i])
        if i + 1 < length + 1:
            assert got is None
        elif at <= i < at + length:
            assert math.isnan(got), (i, got)
        else:
            want = exact_uo(hi, lo, close, length, length, length, i - length + 1, i)
            assert got == pytest.approx(want, rel=1e-9, abs=1e-9)


def test_chaikin_volatility_inf_range_matches_old():
    length, roc_length = 3, 2
    r = random.Random(16)
    close = [100.0 + r.gauss(0, 1) for _ in range(30)]
    hi = [c + 1.0 for c in close]
    lo = [c - 1.0 for c in close]
    hi[8] = INF
    got = _run("chaikin_volatility", {"length": length, "roc_length": roc_length}, (hi, lo))
    want = old_cv(hi, lo, length, roc_length)
    _all_same(got, want)


# -- 5. outlier cascade: residue must not survive the spike ---------------------


@pytest.mark.parametrize("period", [5, 20])
def test_vwma_no_residue_after_outlier_cascade(period):
    r = random.Random(9)
    close = [100 + r.gauss(0, 1e-3) for _ in range(40)]
    for k in range(6, 12):
        close += [100 + 10.0**k] + [100 + r.gauss(0, 1e-3) for _ in range(period - 1)]
    close += [100 + r.gauss(0, 1e-3) for _ in range(120)]
    vol = [1000.0] * len(close)
    ind, w = build_indicator("vwma", period=period), deque(maxlen=period)
    for i, (c, v) in enumerate(zip(close, vol)):
        w.append((c, v))
        got = ind.update(c, v)
        if len(w) == period:
            assert got == pytest.approx(
                exact_vwma(close, vol, i - period + 1, i), rel=1e-9, abs=1e-9
            )


# -- 6. amortised cost: no O(period) work per update ----------------------------


def _batch_config(kind, period):
    if kind == "vwma":
        return {"period": period}, 2
    if kind == "chaikin_money_flow":
        return {"period": period}, 4
    if kind == "money_flow_index":
        return {"period": period}, 4
    if kind == "vortex":
        return {"period": period}, 3
    if kind == "cmo":
        return {"length": period}, 1
    if kind == "ultimate_oscillator":
        return {"fast": period, "middle": period, "slow": period}, 3
    if kind == "chaikin_volatility":
        return {"length": period, "roc_length": period}, 2
    raise AssertionError(kind)


BATCH = [
    "vwma",
    "chaikin_money_flow",
    "money_flow_index",
    "vortex",
    "cmo",
    "ultimate_oscillator",
    "chaikin_volatility",
]


def test_update_cost_independent_of_window():
    """Count float operations via a value type that tallies arithmetic calls."""

    class Counted(float):
        ops = 0

        def _c(self, o, f):
            Counted.ops += 1
            return Counted(f(float(self), float(o)))

        def __add__(s, o):
            return s._c(o, float.__add__)

        def __radd__(s, o):
            return s._c(o, float.__add__)

        def __sub__(s, o):
            return s._c(o, float.__sub__)

        def __rsub__(s, o):
            return s._c(o, float.__rsub__)

        def __mul__(s, o):
            return s._c(o, float.__mul__)

        def __rmul__(s, o):
            return s._c(o, float.__mul__)

        def __truediv__(s, o):
            return s._c(o, float.__truediv__)

        def __rtruediv__(s, o):
            return s._c(o, float.__rtruediv__)

    def ops_per_update(kind, period):
        kw, nin = _batch_config(kind, period)
        ind = build_indicator(kind, **kw)
        r = random.Random(1)

        def feed():
            return ind.update(*[Counted(1 + r.random()) for _ in range(nin)])

        warm = ind.warmup or (2 * period + 10)
        for _ in range(warm + 10):
            feed()
        Counted.ops = 0
        for _ in range(50):
            feed()
        return Counted.ops / 50

    for kind in BATCH:
        small, big = ops_per_update(kind, 10), ops_per_update(kind, 400)
        assert small > 0, (kind, small, big)
        assert big <= small * 1.5 + 1, (kind, small, big)


def test_no_python_level_window_scan_per_update():
    """Independent of the op counter: a per-update ``fsum`` scan over the window would show up."""
    import honba.strategies.indicators._rolling as mod

    calls = {"n": 0}
    real = mod.math.fsum

    def counting(it):
        calls["n"] += 1
        return real(it)

    r = random.Random(2)
    inds = [(k, build_indicator(k, **_batch_config(k, 100)[0])) for k in BATCH]
    mod.math.fsum = counting
    try:
        for _ in range(2000):
            vals = [100 + r.gauss(0, 1) for _ in range(4)]
            for k, ind in inds:
                ind.update(*vals[: _batch_config(k, 100)[1]])
    finally:
        mod.math.fsum = real
    assert calls["n"] <= 2000 * len(BATCH) * 0.05 * 8, calls["n"]


def test_rolling_sum_rebuilds_are_amortised():
    """The grid of ``RollingSum`` instances in each indicator rebuilds only rarely."""

    def rate(kind, period, bars=6000):
        import honba.strategies.indicators._rolling as mod

        calls = {"n": 0}
        real = mod.RollingSum._rebuild

        def counting(self):
            calls["n"] += 1
            real(self)

        kw, nin = _batch_config(kind, period)
        ind = build_indicator(kind, **kw)
        r = random.Random(7)
        mod.RollingSum._rebuild = counting
        try:
            for _ in range(bars):
                ind.update(*[100 + r.gauss(0, 1) for _ in range(nin)])
        finally:
            mod.RollingSum._rebuild = real
        return calls["n"] * period / bars

    for kind in BATCH:
        rebuild_cost = rate(kind, 100)
        assert rebuild_cost < 10.0, (kind, rebuild_cost)
