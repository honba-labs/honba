"""Response records of the REST read API that the wire contract does not already model.

Bars and quotes are the existing :class:`honba.wire.wire.Bar` / :class:`QuoteTick` (timestamps
are :class:`~honba.wire.wire.UnixNanos`). These are *records* (ADR 0012): unknown fields a newer
server adds are ignored. Prices and sizes are ``f64`` observations; none of these endpoints
carries money, so ADR 0011's ``Money`` is not engaged.
"""

from __future__ import annotations

from typing import Any

from honba.wire.base import Str, _Wire
from honba.wire.wire import Currency, Float, InstrumentId, NonNegativeFloat

__all__ = ["CompiledStrategy", "Depth", "DepthLevel", "Health", "InstrumentInfo"]


class Health(_Wire):
    """``GET /health``."""

    status: Str


class InstrumentInfo(_Wire):
    """One reference-data row of ``GET /instruments[/{id}]``."""

    id: InstrumentId
    kind: Str
    """``equity``, ``etf``, ``index``, ``future``, ``option``, ... (``other`` if unmapped)."""
    currency: Currency
    lot_size: Float
    tick_size: Float


class DepthLevel(_Wire):
    """One price level of the order book."""

    price: Float
    qty: NonNegativeFloat


class Depth(_Wire):
    """``GET /depth/{id}``: bids best (highest) first, asks best (lowest) first."""

    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]


class CompiledStrategy(_Wire):
    """One strategy of ``GET /strategies`` or the result of ``POST /strategies``."""

    id: Str
    """Content id: ``sha256:`` plus the digest of the canonical manifest JSON (deterministic)."""
    ir: dict[str, Any]
    """The verified IR, a JSON-shaped record exactly as :meth:`Client.verify_strategy` returns it."""
