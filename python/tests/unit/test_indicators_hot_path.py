"""Deterministic hot-path cost guard for the O(1) rolling indicators.

The steady-state update (full window, no rebuild) must stay a handful of Python-level calls:
the indicator's own ``update`` plus one helper call (plus the output dataclass ``__init__`` for
the two indicators that return one).  The count is taken with ``sys.setprofile`` on a seeded
random walk, so it is exact and independent of machine speed, unlike a wall-clock assertion.
A regression that adds a per-update method call (e.g. a helper per sum, a NamedTuple
``__new__``, an ``_unsafe`` call) fails here.
"""

import random
import sys

import pytest

from honba.strategies.indicators import build_indicator

N_WARM = 400
N_MEASURE = 600

# kind -> (constructor params, max Python calls in the median steady-state update)
CASES = {
    "wma": ({"period": 50}, 1),
    "zscore": ({"length": 50}, 2),
    "bollinger": ({"period": 50}, 3),  # + BollingerValue.__init__
    "correlation": ({"length": 50}, 2),
    "beta": ({"length": 50}, 2),
    "covariance": ({"length": 50}, 2),
    "lsma": ({"period": 50}, 2),
    "linear_regression": ({"length": 50}, 3),  # + LinearRegressionValue.__init__
}
PERIODS = [2, 5, 20, 300]


def _series(seed: int, n: int) -> list[float]:
    rnd = random.Random(seed)
    v, out = 100.0, []
    for _ in range(n):
        v *= 1 + rnd.gauss(0, 0.01)
        out.append(v)
    return out


def _median_calls(ind, two: bool) -> int:
    xs, ys = _series(1, N_WARM + N_MEASURE), _series(2, N_WARM + N_MEASURE)
    feed = (lambda i: ind.update(xs[i], ys[i])) if two else (lambda i: ind.update(xs[i]))
    for i in range(N_WARM):
        feed(i)
    counts = []
    n = 0

    def prof(frame, event, arg):
        nonlocal n
        if event == "call":
            n += 1

    for i in range(N_WARM, N_WARM + N_MEASURE):
        n = 0
        sys.setprofile(prof)
        try:
            feed(i)
        finally:
            sys.setprofile(None)
        counts.append(n - 1)  # the setprofile(None) call itself is seen as one call event
    counts.sort()
    return counts[len(counts) // 2]


@pytest.mark.parametrize("period", PERIODS)
@pytest.mark.parametrize("kind", sorted(CASES))
def test_steady_state_update_is_a_few_calls(kind, period):
    params, limit = CASES[kind]
    (name,) = params
    ind = build_indicator(kind, **{name: period})
    two = len(type(ind).inputs) == 2
    assert _median_calls(ind, two) <= limit
