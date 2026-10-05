"""The strategy manifest: Python mirror of ``honba_strategy::StrategyManifest`` (plan.md E0-S8).

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

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from honba import _honba as _native
from honba.entities.instrument import InstrumentId
from honba.wire.wire import BarAggregation
from honba.wire.wire import InstrumentId as WireInstrumentId

__all__ = [
    "STRATEGY_API_VERSION",
    "ManifestError",
    "StrategyManifest",
    "Subscriptions",
    "TimeframeSpec",
    "Universe",
    "WarmupBars",
]

STRATEGY_API_VERSION: str = _native.STRATEGY_API_VERSION
"""Strategy contract version, read from its one owner ``honba_strategy::STRATEGY_API_VERSION``."""

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


class StrategyManifest(_Strict):
    """A verified, hashable description of a strategy, ready to be run."""

    api_version: str
    name: str
    source_hash: str
    universe: Universe
    subscriptions: Subscriptions
    driving_timeframe: TimeframeSpec
    warmup_bars: WarmupBars
    schedules: dict[str, str] = Field(default_factory=dict)

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
    ) -> StrategyManifest:
        """Start a manifest stamped with the current contract version (Rust ``new`` + ``with_*``)."""
        return cls(
            api_version=STRATEGY_API_VERSION,
            name=name,
            source_hash=source_hash,
            universe=universe,
            subscriptions=subscriptions or Subscriptions(instruments=[]),
            driving_timeframe=driving_timeframe,
            warmup_bars=warmup_bars,
            schedules=dict(schedules or {}),
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
        if self.api_version != STRATEGY_API_VERSION:
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

    def to_json_dict(self) -> dict[str, Any]:
        """The Rust serde shape (``universe`` externally tagged, empty ``schedules`` omitted)."""
        out = self.model_dump(mode="json", exclude={"universe", "schedules"})
        out["universe"] = self.universe.to_json()
        if self.schedules:
            out["schedules"] = dict(self.schedules)
        return out
