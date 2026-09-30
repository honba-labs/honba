"""Streaming indicators, grouped by family like TradingView's taxonomy
(moving_average, trend, momentum, volatility, volume, support_resistance, breadth, statistical).

``update`` returns ``None`` until warmed up. Import from the family
(``indicators.momentum.Rsi``) or from here (``indicators.Rsi``).
"""
from __future__ import annotations

from honba.strategies.indicators import (  # noqa: F401  (importing registers the indicators)
    breadth, momentum, moving_average, statistical, support_resistance, trend, volatility, volume,
)
from honba.strategies.indicators.momentum import Kdj, Rsi
from honba.strategies.indicators.moving_average import Ema, Rma, Sma, Wma, make_ma
from honba.strategies.indicators.trend import Ichimoku, Macd, MacdValue
from honba.strategies.indicators.volatility import (
    Atr, Bollinger, BollingerValue, Donchian, DonchianValue,
)

from honba.strategies.indicators import _base
from honba.strategies.indicators._base import FAMILIES, Indicator
from honba.strategies.indicators.bank import IndicatorBank


def indicator_family(kind: str) -> str:
    return _base.get(kind).family


def indicator_spec(kind: str) -> dict:
    """JSON-serialisable description of an indicator (params, inputs, outputs, family)."""
    return _base.spec(kind)


def list_indicators(family: str | None = None) -> list[str]:
    """Indicator kinds, optionally only those in ``family``."""
    if family is not None and family not in FAMILIES:
        raise ValueError(f"unknown family {family!r}; choose from {list(FAMILIES)}")
    return sorted(k for k, c in _base._REGISTRY.items() if family is None or c.family == family)


def build_indicator(kind: str, **params):
    """Builds an indicator from a config-style spec, e.g. ``build_indicator("ema", period=20)``.

    Unknown kinds raise ``ValueError``; unknown or invalid parameters raise ``TypeError`` /
    ``ValueError`` from the indicator itself.
    """
    return _base.get(kind)(**params)


__all__ = [
    "Sma", "Ema", "Rma", "Wma", "Rsi", "Macd", "MacdValue", "Bollinger", "BollingerValue",
    "Donchian", "DonchianValue", "Atr", "Kdj", "Ichimoku", "make_ma", "build_indicator",
    "indicator_family", "indicator_spec", "list_indicators", "FAMILIES", "Indicator", "IndicatorBank",
]


def __getattr__(name: str):
    """Flat access to every registered indicator class: ``from ...indicators import Adx``."""
    for cls in _base._REGISTRY.values():
        if cls.__name__ == name:
            return cls
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
