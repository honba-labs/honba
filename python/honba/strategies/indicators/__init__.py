"""Streaming indicators, grouped by family like TradingView's taxonomy
(moving_average, trend, momentum, volatility, volume, support_resistance, breadth, statistical).

``update`` returns ``None`` until warmed up. Import from the family
(``indicators.momentum.Rsi``) or from here (``indicators.Rsi``).
"""
from __future__ import annotations

from honba.strategies.indicators.momentum import Kdj, Rsi
from honba.strategies.indicators.moving_average import Ema, Rma, Sma, Wma, make_ma
from honba.strategies.indicators.trend import Ichimoku, Macd, MacdValue
from honba.strategies.indicators.volatility import (
    Atr, Bollinger, BollingerValue, Donchian, DonchianValue,
)

# kind -> (class, family)
_REGISTRY = {
    "sma": (Sma, "moving_average"), "ema": (Ema, "moving_average"),
    "rma": (Rma, "moving_average"), "wma": (Wma, "moving_average"),
    "macd": (Macd, "trend"), "ichimoku": (Ichimoku, "trend"),
    "rsi": (Rsi, "momentum"), "kdj": (Kdj, "momentum"),
    "atr": (Atr, "volatility"), "bollinger": (Bollinger, "volatility"),
    "donchian": (Donchian, "volatility"),
}
# TradingView's grouping. Only some families have implementations so far.
FAMILIES = (
    "moving_average", "trend", "momentum", "volatility",
    "volume", "support_resistance", "breadth", "statistical",
)


def _lookup(kind: str):
    try:
        return _REGISTRY[kind]
    except KeyError:
        raise ValueError(f"unknown indicator {kind!r}; choose from {sorted(_REGISTRY)}") from None


def indicator_family(kind: str) -> str:
    return _lookup(kind)[1]


def list_indicators(family: str | None = None) -> list[str]:
    """Indicator kinds, optionally only those in ``family``."""
    if family is not None and family not in FAMILIES:
        raise ValueError(f"unknown family {family!r}; choose from {list(FAMILIES)}")
    return sorted(k for k, (_, f) in _REGISTRY.items() if family is None or f == family)


def build_indicator(kind: str, **params):
    """Builds an indicator from a config-style spec, e.g. ``build_indicator("ema", period=20)``.

    Unknown kinds raise ``ValueError``; unknown or invalid parameters raise ``TypeError`` /
    ``ValueError`` from the indicator itself.
    """
    return _lookup(kind)[0](**params)


__all__ = [
    "Sma", "Ema", "Rma", "Wma", "Rsi", "Macd", "MacdValue", "Bollinger", "BollingerValue",
    "Donchian", "DonchianValue", "Atr", "Kdj", "Ichimoku", "make_ma", "build_indicator",
    "indicator_family", "list_indicators", "FAMILIES",
]
