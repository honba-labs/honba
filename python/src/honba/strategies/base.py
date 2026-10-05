"""The Strategy interface (ADR 008).

Mirrors the Rust ``Strategy`` trait: strategies receive typed callbacks and act
only through their ``StrategyContext`` (``self.ctx``): read the clock, positions,
cash and instrument metadata, and submit order intents. They never touch
execution directly, so the same strategy runs in backtest, paper and live.
"""

from __future__ import annotations

import logging
import warnings
from abc import ABC
from typing import TYPE_CHECKING, Any, ClassVar

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent
from honba.entities.tick import QuoteTick, TradeTick
from honba.entities.trade import Trade
from honba.strategies.context import LedgerContext, StrategyContext

if TYPE_CHECKING:
    from typing_extensions import Self

LEGACY_ENTRY_POINTS: tuple[str, ...] = ("drain_intents", "handle_fill", "handle_rejected")
"""Pre-ADR-008 runner entry points. Overriding one is deprecated (removed in 0.3)."""


class Strategy(ABC):
    """Subclass, set ``name``, override the hooks you need.

    Every hook defaults to a no-op. The runner binds a context before ``on_start``;
    a strategy used on its own (tests, ``honba.strategies.testing.replay``) gets a
    private ``LedgerContext``, so subclasses need not call ``super().__init__()``.
    """

    name: ClassVar[str]
    # Driving bars consumed before the first order (``StrategyManifest.warmup_bars``); the
    # runner suppresses orders until then. A runner or config value overrides it.
    warmup_bars: ClassVar[int] = 0

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        overridden = [m for m in LEGACY_ENTRY_POINTS if m in cls.__dict__]
        if overridden:
            warnings.warn(
                f"{cls.__qualname__} overrides {', '.join(overridden)}: these runner entry points "
                "are still called in 0.1/0.2 but not from 0.3, when the runner talks to the "
                "StrategyContext directly; move the logic to on_fill (ADR 008)",
                DeprecationWarning,
                stacklevel=3,  # past ABCMeta.__new__: the class statement of the subclass
            )

    def __new__(cls, *args: Any, **kwargs: Any) -> Self:
        if not getattr(cls, "name", None):
            raise TypeError(f"{cls.__name__} must define a class attribute `name`")
        self = super().__new__(cls)
        self._ctx = LedgerContext()
        self.logger = logging.getLogger(f"honba.strategy.{cls.name}")
        return self

    # -- context ---------------------------------------------------------------
    @property
    def ctx(self) -> StrategyContext:
        """The context this strategy reads from and submits to."""
        return self._ctx

    def bind(self, ctx: StrategyContext) -> None:
        """Attach the runner's context. Called by runners before ``on_start``."""
        if not isinstance(ctx, StrategyContext):
            raise TypeError(f"expected a StrategyContext, got {type(ctx).__name__}")
        self._ctx = ctx

    # -- hooks (all default to no-ops) -------------------------------------------
    def on_start(self) -> None: ...

    def on_bar(self, bar: Bar) -> None: ...

    def on_quote(self, quote: QuoteTick) -> None:
        """Top-of-book update."""

    def on_trade(self, trade: TradeTick) -> None:
        """A market trade print (the strategy's own executions arrive in ``on_fill``)."""

    def on_fill(self, fill: Trade) -> None: ...

    def on_stop(self) -> None: ...

    # -- conveniences (delegate to the context) ---------------------------------
    def position(self, instrument_id: InstrumentId) -> float:
        """Net signed quantity held, updated from fills (``self.ctx.position``)."""
        return self.ctx.position(instrument_id)

    def busy(self, instrument_id: InstrumentId) -> bool:
        """True while an order for this instrument is unfilled (``self.ctx.busy``).

        Fills arrive after the strategy emits an intent, so gate new orders on
        this to avoid duplicate entries or exits.
        """
        return self.ctx.busy(instrument_id)

    def log_event(self, event_type: str, message: str = "", **data: Any) -> None:
        """Emit a structured event log for this strategy.

        Parameters
        ----------
        event_type : str
            Event name/identifier (e.g. 'EVENT_BUY', 'EVENT_MEMBERSHIP_ADD').
        message : str, optional
            Optional human-readable description.
        **data : Any
            Key-value pairs to log as event attributes.
        """
        parts = [event_type]
        if message:
            parts.append(message)
        if data:
            parts.append(" ".join(f"{k}={v}" for k, v in data.items()))
        self.logger.info(" ".join(parts), extra={"event_type": event_type, "event_data": data})

    def buy(self, instrument_id: InstrumentId, quantity: float, **data: Any) -> None:
        self.log_event("EVENT_BUY", symbol=instrument_id.symbol, qty=quantity, **data)
        self.submit(OrderIntent.market_buy(instrument_id, quantity))

    def sell(self, instrument_id: InstrumentId, quantity: float, **data: Any) -> None:
        self.log_event("EVENT_SELL", symbol=instrument_id.symbol, qty=quantity, **data)
        self.submit(OrderIntent.market_sell(instrument_id, quantity))

    def submit(self, intent: OrderIntent) -> None:
        self.ctx.submit(intent)

    # -- legacy runner entry points (kept for one minor version) -----------------
    def drain_intents(self) -> list[OrderIntent]:
        """Returns and clears pending intents (legacy runner entry point)."""
        return self._ledger().drain_intents()

    def handle_fill(self, fill: Trade) -> None:
        """Legacy runner entry point: books the fill in the context, then calls ``on_fill``."""
        self._ledger().apply_fill(fill)
        self.on_fill(fill)

    def handle_rejected(self, intent: OrderIntent) -> None:
        """Legacy runner entry point: an order was rejected or cancelled unfilled."""
        self._ledger().release(intent)

    def _ledger(self) -> LedgerContext:
        if not isinstance(self.ctx, LedgerContext):
            raise TypeError(
                "drain_intents/handle_fill/handle_rejected need a LedgerContext; "
                f"this strategy is bound to {type(self.ctx).__name__}"
            )
        return self.ctx
