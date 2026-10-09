"""Option domain models (ADR 0014).

Option contracts, chains, styles and kinds for derivative pricing and execution.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from typing import Any

from honba.domain.instrument import InstrumentId

__all__ = ["OptionChain", "OptionContract", "OptionKind", "OptionStyle"]


class OptionKind(Enum):
    """Call or Put option."""

    CALL = "call"
    PUT = "put"


class OptionStyle(Enum):
    """Exercise style: European or American."""

    EUROPEAN = "european"
    AMERICAN = "american"


@dataclass(frozen=True, slots=True)
class OptionContract:
    """A single option contract."""

    instrument_id: InstrumentId
    expiry: dt.date
    strike: float
    kind: OptionKind
    style: OptionStyle = OptionStyle.EUROPEAN
    bid: float = 0.0
    ask: float = 0.0
    mid: float = 0.0
    iv: float = 0.0
    open_interest: float = 0.0
    volume: float = 0.0
    greeks: Any = None


@dataclass(frozen=True, slots=True)
class OptionChain:
    """A snapshot of options across strikes and expiries for an underlying."""

    underlying: InstrumentId
    expiry: dt.date
    spot: float
    contracts: list[OptionContract]
