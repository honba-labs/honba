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
