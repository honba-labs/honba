"""Named criteria presets and metric lookback catalog metadata (Design.md Section 5.5 & 12.3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from honba.entities.screener import FilterOp, MetricRef, ScreenerFilterPredicate


@dataclass(frozen=True)
class PresetDefinition:
    key: str
    label: str
    description: str
    target_metric: str
    aliases: tuple[str, ...]
    lookback_bars: int = 252


PRESETS: dict[str, PresetDefinition] = {
    "52_week_low": PresetDefinition(
        key="52_week_low",
        label="52 Week Low",
        description="Instrument is trading at or near its 52 week lowest price",
        target_metric="price_52_week_low",
        aliases=("52 week low", "52w low", "52w-low", "year low"),
        lookback_bars=252,
    ),
    "52_week_high": PresetDefinition(
        key="52_week_high",
        label="52 Week High",
        description="Instrument is trading at or near its 52 week highest price",
        target_metric="price_52_week_high",
        aliases=("52 week high", "52w high", "52w-high", "year high"),
        lookback_bars=252,
    ),
}

# Per-metric lookback bars for gap planning (Section 12.3)
LOOKBACK_BARS: dict[str, int] = {
    "price_52_week_low": 252,
    "price_52_week_high": 252,
    "SMA200": 200,
    "SMA50": 50,
    "SMA20": 20,
    "RSI": 14,
    "close": 1,
    "open": 1,
    "high": 1,
    "low": 1,
    "volume": 1,
}


def get_lookback_bars(metric_key: str) -> int:
    """Return required lookback bars for a metric, defaulting to 1."""
    return LOOKBACK_BARS.get(metric_key, 1)


def list_presets() -> dict[str, PresetDefinition]:
    """Return dictionary of all available screener presets."""
    return PRESETS


def resolve_preset_key(phrase: str) -> PresetDefinition | None:
    """Resolve a phrase (e.g. '52 week low') to a PresetDefinition."""
    norm = phrase.lower().replace("-", " ").strip()
    for preset in PRESETS.values():
        if norm == preset.key or norm in [a.lower().replace("-", " ") for a in preset.aliases]:
            return preset
    return None


def expand_preset(
    kind: Literal["at", "near", "within"],
    preset_key: str,
    tolerance_pct: float = 5.0,
) -> list[ScreenerFilterPredicate]:
    """Expand a preset criterion into ScreenerFilterPredicate(s).

    Rules (Design.md Section 5.5):
    - `at 52 week low`: close <= price_52_week_low
    - `near 52 week low`: close <= price_52_week_low * (1 + tol)
    - `at 52 week high`: close >= price_52_week_high
    - `near 52 week high`: close >= price_52_week_high * (1 - tol)
    """
    preset = PRESETS.get(preset_key)
    if preset is None:
        raise ValueError(f"unknown preset {preset_key!r}")

    if preset_key == "52_week_low":
        return [
            ScreenerFilterPredicate(
                key="close",
                op=FilterOp.LTE,
                value=MetricRef(key=preset.target_metric),
            )
        ]
    elif preset_key == "52_week_high":
        return [
            ScreenerFilterPredicate(
                key="close",
                op=FilterOp.GTE,
                value=MetricRef(key=preset.target_metric),
            )
        ]
    else:
        raise NotImplementedError(f"preset {preset_key} expansion not implemented")
