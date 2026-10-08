"""Opening-auction realism model (Balch pitfall #8).

The opening print is a single auction price, not a continuous two-sided market:
a fill there must carry an adverse spread buffer, and intraday orders may wait
out the first minutes of auction turbulence. The model itself is pure arithmetic
(:mod:`honba.backtest.opening_auction`); how it reaches a fill is
``tests/unit/test_opening_auction_fills.py``, the end-to-end ledger view is
``tests/integration/test_backtest_opening_auction.py``.
"""

from __future__ import annotations

import dataclasses

import pytest

from honba.backtest.opening_auction import OpeningAuction


def test_defaults_disable_everything() -> None:
    auction = OpeningAuction()
    assert auction.spread_bps == 0.0
    assert auction.delay_bars == 0
    assert auction.fraction == 0.0  # a zero buffer leaves fills at the printed open


def test_spread_bps_is_the_adverse_fraction_of_the_open() -> None:
    assert OpeningAuction(spread_bps=25.0).fraction == pytest.approx(0.0025)
    assert OpeningAuction(spread_bps=1.0).fraction == pytest.approx(0.0001)


def test_delay_counts_additional_driving_bars() -> None:
    assert OpeningAuction(delay_bars=5).delay_bars == 5
    assert OpeningAuction(delay_bars=5).fraction == 0.0  # a delay alone changes no price


def test_a_buffer_keeps_a_sell_price_positive() -> None:
    # fraction < 1 is what makes open * (1 - fraction) a usable sell price.
    assert OpeningAuction(spread_bps=9_999.0).fraction < 1.0
    with pytest.raises(ValueError, match="spread_bps"):
        OpeningAuction(spread_bps=10_000.0)


@pytest.mark.parametrize("spread_bps", [-1.0, -0.01, float("nan"), float("inf")])
def test_bad_buffers_are_refused(spread_bps: float) -> None:
    with pytest.raises(ValueError, match="spread_bps"):
        OpeningAuction(spread_bps=spread_bps)


@pytest.mark.parametrize("delay_bars", [-1, 1.5])
def test_bad_delays_are_refused(delay_bars: object) -> None:
    with pytest.raises(ValueError, match="delay_bars"):
        OpeningAuction(delay_bars=delay_bars)  # type: ignore[arg-type]


def test_the_model_is_immutable_so_a_run_config_stays_replayable() -> None:
    auction = OpeningAuction(spread_bps=25.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        auction.spread_bps = 0.0  # type: ignore[misc]
