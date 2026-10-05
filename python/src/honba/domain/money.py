"""Integer Money in minor units (mirrors ``honba_entities::Money``, ADR 0011).

Amounts are ``int`` minor units (paise for INR, cents for the rest), so the
ledger is exact. Conversion from major units is explicit and rounds the same
way as Rust:

- ``from_major`` / ``mul_qty``: nearest minor unit, half away from zero.
- ``payout_from_major``: floor (towards negative infinity) - money credited to
  the portfolio never rounds in its favour.
- ``stake_from_major``: ceiling - money committed is never silently under-sized.

Non-finite input and amounts outside ``i64`` are rejected with ``ValueError``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class Currency(Enum):
    INR = "INR"
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"


MINOR_PER_MAJOR = 100

_I64_MAX = 2**63 - 1

_DIRECTIONAL_NOISE_MINOR = 1e-6
"""Float noise below this many minor units is treated as exact before a floor or
ceiling, so ``0.1 * 3`` is a 30-paise stake, not 31 (same as Rust)."""


def _scaled(major: float) -> float:
    if not math.isfinite(major):
        raise ValueError("money amount must be finite")
    scaled = major * MINOR_PER_MAJOR
    if abs(scaled) > _I64_MAX:
        raise ValueError("money amount exceeds i64 minor units")
    return scaled


def _round_half_away(x: float) -> int:
    """``f64::round``: nearest integer, ties away from zero (``round()`` ties to even)."""
    whole = math.trunc(x)
    if abs(x - whole) >= 0.5:
        return whole + (1 if x > 0 else -1)
    return whole


def _snapped(major: float) -> float:
    scaled = _scaled(major)
    nearest = _round_half_away(scaled)
    return float(nearest) if abs(scaled - nearest) < _DIRECTIONAL_NOISE_MINOR else scaled


def _round_major_to_minor(major: float) -> int:
    return _round_half_away(_scaled(major))


@dataclass(frozen=True, slots=True)
class Money:
    """A monetary amount in integer minor units (paise for INR, cents for others)."""

    amount: int
    currency: Currency

    def __post_init__(self) -> None:
        if isinstance(self.amount, bool) or not isinstance(self.amount, int):
            raise TypeError(f"Money amount must be int minor units, got {self.amount!r}")
        if not isinstance(self.currency, Currency):
            raise TypeError(f"Money currency must be a Currency, got {self.currency!r}")
        if abs(self.amount) > _I64_MAX:
            raise ValueError("money amount exceeds i64 minor units")

    @staticmethod
    def from_major(major: float, currency: Currency) -> Money:
        """Create Money from major units, rounding half away from zero."""
        return Money(_round_major_to_minor(major), currency)

    @staticmethod
    def from_minor(minor: int, currency: Currency) -> Money:
        """Create Money from integer minor units."""
        return Money(minor, currency)

    @staticmethod
    def payout_from_major(major: float, currency: Currency) -> Money:
        """A payout: round down (towards negative infinity) to the minor unit."""
        return Money(math.floor(_snapped(major)), currency)

    @staticmethod
    def stake_from_major(major: float, currency: Currency) -> Money:
        """A stake: round up (towards positive infinity) to the minor unit."""
        return Money(math.ceil(_snapped(major)), currency)

    @staticmethod
    def mul_qty(quantity: float, price: float, currency: Currency) -> Money:
        """``quantity * price`` rounded once to the nearest minor unit (half away from zero)."""
        if not (math.isfinite(quantity) and math.isfinite(price)):
            raise ValueError("quantity must be finite")
        return Money.from_major(quantity * price, currency)

    @staticmethod
    def zero(currency: Currency) -> Money:
        return Money(0, currency)

    def to_major(self) -> float:
        """Major units as a float: lossy by definition, for display and research only."""
        return self.amount / MINOR_PER_MAJOR

    def _checked(self, other: Money) -> None:
        if not isinstance(other, Money):
            raise TypeError(f"expected Money, got {type(other).__name__}")
        if self.currency != other.currency:
            raise ValueError(f"currency mismatch: {self.currency} vs {other.currency}")

    def __add__(self, other: Money) -> Money:
        self._checked(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._checked(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __str__(self) -> str:
        return f"{self.currency.value} {self.to_major():.2f}"
