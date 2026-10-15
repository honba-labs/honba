"""The Strategy interface (ADR 008, E4-S2).

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

    from honba.strategies.manifest import ParameterSpec

__all__ = [
    "LEGACY_ENTRY_POINTS",
    "Parameter",
    "Strategy",
]

LEGACY_ENTRY_POINTS: tuple[str, ...] = ("drain_intents", "handle_fill", "handle_rejected")
"""Pre-ADR-008 runner entry points. Overriding one is deprecated (removed in 0.3)."""


class Parameter:
    """One-line parameter declaration for strategy hyperparameters (E4-S2).

    Can be used as a class attribute descriptor:
    ```python
    class MyStrategy(Strategy):
        fast = Parameter(default=20, min=5, max=50, step=5, description="Fast SMA")
    ```
    """

    def __init__(
        self,
        default: Any,
        *,
        min: float | None = None,
        max: float | None = None,
        step: float | None = None,
        range: str | tuple[float, float, float] | list[float] | None = None,
        values: list[Any] | None = None,
        description: str = "",
    ) -> None:
        self.default = default
        self.description = description
        self.name: str = ""
        self.values = values
        self.range_str: str | None = None

        if range is not None:
            if isinstance(range, str):
                parts = [p.strip() for p in range.split(":")]
                if len(parts) != 3:
                    raise ValueError(f"range string must be 'min:max:step', got {range!r}")
                self.min = float(parts[0]) if "." in parts[0] else int(parts[0])
                self.max = float(parts[1]) if "." in parts[1] else int(parts[1])
                self.step = float(parts[2]) if "." in parts[2] else int(parts[2])
                self.range_str = range
            elif isinstance(range, (tuple, list)) and len(range) == 3:
                self.min, self.max, self.step = range[0], range[1], range[2]
                self.range_str = f"{self.min}:{self.max}:{self.step}"
            else:
                raise ValueError(f"range must be 'min:max:step' or (min, max, step), got {range!r}")
        else:
            self.min = min
            self.max = max
            self.step = step
            if min is not None and max is not None and step is not None:
                self.range_str = f"{min}:{max}:{step}"

        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"min ({self.min}) cannot exceed max ({self.max})")
        if self.step is not None and self.step <= 0:
            raise ValueError(f"step ({self.step}) must be > 0")

        # Validate default value against bounds
        if isinstance(self.default, (int, float)):
            if self.min is not None and self.default < self.min:
                raise ValueError(f"default {self.default} is less than min {self.min}")
            if self.max is not None and self.default > self.max:
                raise ValueError(f"default {self.default} is greater than max {self.max}")

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def __get__(self, instance: Any, owner: type) -> Any:
        if instance is None:
            return self
        params = getattr(instance, "_params", None)
        if params is not None and self.name in params:
            return params[self.name]
        return self.default

    def discrete_values(self) -> list[Any]:
        """Returns all discrete values across the declared range."""
        if self.values is not None:
            return list(self.values)
        if self.min is not None and self.max is not None and self.step is not None:
            is_int = (
                isinstance(self.default, int)
                and (isinstance(self.min, int) or float(self.min).is_integer())
                and (isinstance(self.step, int) or float(self.step).is_integer())
            )
            vals = []
            curr = float(self.min)
            max_val = float(self.max)
            step_val = float(self.step)
            while curr <= max_val + 1e-9:
                vals.append(round(curr) if is_int else round(curr, 6))
                curr += step_val
            return vals
        return [self.default]

    def to_spec(self) -> ParameterSpec:
        from honba.strategies.manifest import ParameterSpec

        return ParameterSpec(
            default=self.default,
            range=self.range_str,
            min=float(self.min) if self.min is not None else None,
            max=float(self.max) if self.max is not None else None,
            step=float(self.step) if self.step is not None else None,
            description=self.description or None,
        )


class Strategy(ABC):
    """Subclass, set ``name``, override the hooks you need.

    Every hook defaults to a no-op. The runner binds a context before ``on_start``;
    a strategy used on its own (tests, ``honba.strategies.testing.replay``) gets a
    private ``LedgerContext``, so subclasses need not call ``super().__init__()``.\
    """

    name: ClassVar[str]
    # Driving bars consumed before the first order (``StrategyManifest.warmup_bars``); the
    # runner suppresses orders until then. A runner or config value overrides it.
    warmup_bars: ClassVar[int] = 0
    _declared_parameters: ClassVar[dict[str, Parameter]] = {}
    _ctx: StrategyContext
    logger: logging.Logger
    _params: dict[str, Any]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Collect declared Parameter instances across class hierarchy
        declared: dict[str, Parameter] = {}
        for base in reversed(cls.__mro__):
            if hasattr(base, "_declared_parameters"):
                declared.update(base._declared_parameters)
        for k, v in cls.__dict__.items():
            if isinstance(v, Parameter):
                declared[k] = v
        cls._declared_parameters = declared

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
        self._params = {}
        if args and hasattr(args[0], "params") and isinstance(args[0].params, dict):
            self._params = dict(args[0].params)
        elif "config" in kwargs and hasattr(kwargs["config"], "params"):
            self._params = dict(kwargs["config"].params)
        elif "params" in kwargs and isinstance(kwargs["params"], dict):
            self._params = dict(kwargs["params"])
        return self

    @classmethod
    def hyperparameters(cls) -> dict[str, Parameter]:
        """Declared hyperparameters for this strategy (E4-S2)."""
        return dict(cls._declared_parameters)

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

    def cancel(self, intent: OrderIntent) -> None:
        """Ask for the order sent from ``intent`` to be cancelled (idempotent, see the context)."""
        self.ctx.cancel(intent)

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
