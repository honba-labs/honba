"""O(1) rolling helpers and indicators built on them vs. the original O(window) oracles.

Tolerance: rel 1e-9 / abs 1e-9 on means and stdevs. Streaming updates differ from the recomputed
oracle only by float rounding (~1e-16 per step, bounded by periodic exact recompute); 1e-9 is
several orders above that yet far below any indicator-relevant difference. The high-level
low-variance series (~20000 +/- 1e-3) is the cancellation case a naive sum/sumsq would fail.
"""

import math
import random
from collections import deque

import pytest

from honba.strategies.indicators import build_indicator
from honba.strategies.indicators._rolling import RollingMoments, RollingSum
from honba.strategies.indicators.volatility._stats import RollingStd

N_BARS = 12_000
TOL = {"rel": 1e-9, "abs": 1e-9}


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


# -- oracles (the original O(window) implementations) --------------------------


def oracle_moments(w, ddof=0):
    n = len(w)
    mean = sum(w) / n
    var = sum((v - mean) ** 2 for v in w) / (n - ddof)
    return mean, math.sqrt(var)


def oracle_wma(w):
    n = len(w)
    return sum(k * v for k, v in enumerate(w, 1)) / (n * (n + 1) / 2)


# -- RollingSum ---------------------------------------------------------------


@pytest.mark.parametrize("sid", SIDS)
@pytest.mark.parametrize("period", [1, 5, 50])
def test_rolling_sum_matches_oracle(sid, period):
    rs, w = RollingSum(period), deque(maxlen=period)
    for x in SERIES[sid]:
        w.append(x)
        got = rs.update(x)
        if len(w) < period:
            assert got is None
        else:
            assert got == pytest.approx(math.fsum(w), **TOL)


# -- RollingMoments -----------------------------------------------------------


@pytest.mark.parametrize("sid", SIDS)
@pytest.mark.parametrize("period,ddof", [(2, 0), (20, 0), (20, 1), (200, 0)])
def test_rolling_moments_matches_oracle(sid, period, ddof):
    rm, w = RollingMoments(period, ddof=ddof), deque(maxlen=period)
    for x in SERIES[sid]:
        w.append(x)
        got = rm.update(x)
        if len(w) < period:
            assert got is None
            continue
        mean, sd = oracle_moments(w, ddof)
        assert got.mean == pytest.approx(mean, **TOL)
        assert got.std == pytest.approx(sd, **TOL)
        assert got.variance == pytest.approx(sd * sd, **TOL)


def test_rolling_moments_constant_window_is_exactly_zero():
    rm = RollingMoments(10)
    for x in SERIES["walk"][:100]:
        rm.update(x)
    for _ in range(10):
        got = rm.update(3.3)
    assert got.std == 0.0 and got.variance == 0.0 and got.mean == 3.3


def test_rolling_moments_ddof_needs_enough_samples():
    with pytest.raises(ValueError):
        RollingMoments(1, ddof=1)


# -- RollingStd (public helper API unchanged) ---------------------------------


@pytest.mark.parametrize("sid", SIDS)
def test_rolling_std_api(sid):
    rs, w = RollingStd(20), deque(maxlen=20)
    for x in SERIES[sid]:
        w.append(x)
        got = rs.update(x)
        if len(w) < 20:
            assert got is None
        else:
            mean, sd = oracle_moments(w)
            assert got[0] == pytest.approx(mean, **TOL)
            assert got[1] == pytest.approx(sd, **TOL)


# -- indicators ---------------------------------------------------------------


@pytest.mark.parametrize("sid", SIDS)
def test_bollinger_matches_oracle(sid):
    ind, w = build_indicator("bollinger", period=20, mult=2.0, mult_lower=1.5), deque(maxlen=20)
    for x in SERIES[sid]:
        w.append(x)
        got = ind.update(x)
        if len(w) < 20:
            assert got is None
            continue
        m, sd = oracle_moments(w)
        assert got.middle == pytest.approx(m, **TOL)
        assert got.upper == pytest.approx(m + 2 * sd, **TOL)
        assert got.lower == pytest.approx(m - 1.5 * sd, **TOL)


@pytest.mark.parametrize("sid", SIDS)
def test_zscore_matches_oracle(sid):
    ind, w = build_indicator("zscore", length=20), deque(maxlen=20)
    for x in SERIES[sid]:
        w.append(x)
        got = ind.update(x)
        if len(w) < 20:
            assert got is None
            continue
        m, sd = oracle_moments(w)
        want = (x - m) / sd if sd > 0 else 0.0
        assert got == pytest.approx(want, rel=1e-6, abs=1e-6)  # ratio amplifies rounding


@pytest.mark.parametrize("sid", SIDS)
@pytest.mark.parametrize("period", [1, 2, 5, 30])
def test_wma_matches_oracle(sid, period):
    ind, w = build_indicator("wma", period=period), deque(maxlen=period)
    for x in SERIES[sid]:
        w.append(x)
        got = ind.update(x)
        if len(w) < period:
            assert got is None
        else:
            assert got == pytest.approx(oracle_wma(w), **TOL)


@pytest.mark.parametrize(
    "kind,kw",
    [
        ("bollinger", {"period": 10}),
        ("zscore", {"length": 10}),
        ("wma", {"period": 10}),
    ],
)
def test_reset_replays_identically(kind, kw):
    ind = build_indicator(kind, **kw)
    a = [ind.update(x) for x in SERIES["walk"][:200]]
    ind.reset()
    assert [ind.update(x) for x in SERIES["walk"][:200]] == a


# -- complexity: per-update work must not scale with window -------------------


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

    def ops_per_update(build, period):
        ind = build(period)
        r = random.Random(1)
        for _ in range(period + 10):
            ind.update(Counted(r.random()))
        Counted.ops = 0
        for _ in range(50):
            ind.update(Counted(r.random()))
        return Counted.ops / 50

    for build in (
        lambda p: build_indicator("bollinger", period=p),
        lambda p: build_indicator("zscore", length=p),
        lambda p: build_indicator("wma", period=p),
    ):
        small, big = ops_per_update(build, 10), ops_per_update(build, 400)
        assert big <= small * 1.5 + 1


# -- regressions from review of 6372528 ---------------------------------------

NAN, INF = math.nan, math.inf


def _same(a, b, tol=1e-9):
    if a is None or b is None:
        return a is b
    if not math.isfinite(b):  # old code gave NaN or +-inf; the streaming code reports NaN
        return not math.isfinite(a)
    return a == pytest.approx(b, rel=tol, abs=tol)


def _old_wma(w):
    n = len(w)
    return sum(k * v for k, v in enumerate(w, 1)) / (n * (n + 1) / 2)


def test_moments_recovers_after_nan():
    rm = RollingMoments(5)
    for x in [1, 2, 3, NAN]:
        rm.update(x)
    r = None
    for i in range(20):
        r = rm.update(float(i % 7))
    assert math.isfinite(r.mean) and math.isfinite(r.std)
    w = [float(i % 7) for i in range(15, 20)]
    m, sd = oracle_moments(w)
    assert r.mean == pytest.approx(m, **TOL) and r.std == pytest.approx(sd, **TOL)


def test_moments_recovers_after_inf():
    rm = RollingMoments(5)
    out = [rm.update(float(x)) for x in [1, 2, INF, 3, 4, 5, 6, 7, 8, 9, 10]]
    assert math.isfinite(out[-1].mean) and math.isfinite(out[-1].std)
    assert out[-1].mean == pytest.approx(8.0)


@pytest.mark.parametrize("bad", [NAN, INF, -INF])
@pytest.mark.parametrize("period", [3, 5])
def test_moments_nan_duration_matches_old(bad, period):
    feed = [1, 2, bad, 1, 2, 3, bad, bad, 4, 5, 6, 7, 8, 9, 3, 1, 4, 1, 5, 9]
    rm, w = RollingMoments(period), deque(maxlen=period)
    for x in feed:
        w.append(float(x))
        got = rm.update(float(x))
        if len(w) < period:
            assert got is None
        elif any(not math.isfinite(v) for v in w):
            assert math.isnan(got.std) and math.isnan(got.variance)
        else:
            m, sd = oracle_moments(w)
            assert got.mean == pytest.approx(m, **TOL) and got.std == pytest.approx(sd, **TOL)


def test_moments_nan_during_warmup_does_not_poison():
    rm = RollingMoments(4)
    assert rm.update(NAN) is None
    for x in [1.0, 2.0]:
        assert rm.update(x) is None
    assert math.isnan(rm.update(3.0).std)  # NaN still in the window
    r = rm.update(4.0)
    assert r.mean == pytest.approx(2.5) and r.std == pytest.approx(oracle_moments([1, 2, 3, 4])[1])


@pytest.mark.parametrize("bad", [NAN, INF])
def test_wma_nan_duration_and_recovery_match_old(bad):
    feed = [1, 2, bad, 1, 2, 3, 4, 5]
    ind, w = build_indicator("wma", period=3), deque(maxlen=3)
    for x in feed:
        w.append(float(x))
        got = ind.update(float(x))
        want = None if len(w) < 3 else _old_wma(w)
        assert _same(got, want)
    assert math.isfinite(got) and got == pytest.approx(_old_wma([3, 4, 5]))


def test_rolling_sum_nan_recovery():
    rs, w = RollingSum(3), deque(maxlen=3)
    for x in [1, 2, NAN, 1, 2, 3, INF, 4, 5, 6, 7]:
        w.append(float(x))
        got = rs.update(float(x))
        if len(w) < 3:
            assert got is None
        elif any(not math.isfinite(v) for v in w):
            assert math.isnan(got)
        else:
            assert got == pytest.approx(sum(w))


def _spike_series(noise, spike, base=100.0, quiet=300, seed=5):
    r = random.Random(seed)
    xs = [base + r.gauss(0, noise) for _ in range(60)]
    xs.append(base + spike)
    xs += [base + r.gauss(0, noise) for _ in range(quiet)]
    return xs


@pytest.mark.parametrize("noise,spike", [(1e-2, 1e5), (1e-2, 1e7), (1e-3, 1e7), (1.0, 1e9)])
def test_moments_no_stale_residue_after_outlier(noise, spike):
    period = 20
    rm, w = RollingMoments(period), deque(maxlen=period)
    worst = 0.0
    for x in _spike_series(noise, spike):
        w.append(x)
        got = rm.update(x)
        if len(w) == period:
            _, sd = oracle_moments(w)
            worst = max(worst, abs(got.std - sd) / sd)
    assert worst < 1e-9


def test_zscore_no_stale_residue_after_outlier():
    ind, w = build_indicator("zscore", length=20), deque(maxlen=20)
    for x in _spike_series(1e-2, 1e7):
        w.append(x)
        got = ind.update(x)
        if len(w) == 20:
            m, sd = oracle_moments(w)
            assert got == pytest.approx((x - m) / sd, rel=1e-6, abs=1e-6)


def test_moments_decaying_cascade_of_spikes():
    """Each drop is <1e4 but the cumulative drop is huge; residue must not survive."""
    period = 10
    r = random.Random(9)
    xs = [50 + r.gauss(0, 1e-2) for _ in range(30)]
    for k in range(6, 0, -1):
        xs += [50 + 10.0**k] + [50 + r.gauss(0, 1e-2) for _ in range(period - 1)]
    xs += [50 + r.gauss(0, 1e-2) for _ in range(100)]
    rm, w = RollingMoments(period), deque(maxlen=period)
    for x in xs:
        w.append(x)
        got = rm.update(x)
        if len(w) == period:
            assert got.std == pytest.approx(oracle_moments(w)[1], rel=1e-9, abs=1e-12)


def test_moments_outlier_burst_stays_amortised_o1():
    """Recomputes are counted through fsum; outliers every 7th bar must not force O(n)/update."""
    import honba.strategies.indicators._rolling as mod

    calls = {"n": 0}
    real = mod.RollingMoments._recompute

    def counting(self):
        calls["n"] += 1
        real(self)

    period, bars = 200, 20_000
    r = random.Random(3)
    rm = RollingMoments(period)
    mod.RollingMoments._recompute = counting
    try:
        for i in range(bars):
            rm.update(100 + r.gauss(0, 1) + (r.choice([1e3, -1e3]) if i % 7 == 0 else 0))
    finally:
        mod.RollingMoments._recompute = real
    assert calls["n"] * period <= 3 * bars  # O(n) rebuild cost averages <= ~3 ops/update


def test_wma_periodic_recompute_bounds_drift():
    """Drift from a huge value is flushed by the periodic exact rebuild (period 5, 1000 bars)."""
    ind, w = build_indicator("wma", period=5), deque(maxlen=5)
    r = random.Random(4)
    xs = [1.0 + r.random() for _ in range(50)] + [1e12] + [1.0 + r.random() for _ in range(1500)]
    for x in xs:
        w.append(x)
        got = ind.update(x)
    assert got == pytest.approx(_old_wma(w), rel=1e-12)


@pytest.mark.parametrize("period", [300])
@pytest.mark.parametrize("sid", SIDS)
def test_long_period_crosses_recompute_boundary(sid, period):
    """max(1000, 4*300) = 1200 updates per rebuild, so 12000 bars cross it ten times."""
    rm, w = RollingMoments(period), deque(maxlen=period)
    wm, ws = build_indicator("wma", period=period), RollingSum(period)
    for x in SERIES[sid]:
        w.append(x)
        g, gw, gs = rm.update(x), wm.update(x), ws.update(x)
        if len(w) == period:
            m, sd = oracle_moments(w)
            assert g.mean == pytest.approx(m, **TOL) and g.std == pytest.approx(sd, **TOL)
            assert gw == pytest.approx(oracle_wma(w), **TOL)
            assert gs == pytest.approx(math.fsum(w), **TOL)


@pytest.mark.parametrize("ddof", [0, 1])
@pytest.mark.parametrize("sid", SIDS)
def test_moments_ddof_variance_vs_oracle(sid, ddof):
    period = 15
    rm, w = RollingMoments(period, ddof=ddof), deque(maxlen=period)
    for x in SERIES[sid][:3000]:
        w.append(x)
        got = rm.update(x)
        if len(w) == period:
            m = sum(w) / period
            var = sum((v - m) ** 2 for v in w) / (period - ddof)
            assert got.variance == pytest.approx(var, **TOL)
            assert got.std == pytest.approx(math.sqrt(var), **TOL)


class _Bar:
    def __init__(self, close):
        self.close = close


@pytest.mark.parametrize(
    "kind,kw",
    [
        ("bollinger", {"period": 10}),
        ("zscore", {"length": 10}),
        ("wma", {"period": 10}),
        ("standard_deviation", {"length": 10}),
    ],
)
def test_update_bar_equals_update(kind, kw):
    a, b = build_indicator(kind, **kw), build_indicator(kind, **kw)
    for x in SERIES["walk"][:200]:
        assert a.update_bar(_Bar(x)) == b.update(x)
