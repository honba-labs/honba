"""Square-root market impact (Balch pitfall #4: ignoring market impact).

``impact = kappa * sigma_daily * sqrt(quantity / ADV)``, fed only by sessions
the simulator has already seen — never by the bar being filled. The pure model
is tested here; the fill-price wiring lives in
``tests/unit/test_impact_fill_prices.py`` and the end-to-end accounting in
``tests/integration/test_backtest_market_impact.py``.
"""

from __future__ import annotations

import math
import statistics
from itertools import pairwise

import pytest

from honba.backtest.impact import MarketImpact
from honba.entities.instrument import InstrumentId

X = InstrumentId("XYZ", "NSE")
Y = InstrumentId("ABC", "NSE")


def _feed(
    impact: MarketImpact,
    iid: InstrumentId = X,
    *,
    volumes: tuple[float, ...],
    closes: tuple[float, ...],
) -> None:
    for volume, close in zip(volumes, closes, strict=True):
        impact.observe(iid, volume=volume, close=close)


def test_impact_is_zero_while_the_market_history_is_too_thin() -> None:
    impact = MarketImpact()
    assert impact.fraction(X, 100.0) == 0.0  # nothing observed yet
    impact.observe(X, volume=1_000.0, close=100.0)
    assert impact.fraction(X, 100.0) == 0.0  # one close: no return at all
    impact.observe(X, volume=1_000.0, close=101.0)
    assert impact.fraction(X, 100.0) == 0.0  # one return: a sample sigma needs two
    impact.observe(X, volume=1_000.0, close=99.0)
    assert impact.fraction(X, 100.0) > 0.0  # two returns: sigma exists
    assert impact.fraction(X, 0.0) == 0.0  # a zero-size order cannot move anything


def test_impact_follows_the_documented_square_root_formula() -> None:
    returns = (0.012, -0.007, 0.004, -0.011, 0.009, -0.003, 0.015, -0.006, 0.008, -0.002)
    closes: list[float] = [100.0]
    for r in returns:
        closes.append(closes[-1] * (1.0 + r))
    volumes = (1_000.0,) * len(closes)

    impact = MarketImpact(kappa=1.5, window=30)
    _feed(impact, volumes=volumes, closes=tuple(closes))

    sigma = statistics.stdev(returns)
    adv = 1_000.0
    quantity = 2_500.0
    assert impact.sigma(X) == pytest.approx(sigma)
    assert impact.adv(X) == pytest.approx(adv)
    assert impact.fraction(X, quantity) == pytest.approx(1.5 * sigma * math.sqrt(quantity / adv))


def test_impact_scales_with_the_square_root_of_the_order_size() -> None:
    impact = MarketImpact(kappa=1.0, window=5)
    _feed(
        impact,
        volumes=(500.0,) * 5,
        closes=(100.0, 101.0, 99.5, 102.0, 100.5),
    )
    base = impact.fraction(X, 100.0)
    assert base > 0.0
    assert impact.fraction(X, 400.0) == pytest.approx(2.0 * base)  # sqrt(4) = 2


def test_zero_kappa_means_no_impact_however_large_the_order() -> None:
    impact = MarketImpact(kappa=0.0)
    _feed(impact, volumes=(1_000.0,) * 6, closes=(100.0, 105.0, 95.0, 110.0, 90.0, 100.0))
    assert impact.fraction(X, 10_000_000.0) == 0.0


def test_the_window_drops_the_oldest_sessions_from_adv_and_sigma() -> None:
    impact = MarketImpact(window=20)
    volumes = (1_000.0,) * 15 + (4_000.0,) * 5
    closes = tuple(100.0 + 0.5 * i for i in range(20))
    _feed(impact, volumes=volumes, closes=closes)
    assert impact.adv(X) == pytest.approx((15 * 1_000.0 + 5 * 4_000.0) / 20)
    # only the last 20 closes (19 returns) contribute to sigma
    returns = [b / a - 1.0 for a, b in pairwise(closes)]
    assert impact.sigma(X) == pytest.approx(statistics.stdev(returns))

    _feed(impact, volumes=(4_000.0,) * 15, closes=(110.0,) * 15)
    assert impact.adv(X) == 4_000.0  # the 1_000-volume sessions have rolled out


def test_histories_are_tracked_per_instrument() -> None:
    impact = MarketImpact(window=10)
    _feed(impact, X, volumes=(1_000.0,) * 4, closes=(100.0, 101.0, 99.0, 102.0))
    _feed(impact, Y, volumes=(9_000.0,) * 4, closes=(50.0, 50.0, 50.0, 50.0))
    assert impact.adv(X) == 1_000.0
    assert impact.adv(Y) == 9_000.0
    assert impact.sigma(Y) == 0.0  # a flat series cannot move the market
    assert impact.fraction(Y, 1_000.0) == 0.0
    assert impact.fraction(X, 1_000.0) > 0.0


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"kappa": -1.0}, "kappa"),
        ({"kappa": float("nan")}, "kappa"),
        ({"window": 1}, "window"),
        ({"window": 0}, "window"),
    ],
)
def test_invalid_impact_parameters_are_refused(kwargs: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        MarketImpact(**kwargs)
