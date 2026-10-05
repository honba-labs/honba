"""Strategy configuration (the ``config.toml`` next to each catalog strategy)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from honba.entities.instrument import InstrumentId

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib


class StrategyConfig(BaseModel):
    name: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    exchange: str = "NSE"
    params: dict[str, Any] = Field(default_factory=dict)
    # name -> {"kind": ..., **params}; built with honba.strategies.indicators.IndicatorBank
    indicators: dict[str, dict[str, Any]] = Field(default_factory=dict)
    # None keeps the engine default from the market pack (T+2 for NSE/BSE equities);
    # set 0..5 to override the clearing cycle for this strategy only.
    settlement_days: int | None = Field(
        default=None,
        ge=0,
        le=5,
        description=(
            "Override the India T+n settlement cycle; None uses the market pack default "
            "(T+2 for NSE/BSE equity delivery)."
        ),
    )
    settlement_calendar: str | None = Field(
        default=None,
        description="Settlement calendar key; None uses the market pack's is_settlement_day.",
    )

    @property
    def instrument_id(self) -> InstrumentId:
        return InstrumentId(self.symbol, self.exchange)

    @classmethod
    def from_toml(cls, path: str | Path) -> StrategyConfig:
        with open(path, "rb") as f:
            return cls.model_validate(tomllib.load(f))
