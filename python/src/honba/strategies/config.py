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
    venue: str = "NSE"
    params: dict[str, Any] = Field(default_factory=dict)
    # name -> {"kind": ..., **params}; built with honba.strategies.indicators.IndicatorBank
    indicators: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @property
    def instrument_id(self) -> InstrumentId:
        return InstrumentId(self.symbol, self.venue)

    @classmethod
    def from_toml(cls, path: str | Path) -> StrategyConfig:
        with open(path, "rb") as f:
            return cls.model_validate(tomllib.load(f))
