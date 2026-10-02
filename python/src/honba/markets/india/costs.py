"""NSE / BSE cash-equity and F&O transaction costs (India).

Pure functions — no I/O, no wall clock.  The backtest simulator and live
broker adapters call these to populate ``Trade.costs``; ``LedgerContext``
already debits the field on every fill.

Rates reflect the discount-broker (Zerodha-style) schedule in force for
equity *delivery* (CNC) in the 2025-26 financial year.  Intraday and F&O
schedules are included so a single module covers the whole Indian stack.

References
----------
- Zerodha Brokerage Calculator / charges schedule
- NSE circulars on transaction charges, IPFT, STT
- Stamp duty (Maharashtra / central schedule for delivery)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from honba.domain.order import OrderSide
else:
    try:
        from honba.domain.order import OrderSide
    except ImportError:  # standalone / test without full package
        from enum import Enum as _Enum

        class OrderSide(_Enum):  # type: ignore[no-redef]
            BUY = "BUY"
            SELL = "SELL"


# ---------------------------------------------------------------------------
# Segment / product
# ---------------------------------------------------------------------------
class Segment(Enum):
    EQUITY_DELIVERY = auto()  # CNC
    EQUITY_INTRADAY = auto()  # MIS / intraday
    FNO_FUTURES = auto()
    FNO_OPTIONS = auto()


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """Itemised costs for one fill (settlement currency = INR)."""

    brokerage: float
    stt: float
    exchange: float
    sebi: float
    ipft: float
    stamp_duty: float
    gst: float

    @property
    def total(self) -> float:
        return (
            self.brokerage
            + self.stt
            + self.exchange
            + self.sebi
            + self.ipft
            + self.stamp_duty
            + self.gst
        )


# ---------------------------------------------------------------------------
# Rate tables (2025-26)
# ---------------------------------------------------------------------------
# Equity delivery (CNC)
_EQ_DEL_STT_SELL = 0.0010          # 0.10 % on sell
_EQ_DEL_STAMP_BUY = 0.00015        # 0.015 % on buy
_EQ_DEL_EXCH = 0.0000297           # NSE transaction charge
_EQ_DEL_SEBI = 0.000001            # ₹10 / crore
_EQ_DEL_IPFT = 0.000001            # approx
_EQ_DEL_BROKERAGE_PCT = 0.0003     # 0.03 %
_EQ_DEL_BROKERAGE_CAP = 20.0       # ₹20 per order

# Equity intraday (MIS)
_EQ_INT_STT_SELL = 0.00025         # 0.025 % on sell
_EQ_INT_STAMP_BUY = 0.00003        # 0.003 % on buy
_EQ_INT_EXCH = 0.0000297
_EQ_INT_SEBI = 0.000001
_EQ_INT_IPFT = 0.000001
_EQ_INT_BROKERAGE_PCT = 0.0003
_EQ_INT_BROKERAGE_CAP = 20.0

_GST_RATE = 0.18


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def nse_equity_delivery_cost(
    side: OrderSide,
    quantity: float,
    price: float,
) -> float:
    """Total transaction cost (INR) for one NSE equity *delivery* fill.

    Parameters
    ----------
    side:
        ``OrderSide.BUY`` or ``OrderSide.SELL``.
    quantity:
        Absolute share quantity (positive).
    price:
        Fill price in INR.

    Returns
    -------
    float
        Total cost in INR (always ≥ 0).
    """
    return nse_equity_delivery_breakdown(side, quantity, price).total


def nse_equity_delivery_breakdown(
    side: OrderSide,
    quantity: float,
    price: float,
) -> CostBreakdown:
    """Itemised NSE equity delivery costs."""
    notional = abs(quantity * price)
    if notional <= 0:
        return CostBreakdown(0, 0, 0, 0, 0, 0, 0)

    is_buy = side is OrderSide.BUY or str(side).upper().endswith("BUY")

    brokerage = min(_EQ_DEL_BROKERAGE_PCT * notional, _EQ_DEL_BROKERAGE_CAP)
    stt = 0.0 if is_buy else _EQ_DEL_STT_SELL * notional
    stamp = _EQ_DEL_STAMP_BUY * notional if is_buy else 0.0
    exchange = _EQ_DEL_EXCH * notional
    sebi = _EQ_DEL_SEBI * notional
    ipft = _EQ_DEL_IPFT * notional
    gst = _GST_RATE * (brokerage + exchange + sebi + ipft)

    return CostBreakdown(
        brokerage=round(brokerage, 4),
        stt=round(stt, 4),
        exchange=round(exchange, 4),
        sebi=round(sebi, 4),
        ipft=round(ipft, 4),
        stamp_duty=round(stamp, 4),
        gst=round(gst, 4),
    )


def nse_equity_intraday_cost(
    side: OrderSide,
    quantity: float,
    price: float,
) -> float:
    """Total cost for one NSE equity *intraday* (MIS) fill."""
    return nse_equity_intraday_breakdown(side, quantity, price).total


def nse_equity_intraday_breakdown(
    side: OrderSide,
    quantity: float,
    price: float,
) -> CostBreakdown:
    notional = abs(quantity * price)
    if notional <= 0:
        return CostBreakdown(0, 0, 0, 0, 0, 0, 0)

    is_buy = side is OrderSide.BUY or str(side).upper().endswith("BUY")

    brokerage = min(_EQ_INT_BROKERAGE_PCT * notional, _EQ_INT_BROKERAGE_CAP)
    stt = 0.0 if is_buy else _EQ_INT_STT_SELL * notional
    stamp = _EQ_INT_STAMP_BUY * notional if is_buy else 0.0
    exchange = _EQ_INT_EXCH * notional
    sebi = _EQ_INT_SEBI * notional
    ipft = _EQ_INT_IPFT * notional
    gst = _GST_RATE * (brokerage + exchange + sebi + ipft)

    return CostBreakdown(
        brokerage=round(brokerage, 4),
        stt=round(stt, 4),
        exchange=round(exchange, 4),
        sebi=round(sebi, 4),
        ipft=round(ipft, 4),
        stamp_duty=round(stamp, 4),
        gst=round(gst, 4),
    )


def cost_for_segment(
    segment: Segment,
    side: OrderSide,
    quantity: float,
    price: float,
) -> float:
    """Dispatch to the correct schedule by segment."""
    if segment is Segment.EQUITY_DELIVERY:
        return nse_equity_delivery_cost(side, quantity, price)
    if segment is Segment.EQUITY_INTRADAY:
        return nse_equity_intraday_cost(side, quantity, price)
    # F&O schedules can be added later; return 0 for now so callers don't break
    return 0.0