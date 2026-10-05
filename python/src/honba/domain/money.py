"""Integer Money in minor units (mirrors honba_entities::Money)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Currency(Enum):
    INR = "INR"
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"


MINOR_PER_MAJOR = 100


def _round_major_to_minor(major: float) -> int:
    if not (major == major and major != float("inf") and major != float("-inf")):
        raise ValueError("money amount must be finite")
    scaled = major * MINOR_PER_MAJOR
    if abs(scaled) > 2**63 - 1:
        raise ValueError("money amount exceeds i64 minor units")
    return int(round(scaled))


@dataclass(frozen=True, slots=True)
class Money:
    """A monetary amount in integer minor units (paise for INR, cents for others)."""

    amount: int
    currency: Currency

    @staticmethod
    def from_major(major: float, currency: Currency) -> Money:
        """Create Money from major units, rounding half away from zero."""
        return Money(_round_major_to_minor(major), currency)

    @staticmethod
    def from_minor(minor: int, currency: Currency) -> Money:
        """Create Money from integer minor units."""
        return Money(minor, currency)

    @staticmethod
    def zero(currency: Currency) -> Money:
        return Money(0, currency)

    def to_major(self) -> float:
        return self.amount / MINOR_PER_MAJOR

    def __add__(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError(f"currency mismatch: {self.currency} vs {other.currency}")
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError(f"currency mismatch: {self.currency} vs {other.currency}")
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __str__(self) -> str:
        return f"{self.currency.value} {self.to_major():.2f}"