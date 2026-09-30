"""O(1) paired-moment / regression helpers and the indicators built on them (batch 2).

Oracles: (1) the original O(window) implementations (``old_*``, copied verbatim from the code
that batch 2 replaced) and (2) exact ``Fraction`` arithmetic (``exact_*``), which is the accuracy
reference.  Streaming results are compared with exact values; the old code is used as a second
oracle where it is itself accurate.

Error metric.  Signed quantities (covariance, slope, intercept, line value, beta, correlation)
use ``|got - want| / max(|want|, 1e-4 * natural_scale)`` with a per-window natural scale
(sqrt(var_x*var_y) for covariance, sigma_y/sigma_x for slope, ...): the floor is the cancellation
guard's own threshold, so a quantity that crosses zero is not compared to an absolute zero.  The
threshold is 1e-9 everywhere (the indicator-level std, read back from upper - value, is given an
allowance of two ulps of the output for that subtraction's own rounding).  The residual std of a
regression is relative (1e-9) while the fit is not near-perfect (std >= 1e-2 sigma_y) and absolute 1e-7 * sigma_y otherwise (subtracting two
nearly equal sums of squares cannot do better than sqrt(eps) * sigma_y).
"""

import math
import random
from collections import deque
from fractions import Fraction

import pytest

from honba.strategies.indicators import build_indicator
from honba.strategies.indicators.statistical._pair import simple_return

N_BARS = 12_000
NAN, INF = math.nan, math.inf
HUGE = 1e150
TOL = 1e-9

LEN = {
    "correlation": "length",
    "beta": "length",
    "covariance": "length",
    "lsma": "period",
    "linear_regression": "length",
}
RETURNS = {"beta", "covariance"}  # these indicators work on simple returns of their inputs
SINGLE = {"lsma", "linear_regression"}  # one input (close)
PAIR_KINDS = ["correlation", "beta", "covariance"]
REG_KINDS = ["lsma", "linear_regression"]
ALL_KINDS = PAIR_KINDS + REG_KINDS


# -- oracles: the original O(window) implementations ---------------------------------------------


def old_cov_var(xs, ys, ddof):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    d = n - ddof
    cxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / d
    vx = sum((x - mx) ** 2 for x in xs) / d
    vy = sum((y - my) ** 2 for y in ys) / d
    return cxy, vx, vy


def old_linreg_fit(ys):
    n = len(ys)
    sx = n * (n - 1) / 2.0
    sxx = (n - 1) * n * (2 * n - 1) / 6.0
    sy = sxy = 0.0
    for i, y in enumerate(ys):
        sy += y
        sxy += i * y
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    return (sy - slope * sx) / n, slope


def old_value(kind, xs, ys, offset=0):
    xs, ys = list(xs), list(ys)
    if kind == "correlation":
        c, vx, vy = old_cov_var(xs, ys, 0)
        if vx <= 0 or vy <= 0:
            return {"v": 0.0}
        return {"v": max(-1.0, min(1.0, c / math.sqrt(vx * vy)))}
    if kind == "beta":
        c, _, vy = old_cov_var(xs, ys, 0)
        return {"v": c / vy if vy > 0 else 0.0}
    if kind == "covariance":
        return {"v": old_cov_var(xs, ys, 1)[0]}
    a, b = old_linreg_fit(ys)
    n = len(ys)
    if kind == "lsma":
        return {"v": a + b * (n - 1 - offset)}
    std = math.sqrt(sum((y - (a + b * i)) ** 2 for i, y in enumerate(ys)) / n)
    return {"value": a + b * (n - 1), "slope": b, "std": std}


# -- oracles: exact Fractions --------------------------------------------------------------------


def _sqrtf(q: Fraction) -> float:
    return math.sqrt(float(q))


def exact_pair_sums(xs, ys):
    fx, fy = [Fraction(v) for v in xs], [Fraction(v) for v in ys]
    n = len(fx)
    mx, my = sum(fx) / n, sum(fy) / n
    sxx = sum((a - mx) ** 2 for a in fx)
    syy = sum((b - my) ** 2 for b in fy)
    sxy = sum((a - mx) * (b - my) for a, b in zip(fx, fy))
    return n, mx, my, sxx, syy, sxy


def exact_reg_sums(ys):
    fy = [Fraction(v) for v in ys]
    n = len(fy)
    my = sum(fy) / n
    cx = Fraction(n - 1, 2)
    sxx = Fraction(n * (n * n - 1), 12)
    syy = sum((b - my) ** 2 for b in fy)
    sxy = sum((i - cx) * b for i, b in enumerate(fy))
    return n, my, sxx, syy, sxy


def exact_value(kind, xs, ys, offset=0):
    """(want dict, natural-scale dict) from exact arithmetic, each rounded once to float."""
    if kind in PAIR_KINDS:
        n, _, _, sxx, syy, sxy = exact_pair_sums(xs, ys)
        if kind == "correlation":
            if sxx <= 0 or syy <= 0:
                return {"v": 0.0}, {"v": 1.0}
            r = _sqrtf(sxy * sxy / (sxx * syy))
            return {"v": max(-1.0, min(1.0, math.copysign(r, float(sxy))))}, {"v": 1.0}
        if kind == "beta":
            sc = _sqrtf(sxx / syy) if syy > 0 else 1.0
            return {"v": float(sxy / syy) if syy > 0 else 0.0}, {"v": sc}
        return {"v": float(sxy / (n - 1))}, {"v": _sqrtf(sxx * syy) / (n - 1)}
    n, my, sxx, syy, sxy = exact_reg_sums(ys)
    slope = sxy / sxx
    half = Fraction(n - 1, 2)
    sse = max(syy - sxy * sxy / sxx, Fraction(0))
    sig = _sqrtf(syy / n)
    slope_scale = _sqrtf(syy / sxx) if syy > 0 else 1.0
    if kind == "lsma":
        v = my + slope * (half - offset)
        nat = abs(float(my)) + abs(float(slope)) * abs(float(half - offset))
        return {"v": float(v)}, {"v": nat}
    end = my + slope * half
    nat = abs(float(my)) + abs(float(slope)) * float(half)
    want = {"value": float(end), "slope": float(slope), "std": _sqrtf(sse / n)}
    return want, {"value": nat, "slope": slope_scale, "std": sig}


def exact_intercept(ys):
    n, my, sxx, _, sxy = exact_reg_sums(ys)
    v = my - (sxy / sxx) * Fraction(n - 1, 2)
    return float(v), abs(float(my)) + abs(float(sxy / sxx)) * (n - 1) / 2


def _err(got, want, scale):
    if not math.isfinite(got):
        return math.inf
    return abs(got - want) / max(abs(want), 1e-4 * scale, 1e-300)  # degenerate: exact zero


def errors(got: dict, want: dict, sc: dict) -> dict:
    out = {}
    for k, w in want.items():
        g = got[k]
        if k == "std":
            d = w if w >= 1e-2 * sc[k] else 100.0 * sc[k]
            d = d if d > 0 else 1e-9  # constant window: std must be exactly 0
            diff = max(0.0, abs(g - w) - 2.0 * got.get("ulp", 0.0))  # output rounding
            out[k] = diff / d if math.isfinite(g) else math.inf
        else:
            out[k] = _err(g, w, sc[k])
    return out


# -- stream harness ------------------------------------------------------------------------------


def as_dict(kind, got):
    if isinstance(got, float):
        return {"v": got}
    # std is read back from upper - value, which carries the output's own rounding (see errors())
    return {
        "value": got.value,
        "slope": got.slope,
        "std": (got.upper - got.value) / 2.0,
        "ulp": math.ulp(max(abs(got.value), abs(got.upper), abs(got.lower))),
    }


def is_nan_result(got):
    if isinstance(got, float):
        return math.isnan(got)
    return all(math.isnan(v) for v in (got.value, got.slope, got.upper, got.lower))


def unsafe(v):
    return not abs(v) <= HUGE


class Stream:
    """Feeds (x, y) to an indicator and keeps the window the old code would have seen."""

    def __init__(self, kind, period, offset=0):
        self.kind, self.period, self.offset = kind, period, offset
        kw = {LEN[kind]: period}
        if kind == "lsma":
            kw["offset"] = offset
        self.ind = build_indicator(kind, **kw)
        self.wx, self.wy = deque(maxlen=period), deque(maxlen=period)
        self.prev = None

    def step(self, x, y):
        got = self.ind.update(y) if self.kind in SINGLE else self.ind.update(x, y)
        if self.kind in RETURNS:
            prev, self.prev = self.prev, (x, y)
            if prev is not None:
                self.wx.append(simple_return(prev[0], x))
                self.wy.append(simple_return(prev[1], y))
        else:
            self.wx.append(x)
            self.wy.append(y)
        return got

    @property
    def full(self):
        return len(self.wy) == self.period

    @property
    def unsafe(self):
        if self.kind in SINGLE:
            return any(unsafe(v) for v in self.wy)
        return any(unsafe(v) for v in self.wx) or any(unsafe(v) for v in self.wy)


def run(kind, period, xs, ys, *, stride=1, old=True, offset=0, record=None):
    """Streams the series; returns the worst error per field versus exact (and versus old)."""
    st = Stream(kind, period, offset)
    worst, worst_old = {}, {}
    for i, (x, y) in enumerate(zip(xs, ys)):
        got = st.step(x, y)
        if not st.full:
            assert got is None, (i, got)
            continue
        assert got is not None, i
        if st.unsafe:
            assert is_nan_result(got), (i, got)
            continue
        if stride > 1 and i % stride and not (1150 <= i < 1260 or 2350 <= i < 2460):
            continue
        wx, wy = list(st.wx), list(st.wy)
        want, sc = exact_value(kind, wx, wy, offset)
        g = as_dict(kind, got)
        for k, e in errors(g, want, sc).items():
            worst[k] = max(worst.get(k, 0.0), e)
        if old:
            w2 = old_value(kind, wx, wy, offset)
            for k, e in errors(g, w2, sc).items():
                worst_old[k] = max(worst_old.get(k, 0.0), e)
    if record is not None:
        record.update(worst)
    return worst, worst_old


def check_worst(worst, tol=TOL, label=""):
    assert worst, "nothing was compared"
    for k, e in worst.items():
        assert e <= tol, (label, k, e)


# -- series ---------------------------------------------------------------------------------------


def _pairs():
    r = random.Random(4321)
    out, n = {}, N_BARS
    x = y = 100.0
    wx, wy = [], []
    for _ in range(n):
        c = r.gauss(0, 0.7)
        x = max(1.0, x + c + r.gauss(0, 0.5))
        y = max(1.0, y + c + r.gauss(0, 0.5))
        wx.append(x)
        wy.append(y)
    out["walk"] = (wx, wy)
    hx = [20000 + r.gauss(0, 1e-3) for _ in range(n)]
    out["high_level"] = (hx, [20000 + 0.5 * (a - 20000) + r.gauss(0, 1e-3) for a in hx])
    out["drift"] = (
        [20000 + 0.01 * i + r.gauss(0, 1e-2) for i in range(n)],
        [19000 + 0.02 * i + r.gauss(0, 1e-2) for i in range(n)],
    )
    cx = [100 + r.gauss(0, 1) for _ in range(n)]
    out["collinear"] = (cx, [2 * a + 3 + r.gauss(0, 1e-7) for a in cx])
    out["near_const_x"] = ([20000 + r.gauss(0, 1e-6) for _ in range(n)], wy)
    flat = [5.0] * 500 + [5.0 + r.gauss(0, 1) for _ in range(500)] + [7.25] * 500
    flat += [r.uniform(1, 3) for _ in range(n - 1500)]
    out["flat_then_noise"] = (flat, [7.0] * 300 + wy[300:])
    return out


PAIRS = _pairs()
PIDS = list(PAIRS)


# -- 1. helpers vs oracles ------------------------------------------------------------------------


def _helpers():
    from honba.strategies.indicators import _rolling

    return _rolling.RollingPairMoments, _rolling.RollingLinReg


@pytest.mark.parametrize("sid", PIDS)
@pytest.mark.parametrize("period,ddof", [(1, 0), (2, 0), (2, 1), (20, 0), (20, 1), (200, 1)])
def test_pair_moments_matches_exact(sid, period, ddof):
    RollingPairMoments, _ = _helpers()
    rp, wx, wy = RollingPairMoments(period, ddof=ddof), deque(maxlen=period), deque(maxlen=period)
    xs, ys = PAIRS[sid]
    worst = 0.0
    for i, (x, y) in enumerate(zip(xs[:3000], ys[:3000])):
        wx.append(x)
        wy.append(y)
        got = rp.update(x, y)
        if len(wx) < period:
            assert got is None
            continue
        if i % 7 and i > 3 * period + 10:
            continue
        n, mx, my, sxx, syy, sxy = exact_pair_sums(wx, wy)
        d = n - ddof
        assert got.mean_x == pytest.approx(float(mx), rel=1e-12)
        assert got.mean_y == pytest.approx(float(my), rel=1e-12)
        for g, w, nat in (
            (got.var_x, float(sxx / d), float(sxx / d)),
            (got.var_y, float(syy / d), float(syy / d)),
            (got.cov, float(sxy / d), _sqrtf(sxx * syy) / d),
        ):
            worst = max(worst, _err(g, w, nat) if nat > 0 else abs(g))
    assert worst <= TOL, worst


def test_pair_moments_ddof_validation_and_period():
    RollingPairMoments, RollingLinReg = _helpers()
    with pytest.raises(ValueError):
        RollingPairMoments(1, ddof=1)
    with pytest.raises(ValueError):
        RollingPairMoments(0)
    with pytest.raises(ValueError):
        RollingPairMoments(5, ddof=-1)
    with pytest.raises(ValueError):
        RollingLinReg(1)


@pytest.mark.parametrize("sid", PIDS)
@pytest.mark.parametrize("period", [2, 3, 20, 200])
def test_linreg_matches_exact(sid, period):
    _, RollingLinReg = _helpers()
    rl, w = RollingLinReg(period), deque(maxlen=period)
    worst = {}
    for i, y in enumerate(PAIRS[sid][1][:3000]):
        w.append(y)
        got = rl.update(y)
        if len(w) < period:
            assert got is None
            continue
        if i % 5 and i > 3 * period + 10:
            continue
        want, sc = exact_value("linear_regression", None, list(w))
        icpt, inat = exact_intercept(w)
        _, my, _, _, _ = exact_reg_sums(w)
        g = {
            "value": got.intercept + got.slope * (period - 1),
            "slope": got.slope,
            "std": math.sqrt(got.sse / period),
        }
        for k, e in errors(g, want, sc).items():
            worst[k] = max(worst.get(k, 0.0), e)
        worst["intercept"] = max(worst.get("intercept", 0.0), _err(got.intercept, icpt, inat))
        worst["mean"] = max(worst.get("mean", 0.0), _err(got.mean, float(my), abs(float(my))))
    check_worst(worst, label=(sid, period))


# -- 2. indicators vs old code and exact, on the shared series -------------------------------------


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize("sid", PIDS)
@pytest.mark.parametrize("period", [2, 5, 20, 60])
def test_indicator_matches_exact(kind, sid, period):
    xs, ys = PAIRS[sid]
    worst, _ = run(kind, period, xs[:3000], ys[:3000], stride=3, old=False)
    check_worst(worst, label=(kind, sid, period))


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize("sid", ["walk", "flat_then_noise", "collinear"])
@pytest.mark.parametrize("period", [2, 7, 25])
def test_indicator_matches_old_code(kind, sid, period):
    xs, ys = PAIRS[sid]
    _, worst_old = run(kind, period, xs[:3000], ys[:3000], stride=3, old=True)
    check_worst(worst_old, label=(kind, sid, period))


@pytest.mark.parametrize("kind", ALL_KINDS)
@pytest.mark.parametrize("sid", PIDS)
def test_long_period_12000_bars_crosses_rebuild_boundary(kind, sid):
    """Period 300 rebuilds every max(1000, 1200) updates: 12000 bars cross it ten times."""
    xs, ys = PAIRS[sid]
    worst, _ = run(kind, 300, xs, ys, stride=37, old=False)
    check_worst(worst, label=(kind, sid))


def test_lsma_offset_matches_old_code():
    xs, ys = PAIRS["walk"]
    for off in (-3, 0, 4, 30):
        _, worst_old = run("lsma", 25, xs[:2000], ys[:2000], stride=2, old=True, offset=off)
        worst, _ = run("lsma", 25, xs[:2000], ys[:2000], stride=2, old=False, offset=off)
        check_worst(worst_old, label=off)
        check_worst(worst, label=off)


# -- 3. degenerate cases: whatever the old code returns -------------------------------------------


def _last(ind, rows):
    out = None
    for r in rows:
        out = ind.update(*r)
    return out


def test_constant_windows_and_zero_variance_outputs():
    c = lambda *a, **k: build_indicator(*a, **k)
    # correlation: zero variance in either window -> 0.0, then recovers
    ind = c("correlation", length=4)
    rows = [(5.0, 1.0), (5.0, 2.0), (5.0, 4.0), (5.0, 3.0)]
    assert _last(ind, rows) == 0.0
    assert ind.update(1.0, 3.0) == 0.0
    out = [ind.update(x, y) for x, y in [(2.0, 1.0), (3.0, 5.0), (4.0, 2.0)]]
    w = ([1.0, 2.0, 3.0, 4.0], [3.0, 1.0, 5.0, 2.0])
    assert out[-1] == pytest.approx(old_value("correlation", *w)["v"], abs=1e-12)
    ind = c("correlation", length=3)
    assert _last(ind, [(1.0, 7.25)] * 3) == 0.0
    for _ in range(50):
        assert ind.update(2.0, 7.25) == 0.0
    # covariance: constant series -> exactly 0.0 (old: sum of exact zeros)
    ind = c("covariance", length=3)
    assert _last(ind, [(10.0, 4.0)] * 5) == 0.0
    # beta: flat benchmark -> 0.0; flat asset with moving benchmark -> 0.0 / var = 0.0
    assert _last(c("beta", length=3), [(1.0, 5.0), (2.0, 5.0), (3.0, 5.0), (4.0, 5.0)]) == 0.0
    rows = [(5.0, 1.0), (5.0, 2.0), (5.0, 3.0), (5.0, 5.0), (5.0, 4.0)]
    assert _last(c("beta", length=3), rows) == 0.0
    # regression of a constant: slope exactly 0, zero-width channel
    lin = c("linear_regression", length=5)
    out = _last(lin, [(20000.1,)] * 12)
    assert (out.slope, out.upper, out.lower, out.value) == (0.0, 20000.1, 20000.1, 20000.1)
    assert _last(c("lsma", period=5, offset=2), [(0.1,)] * 12) == 0.1


def test_constant_window_with_inexact_mean_is_exactly_zero_variance():
    """Documented deviation: the old two-pass code returned float noise for 0.1 * n windows
    (mean != 0.1 after summing); a constant window is now exactly degenerate."""
    assert (
        _last(build_indicator("correlation", length=3), [(0.1, 1.0), (0.1, 3.0), (0.1, 2.0)]) == 0.0
    )
    assert _last(build_indicator("covariance", length=3), [(1.0, 0.1)] * 5) == 0.0


def test_constant_windows_after_varying_values_are_exact():
    """Window becomes constant after a spike left: no residue, exactly the old degenerate result."""
    ind = build_indicator("correlation", length=5)
    r = random.Random(3)
    for _ in range(30):
        ind.update(100 + r.gauss(0, 1), 50 + r.gauss(0, 1))
    ind.update(1e7, 1e7)
    outs = [ind.update(20000.0, 50 + r.gauss(0, 1)) for _ in range(12)]
    assert outs[-1] == 0.0


# -- 4. non-finite and huge values -------------------------------------------------------------------


def _bad_feeds(bad, where, p, n=60):
    """Good data with bad values at warmup start, window boundaries and consecutively."""
    r = random.Random(17)
    base = [(100 + r.gauss(0, 1), 50 + r.gauss(0, 1)) for _ in range(n)]
    spots = [[0], [p - 1], [p], [p + 1], [2 * p], [0, 1], [p, p + 1, p + 2], list(range(p + 1))]
    feeds = []
    for idx in spots:
        f = list(base)
        for i in idx:
            x, y = f[i]
            f[i] = (bad if where in ("x", "both") else x, bad if where in ("y", "both") else y)
        feeds.append(f)
    return feeds


@pytest.mark.parametrize("bad", [NAN, INF, -INF, 1e160, -1e200])
@pytest.mark.parametrize("where", ["x", "y", "both"])
@pytest.mark.parametrize("period", [2, 3, 5])
@pytest.mark.parametrize("kind", ALL_KINDS)
def test_unsafe_values_nan_while_in_window_then_match_old(kind, where, bad, period):
    """NaN while a non-finite / |x|>1e150 value is in the window; afterwards equal to old code."""
    if kind in SINGLE and where == "x":
        pytest.skip("single-input indicator")
    for feed in _bad_feeds(bad, where, period):
        xs, ys = zip(*feed)
        worst, worst_old = run(kind, period, xs, ys, old=True)
        check_worst(worst, label=("exact", kind, where, bad, period))
        check_worst(worst_old, label=("old", kind, where, bad, period))
        st = Stream(kind, period)
        outs = [st.step(x, y) for x, y in feed]
        assert not is_nan_result(outs[-1])  # recovered by the end of the feed


@pytest.mark.parametrize("bad", [NAN, INF, -INF, 1e160])
@pytest.mark.parametrize("where", ["x", "y", "both"])
@pytest.mark.parametrize("period", [1, 2, 3])
def test_helpers_unsafe_values(where, bad, period):
    RollingPairMoments, RollingLinReg = _helpers()
    for feed in _bad_feeds(bad, where, period):
        rp, wx, wy = RollingPairMoments(period), deque(maxlen=period), deque(maxlen=period)
        for x, y in feed:
            wx.append(x)
            wy.append(y)
            got = rp.update(x, y)
            if len(wx) < period:
                assert got is None
            elif any(unsafe(v) for v in list(wx) + list(wy)):
                assert all(math.isnan(v) for v in got)
            else:
                n, _, _, sxx, syy, sxy = exact_pair_sums(wx, wy)
                assert got.var_x == pytest.approx(float(sxx / n), rel=1e-9, abs=1e-12)
                assert got.var_y == pytest.approx(float(syy / n), rel=1e-9, abs=1e-12)
                assert got.cov == pytest.approx(float(sxy / n), rel=1e-9, abs=1e-12)
        if period >= 2:
            rl, w = RollingLinReg(period), deque(maxlen=period)
            for x, y in feed:
                w.append(y)
                got = rl.update(y)
                if len(w) < period:
                    assert got is None
                elif any(unsafe(v) for v in w):
                    assert all(math.isnan(v) for v in got)
                else:
                    _, _, sxx, _, sxy = exact_reg_sums(w)
                    assert got.slope == pytest.approx(float(sxy / sxx), rel=1e-9, abs=1e-12)


def test_nan_during_warmup_and_nan_in_both_do_not_poison():
    ind = build_indicator("correlation", length=4)
    assert ind.update(NAN, NAN) is None
    for x, y in [(1.0, 2.0), (2.0, 1.0)]:
        assert ind.update(x, y) is None
    assert math.isnan(ind.update(3.0, 5.0))
    out = ind.update(4.0, 2.0)
    assert out == pytest.approx(old_value("correlation", [1, 2, 3, 4], [2, 1, 5, 2])["v"])


# -- 5. outlier residue -----------------------------------------------------------------------------


def _spiky(noise, spike, where, kind_scale=1.0, quiet=300, seed=5, at=60, both=False):
    r = random.Random(seed)
    n = at + 1 + quiet
    xs = [100.0 + r.gauss(0, noise) for _ in range(n)]
    ys = [50.0 + 0.4 * (a - 100) + r.gauss(0, noise) for a in xs]
    if where in ("x", "both"):
        xs[at] += spike
    if where in ("y", "both"):
        ys[at] += spike
    return xs, ys


@pytest.mark.parametrize("noise,spike", [(1e-3, 1e3), (1e-2, 1e5), (1e-3, 1e7), (1.0, 1e9)])
@pytest.mark.parametrize("period", [5, 20, 100])
@pytest.mark.parametrize("where", ["x", "y", "both"])
@pytest.mark.parametrize("kind", ALL_KINDS)
def test_no_residue_after_outlier(kind, where, period, noise, spike):
    xs, ys = _spiky(noise, spike, where)
    worst, _ = run(kind, period, xs, ys, old=False)
    check_worst(worst, label=(kind, where, period, noise, spike))


@pytest.mark.parametrize("noise,spike", [(1e-3, 1e3), (1e-2, 1e5), (1e-3, 1e7), (1.0, 1e9)])
@pytest.mark.parametrize("period", [5, 20, 100])
def test_linreg_helper_residual_std_after_outlier(period, noise, spike):
    """The indicator-level std check hides errors below the output ulp; check sse directly."""
    _, RollingLinReg = _helpers()
    _, ys = _spiky(noise, spike, "y")
    rl, w, worst = RollingLinReg(period), deque(maxlen=period), {}
    for y in ys:
        w.append(y)
        got = rl.update(y)
        if len(w) < period:
            continue
        want, sc = exact_value("linear_regression", None, list(w))
        g = {
            "value": got.intercept + got.slope * (period - 1),
            "slope": got.slope,
            "std": math.sqrt(got.sse / period),
        }
        for k, e in errors(g, want, sc).items():
            worst[k] = max(worst.get(k, 0.0), e)
    check_worst(worst, label=(period, noise, spike))


@pytest.mark.parametrize("period", [5, 20, 100])
@pytest.mark.parametrize("spike", [1e6, 1e9])
@pytest.mark.parametrize("kind", ALL_KINDS)
def test_spike_inside_first_period_bars(kind, period, spike):
    for at in sorted({0, 1, period // 2, period - 1}):
        xs, ys = _spiky(1e-2, spike, "both", at=at, quiet=3 * period)
        worst, _ = run(kind, period, xs, ys, old=False)
        check_worst(worst, label=(kind, period, at, spike))


@pytest.mark.parametrize("period", [5, 10, 20])
@pytest.mark.parametrize("kind", ALL_KINDS)
def test_decaying_cascade_of_spikes(kind, period):
    """Each drop is <1e4 but the cumulative drop is huge; residue must not survive."""
    r = random.Random(9)
    xs = [50 + r.gauss(0, 1e-2) for _ in range(30)]
    for k in range(6, 0, -1):
        xs += [50 + 10.0**k] + [50 + r.gauss(0, 1e-2) for _ in range(period - 1)]
    xs += [50 + r.gauss(0, 1e-2) for _ in range(100)]
    ys = [30 + 0.3 * (a - 50) + r.gauss(0, 1e-2) for a in xs]
    worst, _ = run(kind, period, xs, ys, old=False)
    check_worst(worst, label=(kind, period))


# -- 6. smooth trends -------------------------------------------------------------------------------


def _curves(n=3000):
    return {
        "ramp": [0.1 * i for i in range(n)],
        "neg_ramp": [1000 - 0.1 * i for i in range(n)],
        "high_ramp": [20000 + 0.01 * i for i in range(n)],
        "quad": [1e-5 * i * i for i in range(n)],
        "high_quad": [20000 + 1e-4 * i * i for i in range(n)],
        "sin": [100 + 10 * math.sin(i / 7.0) for i in range(n)],
        "high_sin": [20000 + 50 * math.sin(i / 11.0) for i in range(n)],
    }


CURVES = _curves()
TREND_PERIODS = [2, 3, 4, 5, 7, 10, 50]
WORST_TREND: dict = {}


@pytest.mark.parametrize("period", TREND_PERIODS)
@pytest.mark.parametrize("name", list(CURVES))
@pytest.mark.parametrize("kind", ALL_KINDS)
def test_smooth_trend_error(kind, name, period):
    other = CURVES["high_sin"] if name != "high_sin" else CURVES["quad"]
    rec: dict = {}
    worst, _ = run(kind, period, CURVES[name], other, old=False, record=rec)
    for k, e in worst.items():
        WORST_TREND[(kind, k)] = max(WORST_TREND.get((kind, k), 0.0), e)
    check_worst(worst, label=(kind, name, period))


@pytest.mark.parametrize("period", TREND_PERIODS)
@pytest.mark.parametrize("name", list(CURVES))
def test_smooth_trend_slope_and_intercept_helper(name, period):
    _, RollingLinReg = _helpers()
    rl, w, worst = RollingLinReg(period), deque(maxlen=period), {"slope": 0.0, "intercept": 0.0}
    for y in CURVES[name]:
        w.append(y)
        got = rl.update(y)
        if len(w) < period:
            continue
        _, _, sxx, syy, sxy = exact_reg_sums(w)
        sc = _sqrtf(syy / sxx) if syy > 0 else 1.0
        worst["slope"] = max(worst["slope"], _err(got.slope, float(sxy / sxx), sc))
        icpt, inat = exact_intercept(w)
        worst["intercept"] = max(worst["intercept"], _err(got.intercept, icpt, inat))
    WORST_TREND[("helper", name, period)] = worst
    check_worst(worst, label=(name, period))


def test_print_worst_trend_errors():
    """Informational: the maxima over all smooth-trend cases (run with -s)."""
    print(
        "\nworst smooth-trend errors:",
        {f"{k[0]}.{k[1]}": v for k, v in WORST_TREND.items() if len(k) == 2},
    )


# -- 7. variance cancellation: collinear and near-constant inputs ---------------------------------


@pytest.mark.parametrize("noise", [0.0, 1e-12, 1e-9, 1e-6, 1e-3])
@pytest.mark.parametrize("period", [2, 5, 20, 100])
@pytest.mark.parametrize("kind", PAIR_KINDS)
def test_nearly_collinear(kind, period, noise):
    r = random.Random(31)
    xs = [100 + r.gauss(0, 1) + 0.05 * i for i in range(600)]
    ys = [3.0 * a - 7.0 + r.gauss(0, noise) for a in xs]
    worst, worst_old = run(kind, period, xs, ys, old=True)
    check_worst(worst, label=("exact", kind, period, noise))
    if noise >= 1e-6:  # below that the old two-pass code's own rounding dominates
        check_worst(worst_old, label=("old", kind, period, noise))


@pytest.mark.parametrize("jitter", [0.0, 1e-13, 1e-9, 1e-5])
@pytest.mark.parametrize("period", [3, 20, 100])
@pytest.mark.parametrize("kind", PAIR_KINDS)
def test_near_constant_x(kind, period, jitter):
    r = random.Random(32)
    xs = [20000.0 + (r.gauss(0, jitter) if jitter else 0.0) for _ in range(600)]
    ys = [100 + r.gauss(0, 1) for _ in range(600)]
    worst, _ = run(kind, period, xs, ys, old=False)
    check_worst(worst, label=(kind, period, jitter))


def test_near_constant_single_ulp_changes():
    """x constant at 20000 except isolated 1-ulp steps. The exact answer is the real (tiny-var)
    correlation while the step is in the window and 0.0 after it left. The old two-pass code is
    not a usable oracle here (it rounds the mean: up to ~5e-2 off in correlation), so only the
    exact oracle is used."""
    ulp = math.ulp(20000.0)
    xs = [20000.0] * 40 + [20000.0 + ulp] + [20000.0] * 40 + [20000.0 - ulp] + [20000.0] * 40
    r = random.Random(33)
    ys = [100 + r.gauss(0, 1) for _ in xs]
    for kind in PAIR_KINDS:
        worst, _ = run(kind, 10, xs, ys, old=False)
        check_worst(worst, label=kind)
    st = Stream("correlation", 10)
    outs = [st.step(x, y) for x, y in zip(xs, ys)]
    assert outs[-1] == 0.0 and outs[45] != 0.0  # after the step left: exactly the old 0.0


# -- 8. amortised cost -------------------------------------------------------------------------------


def _count_rebuilds(cls_name, factory, updates):
    import honba.strategies.indicators._rolling as mod

    cls = getattr(mod, cls_name)
    calls, real = {"n": 0}, cls._recompute

    def counting(self):
        calls["n"] += 1
        real(self)

    obj = factory(cls)
    cls._recompute = counting
    try:
        for u in updates:
            obj.update(*u)
    finally:
        cls._recompute = real
    return calls["n"] / len(updates)


def _walk(seed, bars=20_000):
    r = random.Random(seed)
    x, y, out = 100.0, 100.0, []
    for _ in range(bars):
        x += r.gauss(0, 1)
        y += r.gauss(0, 1)
        out.append((x, y))
    return out


@pytest.mark.parametrize("period", [20, 100, 300])
def test_pair_rebuild_rate_on_random_walks(period):
    rate = _count_rebuilds("RollingPairMoments", lambda c: c(period), _walk(11))
    print(f"\npair random-walk rebuild rate p={period}: {rate:.5f}")
    assert rate < 0.01, rate
    rate_r = _count_rebuilds(
        "RollingPairMoments",
        lambda c: c(period),
        [(b[0] - a[0], b[1] - a[1]) for a, b in zip(_walk(12), _walk(12)[1:])],
    )
    print(f"pair return-series rebuild rate p={period}: {rate_r:.5f}")
    assert rate_r < 0.01, rate_r


@pytest.mark.parametrize("period", [20, 100, 300])
def test_linreg_rebuild_rate_on_random_walks(period):
    rate = _count_rebuilds("RollingLinReg", lambda c: c(period), [(p[0],) for p in _walk(11)])
    print(f"\nlinreg random-walk rebuild rate p={period}: {rate:.5f}")
    assert rate < 0.01, rate


def _alternating_pair(period, s=1e6, bars=4000):
    """x_i = x_{i-p} +- s on alternate bars, y follows x: variances and covariance pulse."""
    r = random.Random(6)
    xs = [r.gauss(0, 1e-3) for _ in range(period)]
    for i in range(period, bars):
        xs.append(xs[i - period] + (s if i % 2 == 0 else -s))
    ys = [0.5 * a + r.gauss(0, 1e-3) for a in xs]
    return list(zip(xs, ys))


def _sawtooth(period, bars=4000):
    """Geometric growth (every value ~1.3x the last: shift trigger each bar) then reset."""
    out, v = [], 1.0
    for i in range(bars):
        v = v * 1.3 if i % 90 else 1.0
        out.append((v, -2.0 * v + (i % 3)))
    return out


def _spike_train(period, bars=4000):
    r = random.Random(7)
    return [
        (
            100 + r.gauss(0, 1) + (1e7 if i % (period + 1) == 0 else 0.0),
            100 + r.gauss(0, 1) + (-1e7 if i % (period + 1) == 0 else 0.0),
        )
        for i in range(bars)
    ]


@pytest.mark.parametrize("period", [10, 20, 50, 100])
@pytest.mark.parametrize("gen", [_alternating_pair, _sawtooth, _spike_train])
def test_rebuild_rate_bounded_on_adversarial_input(gen, period):
    data = gen(period)
    rate = _count_rebuilds("RollingPairMoments", lambda c: c(period), data)
    rate_l = _count_rebuilds("RollingLinReg", lambda c: c(period), [(d[1],) for d in data])
    print(
        f"\n{gen.__name__} p={period}: pair {rate:.4f} linreg {rate_l:.4f} bound {2 / period + 0.01:.4f}"
    )
    assert rate <= 2 / period + 0.01, rate
    assert rate_l <= 2 / period + 0.01, rate_l


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
        ind = build_indicator(kind, **{LEN[kind]: period})
        r = random.Random(1)
        feed = (
            (lambda: (ind.update(Counted(1 + r.random())),))
            if kind in SINGLE
            else (lambda: (ind.update(Counted(1 + r.random()), Counted(1 + r.random())),))
        )
        for _ in range(period + 10):
            feed()
        Counted.ops = 0
        for _ in range(50):
            feed()
        return Counted.ops / 50

    for kind in ALL_KINDS:
        small, big = ops_per_update(kind, 10), ops_per_update(kind, 400)
        assert small > 0, (kind, small, big)
        assert big <= small * 1.5 + 1, (kind, small, big)


def test_no_python_level_window_scan_per_update():
    """Independent of the op counter: sum()/generator scans over the window would show up as
    calls to ``math.fsum`` in steady state on ordinary data."""
    import honba.strategies.indicators._rolling as mod

    calls = {"n": 0}
    real = mod.math.fsum

    def counting(it):
        calls["n"] += 1
        return real(it)

    r = random.Random(2)
    inds = [build_indicator(k, **{LEN[k]: 100}) for k in ALL_KINDS]
    mod.math.fsum = counting
    try:
        for i in range(2000):
            x, y = 100 + r.gauss(0, 1), 50 + r.gauss(0, 1)
            for k, ind in zip(ALL_KINDS, inds):
                ind.update(y) if k in SINGLE else ind.update(x, y)
    finally:
        mod.math.fsum = real
    assert calls["n"] <= 2000 * len(ALL_KINDS) * 0.05 * 5, calls["n"]


# -- 9. API: update_bar, reset, config, long series -----------------------------------------------


class _Bar:
    def __init__(self, close, benchmark):
        self.close, self.benchmark = close, benchmark


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_update_bar_equals_update(kind):
    a, b = build_indicator(kind, **{LEN[kind]: 10}), build_indicator(kind, **{LEN[kind]: 10})
    xs, ys = PAIRS["walk"]
    for x, y in zip(xs[:300], ys[:300]):
        bar = _Bar(x, y)
        if kind in SINGLE:
            assert a.update_bar(bar) == b.update(x)
        else:
            assert a.update_bar(bar) == b.update(x, y)


@pytest.mark.parametrize("kind", ALL_KINDS)
def test_reset_replays_identically_and_clears_state(kind):
    ind = build_indicator(kind, **{LEN[kind]: 10})
    xs, ys = PAIRS["walk"]
    feed = lambda: [
        ind.update(y) if kind in SINGLE else ind.update(x, y) for x, y in zip(xs[:300], ys[:300])
    ]
    first = feed()
    ind.update(NAN, NAN) if kind not in SINGLE else ind.update(NAN)
    ind.reset()
    assert feed() == first
    ind.reset()
    assert ind.update(*([1.0] if kind in SINGLE else [1.0, 2.0])) is None


@pytest.mark.parametrize(
    "kind,warm",
    [
        ("correlation", 20),
        ("beta", 21),
        ("covariance", 21),
        ("lsma", 20),
        ("linear_regression", 20),
    ],
)
def test_warmup_unchanged(kind, warm):
    ind = build_indicator(kind, **{LEN[kind]: 20})
    xs, ys = PAIRS["walk"]
    out = [ind.update(y) if kind in SINGLE else ind.update(x, y) for x, y in zip(xs[:40], ys[:40])]
    assert all(o is None for o in out[: warm - 1]) and out[warm - 1] is not None
    assert ind.warmup == warm


def test_helpers_reset_equivalent_to_fresh():
    RollingPairMoments, _ = _helpers()
    xs, ys = PAIRS["walk"]
    rp = RollingPairMoments(8)
    a = [rp.update(x, y) for x, y in zip(xs[:100], ys[:100])]
    rp2 = RollingPairMoments(8)
    b = [rp2.update(x, y) for x, y in zip(xs[:100], ys[:100])]
    assert a == b
