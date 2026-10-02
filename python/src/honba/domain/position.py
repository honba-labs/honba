"""Position domain model (mirrors honba_entities::Position)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from honba.domain.instrument import InstrumentId


class PositionSide(Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> float:
        return 1.0 if self is PositionSide.LONG else -1.0

    @property
    def opposite(self) -> PositionSide:
        return PositionSide.SHORT if self is PositionSide.LONG else PositionSide.LONG


@dataclass(slots=True)
class Position:
    """A position in a single instrument.

    A position is flat when quantity == 0. Fills update the position via apply_fill.
    """

    instrument_id: InstrumentId
    currency: str = "INR"
    side: PositionSide = PositionSide.LONG
    quantity: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0

    def __post_init__(self) -> None:
        if self.quantity < 0:
            raise ValueError(f"quantity must be >= 0, got {self.quantity}")
        if self.avg_price < 0:
            raise ValueError(f"avg_price must be >= 0, got {self.avg_price}")
        if not math.isfinite(self.realized_pnl):
            raise ValueError(f"realized_pnl must be finite, got {self.realized_pnl}")

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0.0

    @property
    def signed_quantity(self) -> float:
        return self.quantity * self.side.sign

    def apply_fill(self, fill_side: PositionSide, fill_qty: float, fill_px: float) -> None:
        if fill_qty <= 0:
            raise ValueError(f"fill quantity must be > 0, got {fill_qty}")
        if fill_px <= 0:
            raise ValueError(f"fill price must be > 0, got {fill_px}")

        if self.is_flat:
            self.side = fill_side
            self.quantity = fill_qty
            self.avg_price = fill_px
            return

        if self.side == fill_side:
            new_qty = self.quantity + fill_qty
            self.avg_price = ((self.quantity * self.avg_price) + (fill_qty * fill_px)) / new_qty
            self.quantity = new_qty
        else:
            if fill_qty < self.quantity:
                closed_qty = fill_qty
                pnl = closed_qty * (fill_px - self.avg_price) * self.side.sign
                self.realized_pnl += pnl
                self.quantity -= fill_qty
            elif fill_qty == self.quantity:
                closed_qty = fill_qty
                pnl = closed_qty * (fill_px - self.avg_price) * self.side.sign
                self.realized_pnl += pnl
                self.quantity = 0.0
                self.avg_price = 0.0
            else:
                closed_qty = self.quantity
                pnl = closed_qty * (fill_px - self.avg_price) * self.side.sign
                self.realized_pnl += pnl
                remainder = fill_qty - self.quantity
                self.side = fill_side
                self.quantity = remainder
                self.avg_price = fill_px
