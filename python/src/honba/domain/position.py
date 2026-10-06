"""Position domain model (mirrors honba_entities::Position)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from honba.domain.instrument import InstrumentId
from honba.domain.money import Currency, Money, _round_half_away


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
    ``avg_price`` rounds to the minor unit on every fill and ``realized_pnl`` is
    integer ``Money`` booked once per fill (ADR 0011), as in Rust.
    """

    instrument_id: InstrumentId
    currency: Currency = Currency.INR
    side: PositionSide = PositionSide.LONG
    quantity: float = 0.0
    avg_price: float = 0.0
    realized_pnl: Money = field(default_factory=lambda: Money.zero(Currency.INR))

    def __post_init__(self) -> None:
        if self.quantity < 0:
            raise ValueError(f"quantity must be >= 0, got {self.quantity}")
        if self.avg_price < 0:
            raise ValueError(f"avg_price must be >= 0, got {self.avg_price}")
        if not isinstance(self.realized_pnl, Money):
            raise TypeError(f"realized_pnl must be Money, got {type(self.realized_pnl)}")

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
            self.avg_price = _round_to_minor(fill_px, self.currency)
            return

        if self.side == fill_side:
            new_qty = self.quantity + fill_qty
            self.avg_price = _round_to_minor(
                ((self.quantity * self.avg_price) + (fill_qty * fill_px)) / new_qty,
                self.currency,
            )
            self.quantity = new_qty
        else:
            if fill_qty < self.quantity:
                closed_qty = fill_qty
                pnl = closed_qty * (fill_px - self.avg_price) * self.side.sign
                self.realized_pnl += Money.mul_qty(pnl, 1.0, self.currency)
                self.quantity -= fill_qty
            elif fill_qty == self.quantity:
                closed_qty = fill_qty
                pnl = closed_qty * (fill_px - self.avg_price) * self.side.sign
                self.realized_pnl += Money.mul_qty(pnl, 1.0, self.currency)
                self.quantity = 0.0
                self.avg_price = 0.0
            else:
                closed_qty = self.quantity
                pnl = closed_qty * (fill_px - self.avg_price) * self.side.sign
                self.realized_pnl += Money.mul_qty(pnl, 1.0, self.currency)
                remainder = fill_qty - self.quantity
                self.side = fill_side
                self.quantity = remainder
                self.avg_price = _round_to_minor(fill_px, self.currency)


def _round_to_minor(price: float, currency: Currency) -> float:
    """Nearest minor unit of ``currency``, half away from zero (``f64::round``, as in Rust)."""
    scale = currency.minor_per_major
    return _round_half_away(price * scale) / scale
