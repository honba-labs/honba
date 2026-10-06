"""Unit tests for honba.india.costs against published Zerodha-style examples.

Run from the package root:

    pytest python/tests/india/test_costs.py -q

Or standalone (this file imports the local costs module):

    python -m pytest artifacts/honba_india_costs/test_costs.py -q
"""

from __future__ import annotations

import dataclasses

import pytest

from honba.entities.order import OrderSide
from honba.markets.india.costs import (
    CostBreakdown,
    nse_equity_delivery_breakdown,
    nse_equity_delivery_cost,
    nse_equity_intraday_breakdown,
    nse_equity_intraday_cost,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _approx(a: float, b: float, tol: float = 0.05) -> bool:
    """Absolute tolerance in INR (charges are rounded to paise in practice)."""
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
# Zerodha calculator reference cases (equity delivery CNC)
#
# Source: Zerodha Brokerage Calculator / public charge schedule.
# Example: BUY 10 shares @ ₹2,000  → notional ₹20,000
#   Brokerage  = min(0.03% × 20k, 20) = ₹6.00
#   STT        = 0 (buy)
#   Exchange   ≈ 0.00297% × 20k ≈ ₹0.594
#   SEBI       = ₹10/crore × 20k ≈ ₹0.02
#   IPFT       ≈ ₹0.02
#   Stamp      = 0.015% × 20k = ₹3.00
#   GST        = 18% × (6 + 0.594 + 0.02 + 0.02) ≈ ₹1.19
#   Total buy  ≈ ₹10.82
#
# SELL same lot:
#   STT        = 0.10% × 20k = ₹20.00
#   Stamp      = 0
#   rest similar → total sell ≈ ₹27.8
# ---------------------------------------------------------------------------


class TestEquityDeliveryBuy:
    def test_buy_10_shares_2000(self) -> None:
        b = nse_equity_delivery_breakdown(OrderSide.BUY, 10, 2000.0)
        assert _approx(b.brokerage, 6.0)
        assert b.stt == 0.0
        assert _approx(b.stamp_duty, 3.0)
        assert _approx(b.exchange, 0.594)
        assert b.total > 0
        # total should be in the 10–12 INR band
        assert 10.0 <= b.total <= 12.5

    def test_buy_large_hits_brokerage_cap(self) -> None:
        # notional 1,00,000 → 0.03% = 30 > cap 20
        b = nse_equity_delivery_breakdown(OrderSide.BUY, 50, 2000.0)
        assert b.brokerage == 20.0
        assert b.stt == 0.0
        assert _approx(b.stamp_duty, 15.0)  # 0.015% of 1L

    def test_buy_cost_equals_breakdown_total(self) -> None:
        side, qty, px = OrderSide.BUY, 25, 1500.0
        assert nse_equity_delivery_cost(side, qty, px) == pytest.approx(
            nse_equity_delivery_breakdown(side, qty, px).total
        )


class TestEquityDeliverySell:
    def test_sell_10_shares_2000(self) -> None:
        b = nse_equity_delivery_breakdown(OrderSide.SELL, 10, 2000.0)
        assert _approx(b.brokerage, 6.0)
        assert _approx(b.stt, 20.0)  # 0.10 % of 20k
        assert b.stamp_duty == 0.0
        assert b.total > 20.0
        # total should be in the 26–29 INR band
        assert 26.0 <= b.total <= 29.5

    def test_sell_cost_equals_breakdown_total(self) -> None:
        side, qty, px = OrderSide.SELL, 100, 500.0
        assert nse_equity_delivery_cost(side, qty, px) == pytest.approx(
            nse_equity_delivery_breakdown(side, qty, px).total
        )


class TestRoundTrip:
    """Buy + sell the same lot — STT dominates the round-trip cost."""

    def test_round_trip_20k_notional(self) -> None:
        buy = nse_equity_delivery_cost(OrderSide.BUY, 10, 2000.0)
        sell = nse_equity_delivery_cost(OrderSide.SELL, 10, 2000.0)
        rt = buy + sell
        # STT alone is 20; total round-trip roughly 37–42
        assert 35.0 <= rt <= 45.0

    def test_round_trip_scales_with_notional(self) -> None:
        small = nse_equity_delivery_cost(OrderSide.BUY, 1, 1000) + nse_equity_delivery_cost(
            OrderSide.SELL, 1, 1000
        )
        large = nse_equity_delivery_cost(OrderSide.BUY, 10, 1000) + nse_equity_delivery_cost(
            OrderSide.SELL, 10, 1000
        )
        assert large > small * 5  # not strictly linear because of brokerage cap


class TestEdgeCases:
    def test_zero_quantity(self) -> None:
        assert nse_equity_delivery_cost(OrderSide.BUY, 0, 100) == 0.0

    def test_zero_price(self) -> None:
        assert nse_equity_delivery_cost(OrderSide.SELL, 10, 0) == 0.0

    def test_negative_quantity_treated_as_abs(self) -> None:
        # callers should pass positive qty, but we abs() internally via notional
        pos = nse_equity_delivery_cost(OrderSide.BUY, 10, 100)
        neg = nse_equity_delivery_cost(OrderSide.BUY, -10, 100)
        assert pos == pytest.approx(neg)


class TestIntraday:
    def test_intraday_stt_lower_than_delivery(self) -> None:
        deliv = nse_equity_delivery_breakdown(OrderSide.SELL, 10, 2000)
        intra = nse_equity_intraday_breakdown(OrderSide.SELL, 10, 2000)
        assert intra.stt < deliv.stt
        assert _approx(intra.stt, 5.0)  # 0.025 % of 20k

    def test_intraday_buy_no_stt(self) -> None:
        b = nse_equity_intraday_breakdown(OrderSide.BUY, 10, 2000)
        assert b.stt == 0.0

    def test_intraday_cost_matches_breakdown(self) -> None:
        assert nse_equity_intraday_cost(OrderSide.SELL, 50, 100) == pytest.approx(
            nse_equity_intraday_breakdown(OrderSide.SELL, 50, 100).total
        )


class TestBreakdownDataclass:
    def test_total_property(self) -> None:
        b = CostBreakdown(1, 2, 3, 4, 5, 6, 7)
        assert b.total == 28.0

    def test_frozen(self) -> None:
        b = nse_equity_delivery_breakdown(OrderSide.BUY, 1, 100)
        with pytest.raises(dataclasses.FrozenInstanceError):
            b.brokerage = 99  # type: ignore[misc]
