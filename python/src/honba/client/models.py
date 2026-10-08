"""Response records of the REST read API that the wire contract does not already model.

Bars and quotes are the existing :class:`honba.wire.wire.Bar` / :class:`QuoteTick` (timestamps
are :class:`~honba.wire.wire.UnixNanos`). These are *records* (ADR 0012): unknown fields a newer
server adds are ignored. Prices and sizes are ``f64`` observations; none of these endpoints
carries money, so ADR 0011's ``Money`` is not engaged.
"""

from __future__ import annotations

from typing import Any, Literal

from honba.wire.base import Str, _Wire
from honba.wire.wire import Currency, Float, InstrumentId, NonNegativeFloat

__all__ = [
    "TERMINAL_STATUSES",
    "Assumptions",
    "BacktestMetrics",
    "BacktestResult",
    "CapabilityManifest",
    "CompiledStrategy",
    "Depth",
    "DepthLevel",
    "ErrorDetail",
    "Health",
    "InstrumentInfo",
    "RunStatus",
    "ScreenerResultRow",
]


class Health(_Wire):
    """``GET /health``."""

    status: Str


class CapabilityManifest(_Wire):
    """The ``capabilities`` object of ``GET /capabilities``: what this server offers."""

    crates: tuple[Str, ...]
    market_packs: tuple[Str, ...]
    endpoints: tuple[Str, ...]
    """Every route in the contract as ``METHOD /path``, in registry order."""
    not_implemented: tuple[Str, ...] = ()
    """The subset of ``endpoints`` that answers 501 ``not_implemented`` today."""
    toolsets: tuple[Str, ...]
    adapters: tuple[Str, ...]
    features: dict[Str, bool]


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


class ScreenerResultRow(_Wire):
    """One instrument that passed ``GET /screener/scan``."""

    instrument_id: InstrumentId
    metrics: dict[str, float | None]
    """Latest value of every metric the filter reads, by key as written; ``None`` while warming up."""


RunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
"""Lifecycle of a run (ADR 0017 decision 1); the last three are terminal and final."""

TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})


class ErrorDetail(_Wire):
    """The envelope's ``ErrorDetail`` as a value: why a run failed (``status == "failed"``).

    A failed run is a successful poll, so the cause comes back as data, not as an exception.
    ``code`` is the Rust ``ErrorCode`` string (``internal_error``, ``market_data_unavailable``,
    ...); ``context`` carries ``reason`` (``journal_write``, ``panic``, ``interrupted``, ...).
    """

    code: Str
    message: Str
    retryable: bool = False
    context: Any = None


class BacktestMetrics(_Wire):
    """Headline metrics of a completed Rust-executor run.

    Basis (see :class:`Assumptions`): closed round trips only, no mark-to-market, Sharpe
    annualised at ``periods_per_year``. These are *not* ``BacktestSession`` metrics.
    """

    trades: int
    """Closed round trips (a position returning to flat)."""
    net_pnl: Float
    sharpe: Float
    max_drawdown: Float
    total_return: Float


class Assumptions(_Wire):
    """What the run did not model, and how it timed fills (ADR 0017 decision 7)."""

    not_modelled: tuple[Str, ...] = ()
    """Stable snake_case names of the effects the executor ignores (``slippage``, ...)."""
    timing: Str = ""
    """When orders fill relative to the decision bar."""
    fill_model: Str | None = None
    metrics_basis: Str | None = None
    periods_per_year: Float | None = None


class BacktestResult(_Wire):
    """``BacktestResponse``: the state of one backtest run.

    ``metrics`` only when ``completed``, ``assumptions`` only once terminal, ``error`` only
    when ``failed``.
    """

    run_id: Str
    status: RunStatus
    metrics: BacktestMetrics | None = None
    assumptions: Assumptions | None = None
    error: ErrorDetail | None = None

    @property
    def is_terminal(self) -> bool:
        """``True`` once the run can no longer change."""
        return self.status in TERMINAL_STATUSES
