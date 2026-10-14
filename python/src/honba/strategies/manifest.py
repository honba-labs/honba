"""The strategy manifest: Python mirror of ``honba_strategy::StrategyManifest`` (E0-S8, E4-S2).

A manifest is data, not behaviour: the strategy's name, a digest of its source, the
universe it may trade, what it subscribes to, its driving timeframe and the number of
warm-up bars it needs before its first decision. Backtest, sweep and live read the
same manifest, so they cannot silently diverge.

The JSON shape is the Rust serde shape (unknown fields rejected, empty ``schedules``
omitted, ``universe`` externally tagged as ``{"named": ...}`` or ``{"explicit": [...]}``);
``schema/conformance/strategy_manifest.json`` pins it for both languages.
``validate_manifest`` raises :class:`ManifestError` with the same codes as Rust's
``ManifestError`` variants.

``warmup_bars`` is enforced by :class:`honba.strategies.runner.StrategyRunner`.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from honba._native import native_attr
from honba.entities.instrument import InstrumentId
from honba.wire.wire import BarAggregation
from honba.wire.wire import InstrumentId as WireInstrumentId

if TYPE_CHECKING:
    from honba.strategies.base import Parameter, Strategy

__all__ = [
    "STRATEGY_API_VERSION",  # noqa: F822 - lazy, see __getattr__
    "ManifestError",
    "ParameterSpec",
    "StrategyManifest",
    "Subscriptions",
    "TimeframeSpec",
    "Universe",
    "WarmupBars",
    "param_range_to_schema",
    "sweep_space_from_manifest",
    "trial_grid_from_manifest",
]


def __getattr__(name: str) -> Any:
    """Lazy ``STRATEGY_API_VERSION`` from its one owner ``honba_strategy::STRATEGY_API_VERSION``."""
    if name == "STRATEGY_API_VERSION":
        return native_attr(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_U32_MAX = 2**32 - 1

WarmupBars = Annotated[StrictInt, Field(ge=0, le=_U32_MAX)]
"""Bars consumed before the first decision (a Rust ``u32``)."""


class ManifestError(ValueError):
    """Why a manifest was rejected. ``code`` matches the Rust ``ManifestError`` variant."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _wire_ids(ids: list[InstrumentId]) -> list[WireInstrumentId]:
    return [WireInstrumentId.from_domain(i) for i in ids]


class Universe(_Strict):
    """Exactly one of ``named`` (resolved by the loader) or ``explicit``."""

    named: str | None = None
    explicit: list[WireInstrumentId] | None = None

    @model_validator(mode="after")
    def _one_of(self) -> Universe:
        if (self.named is None) == (self.explicit is None):
            raise ValueError("universe must be exactly one of 'named' or 'explicit'")
        return self

    @classmethod
    def of_named(cls, name: str) -> Universe:
        return cls(named=name)

    @classmethod
    def of_explicit(cls, ids: list[InstrumentId]) -> Universe:
        return cls(explicit=_wire_ids(ids))

    def to_json(self) -> dict[str, Any]:
        if self.named is not None:
            return {"named": self.named}
        return {"explicit": [i.model_dump(mode="json") for i in self.explicit or []]}


class Subscriptions(_Strict):
    """Market data the strategy consumes."""

    instruments: list[WireInstrumentId]
    quotes: StrictBool = False
    trades: StrictBool = False

    @classmethod
    def of(
        cls, ids: list[InstrumentId], *, quotes: bool = False, trades: bool = False
    ) -> Subscriptions:
        return cls(instruments=_wire_ids(ids), quotes=quotes, trades=trades)


class TimeframeSpec(_Strict):
    """A bar aggregation interval, e.g. ``interval=5, aggregation=MINUTE``."""

    interval: Annotated[StrictInt, Field(ge=0, le=_U32_MAX)]
    aggregation: BarAggregation


class ParameterSpec(_Strict):
    """A parameter declaration with default and optional sweep range (E4-S2)."""

    default: Any
    range: str | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    description: str | None = None

    @classmethod
    def from_range(
        cls,
        range_expr: str | tuple[float, float, float] | list[float],
        default: Any,
        description: str | None = None,
    ) -> ParameterSpec:
        if isinstance(range_expr, str):
            parts = [p.strip() for p in range_expr.split(":")]
            if len(parts) != 3:
                raise ManifestError(
                    "invalid_parameter_range", f"range must be min:max:step, got {range_expr!r}"
                )
            try:
                min_val = float(parts[0])
                max_val = float(parts[1])
                step_val = float(parts[2])
            except ValueError as exc:
                raise ManifestError("invalid_parameter_range", str(exc)) from None
            range_str = range_expr
        elif isinstance(range_expr, (tuple, list)) and len(range_expr) == 3:
            min_val, max_val, step_val = (
                float(range_expr[0]),
                float(range_expr[1]),
                float(range_expr[2]),
            )
            range_str = f"{min_val}:{max_val}:{step_val}"
        else:
            raise ManifestError("invalid_parameter_range", f"invalid range format: {range_expr!r}")

        if min_val > max_val:
            raise ManifestError(
                "invalid_parameter_range", f"min ({min_val}) cannot be greater than max ({max_val})"
            )
        if step_val <= 0:
            raise ManifestError("invalid_parameter_range", f"step ({step_val}) must be positive")

        if isinstance(default, (int, float)):
            if default < min_val:
                raise ManifestError(
                    "parameter_default_out_of_range",
                    f"default {default} is below minimum {min_val}",
                )
            if default > max_val:
                raise ManifestError(
                    "parameter_default_out_of_range",
                    f"default {default} is above maximum {max_val}",
                )

        return cls(
            default=default,
            range=range_str,
            min=min_val,
            max=max_val,
            step=step_val,
            description=description,
        )

    def discrete_values(self) -> list[Any]:
        if self.min is not None and self.max is not None and self.step is not None:
            is_int = (
                isinstance(self.default, int) and self.min.is_integer() and self.step.is_integer()
            )
            vals = []
            curr = self.min
            while curr <= self.max + 1e-9:
                vals.append(round(curr) if is_int else round(curr, 6))
                curr += self.step
            return vals
        return [self.default]


def param_range_to_schema(
    range_expr: str | tuple[float, float, float] | list[float] | Parameter,
    default: Any = None,
    description: str | None = None,
) -> ParameterSpec:
    """Convert a parameter range expression or Parameter to a ParameterSpec (E4-S2)."""
    from honba.strategies.base import Parameter

    if isinstance(range_expr, Parameter):
        return range_expr.to_spec()
    if default is None:
        raise ManifestError(
            "parameter_default_out_of_range", "default value is required for param_range_to_schema"
        )
    return ParameterSpec.from_range(range_expr, default, description=description)


class StrategyManifest(_Strict):
    """A verified, hashable description of a strategy, ready to run.

    ``graph_hash`` and ``sidecar`` are **Python-only** provenance fields (ADR 0029, D16): the
    digest of the canonical ``StrategyGraph`` JSON a generated strategy came from, and the
    optional ``.strategy.json`` path beside it. They are deliberately **absent from the Rust
    serde shape** (``manifest.rs`` is untouched and rejects unknown fields), so
    :meth:`to_json_dict` strips them; only the Python control plane reads them.
    """

    api_version: str
    name: str
    source_hash: str
    universe: Universe
    subscriptions: Subscriptions
    driving_timeframe: TimeframeSpec
    warmup_bars: WarmupBars
    schedules: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, ParameterSpec] = Field(default_factory=dict)
    # Python-only (stripped from the Rust JSON shape by ``to_json_dict``).
    graph_hash: str | None = None
    sidecar: str | None = None

    @classmethod
    def build(
        cls,
        name: str,
        source_hash: str,
        universe: Universe,
        driving_timeframe: TimeframeSpec,
        *,
        subscriptions: Subscriptions | None = None,
        warmup_bars: int = 0,
        schedules: dict[str, str] | None = None,
        parameters: dict[str, ParameterSpec] | None = None,
        graph_hash: str | None = None,
        sidecar: str | None = None,
    ) -> StrategyManifest:
        """Start a manifest stamped with the current contract version (Rust ``new`` + ``with_*``)."""
        return cls(
            api_version=native_attr("STRATEGY_API_VERSION"),
            name=name,
            source_hash=source_hash,
            universe=universe,
            subscriptions=subscriptions or Subscriptions(instruments=[]),
            driving_timeframe=driving_timeframe,
            warmup_bars=warmup_bars,
            schedules=dict(schedules or {}),
            parameters=dict(parameters or {}),
            graph_hash=graph_hash,
            sidecar=sidecar,
        )

    @classmethod
    def from_strategy(
        cls,
        strategy_cls: type[Strategy],
        source_hash: str,
        universe: Universe,
        driving_timeframe: TimeframeSpec,
        *,
        subscriptions: Subscriptions | None = None,
        schedules: dict[str, str] | None = None,
        graph_hash: str | None = None,
        sidecar: str | None = None,
    ) -> StrategyManifest:
        """Create a manifest directly from a Strategy class, extracting its name, warmup, and hyperparameters."""
        hparams = strategy_cls.hyperparameters()
        param_specs = {k: p.to_spec() for k, p in hparams.items()}
        return cls.build(
            name=strategy_cls.name,
            source_hash=source_hash,
            universe=universe,
            driving_timeframe=driving_timeframe,
            subscriptions=subscriptions,
            warmup_bars=strategy_cls.warmup_bars,
            schedules=schedules,
            parameters=param_specs,
            graph_hash=graph_hash,
            sidecar=sidecar,
        )

    def instruments(self) -> list[InstrumentId]:
        """Sorted, de-duplicated union of the subscriptions and an explicit universe."""
        ids = {i.to_domain() for i in self.subscriptions.instruments}
        ids.update(i.to_domain() for i in self.universe.explicit or [])
        return sorted(ids, key=lambda i: (i.symbol, i.exchange))

    def validate_manifest(self) -> None:
        """Check internal consistency; raise :class:`ManifestError` like Rust ``validate``."""
        if not self.name.strip():
            raise ManifestError("empty_name", "strategy name must not be empty")
        if not self.source_hash.strip():
            raise ManifestError("empty_source_hash", "source_hash must not be empty")
        if self.api_version != native_attr("STRATEGY_API_VERSION"):
            raise ManifestError(
                "unsupported_api_version",
                f"unsupported strategy api_version: {self.api_version}",
            )
        if self.driving_timeframe.interval == 0:
            raise ManifestError("zero_interval", "timeframe interval must be > 0")
        if self.universe.named is not None and not self.subscriptions.instruments:
            raise ManifestError(
                "named_universe_unresolved",
                "named universe must be resolved to instruments before running",
            )
        # Validate parameters and ensure defaults stay within range
        for name, param in self.parameters.items():
            if param.min is not None and param.max is not None and param.min > param.max:
                raise ManifestError("invalid_parameter_range", f"parameter {name!r} min > max")
            if param.step is not None and param.step <= 0:
                raise ManifestError("invalid_parameter_range", f"parameter {name!r} step <= 0")
            if isinstance(param.default, (int, float)):
                if param.min is not None and param.default < param.min:
                    raise ManifestError(
                        "parameter_default_out_of_range", f"parameter {name!r} default < min"
                    )
                if param.max is not None and param.default > param.max:
                    raise ManifestError(
                        "parameter_default_out_of_range", f"parameter {name!r} default > max"
                    )

    def to_json_dict(self) -> dict[str, Any]:
        """The Rust serde shape (``universe`` externally tagged, empty ``schedules`` omitted).

        ``graph_hash`` / ``sidecar`` are Python-only and stripped here: the Rust
        ``StrategyManifest`` rejects unknown fields, so they must never reach
        ``honba verify`` or the REST ``/strategies`` body.
        """
        out = self.model_dump(
            mode="json", exclude={"universe", "schedules", "parameters", "graph_hash", "sidecar"}
        )
        out["universe"] = self.universe.to_json()
        if self.schedules:
            out["schedules"] = dict(self.schedules)
        if self.parameters:
            out["parameters"] = {
                k: v.model_dump(mode="json", exclude_none=True) for k, v in self.parameters.items()
            }
        return out


def sweep_space_from_manifest(
    manifest: StrategyManifest | type[Strategy] | Strategy | Mapping[str, Any],
) -> dict[str, list[Any]]:
    """Extract discrete search space choices from a manifest or strategy (E4-S2)."""
    from honba.strategies.base import Strategy

    if isinstance(manifest, type) and issubclass(manifest, Strategy):
        params = manifest.hyperparameters()
        return {k: p.discrete_values() for k, p in params.items()}
    if isinstance(manifest, Strategy):
        params = manifest.hyperparameters()
        return {k: p.discrete_values() for k, p in params.items()}
    if isinstance(manifest, StrategyManifest):
        return {k: spec.discrete_values() for k, spec in manifest.parameters.items()}
    if isinstance(manifest, Mapping):
        params_dict = manifest.get("parameters", {})
        res = {}
        for k, v in params_dict.items():
            if isinstance(v, dict):
                spec = ParameterSpec.model_validate(v)
                res[k] = spec.discrete_values()
        return res
    raise TypeError(f"unsupported manifest type: {type(manifest)}")


def trial_grid_from_manifest(
    manifest: StrategyManifest | type[Strategy] | Strategy | Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Cartesian product of all parameter sweep choices (E4-S2)."""
    space = sweep_space_from_manifest(manifest)
    if not space:
        return []
    keys = list(space.keys())
    value_lists = [space[k] for k in keys]
    grid = []
    for combo in itertools.product(*value_lists):
        grid.append(dict(zip(keys, combo, strict=True)))
    return grid
