"""Instrument identity and metadata (mirrors honba_messages::InstrumentId, honba_entities::Instrument)."""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass
from enum import Enum

from honba.domain.money import Currency, Money


@dataclass(frozen=True, slots=True)
class InstrumentId:
    """A symbol on an exchange, e.g. ``InstrumentId("NIFTY50", "NSE")``."""

    symbol: str
    exchange: str = "NSE"

    def __str__(self) -> str:
        return f"{self.symbol}.{self.exchange}"


class InstrumentKind(Enum):
    """The kind of instrument (mirrors ``honba_entities::InstrumentKind``)."""

    EQUITY = "equity"
    ETF = "etf"
    BOND = "bond"
    IPO = "ipo"
    FUTURE = "future"
    OPTION = "option"
    FX = "fx"
    INDEX = "index"
    MUTUAL_FUND = "mutual_fund"


class OptionType(Enum):
    """The type of an option contract: Call or Put (mirrors ``honba_entities::OptionType``)."""

    CALL = "call"
    PUT = "put"

    @property
    def code(self) -> str:
        """Return standard two-letter code ('CE' for Call, 'PE' for Put)."""
        return "CE" if self is OptionType.CALL else "PE"

    @classmethod
    def from_code(cls, code: str) -> OptionType:
        """Parse a string code ('CE'/'CALL' or 'PE'/'PUT')."""
        c = code.strip().upper()
        if c in ("CE", "CALL"):
            return cls.CALL
        if c in ("PE", "PUT"):
            return cls.PUT
        raise ValueError(f"unknown option type code {code!r}")


@dataclass(frozen=True, slots=True)
class Instrument:
    """Static metadata for a tradable instrument (mirrors ``honba_entities::Instrument``).

    ``lot_size`` is the minimum tradable quantity and ``tick_size`` the minimum price
    increment; both must be finite and > 0. ``currency`` is the ISO settlement currency.
    For derivative contracts, ``expiry``, ``strike``, ``option_type``, and ``underlying``
    provide contract specifications.
    """

    instrument_id: InstrumentId
    kind: InstrumentKind
    lot_size: float
    tick_size: float
    currency: str = "INR"
    expiry: datetime.date | None = None
    strike: float | None = None
    option_type: OptionType | None = None
    underlying: str | None = None

    def __post_init__(self) -> None:
        for name in ("lot_size", "tick_size"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"{name} must be positive, got {value}")

    @property
    def is_derivative(self) -> bool:
        """True when this instrument is a derivative contract (Future or Option)."""
        return self.kind in (InstrumentKind.FUTURE, InstrumentKind.OPTION)

    def stake_quantity(self, quantity: float) -> float:
        """Round a desired quantity **up** to the next lot multiple (ADR 0011).

        A stake that rounded down would silently under-size the position. A quantity
        already on a lot multiple (within float noise) is kept; negative or
        non-finite input raises ``ValueError``.
        """
        if not math.isfinite(quantity) or quantity < 0:
            raise ValueError(f"stake quantity must be finite and >= 0, got {quantity}")
        lots = quantity / self.lot_size
        nearest = round(lots)
        lots = nearest if abs(lots - nearest) < _LOT_TICK_TOLERANCE else math.ceil(lots)
        return lots * self.lot_size

    def is_on_tick(self, price: float) -> bool:
        """True when ``price`` sits on a tick, within float noise."""
        if not math.isfinite(price):
            return False
        ticks = price / self.tick_size
        return abs(ticks - round(ticks)) < _LOT_TICK_TOLERANCE

    def settle_notional(self, quantity: float, price: float) -> Money:
        """``quantity * price`` that settles, rounded once to minor units.

        An off-tick price raises ``ValueError``; it is never snapped (ADR 0011).
        """
        if not (math.isfinite(quantity) and math.isfinite(price)):
            raise ValueError("quantity and price must be finite")
        if not self.is_on_tick(price):
            raise ValueError(f"settlement price {price} is not on tick {self.tick_size}")
        return Money.mul_qty(quantity, price, Currency(self.currency))


_LOT_TICK_TOLERANCE = 1e-6
"""Float noise (in lots or ticks) below which a value sits exactly on a lot or tick."""
