"""Integration test for sweep space extraction and manifest verification (E4-S2)."""

from __future__ import annotations

import pytest

from honba.entities.instrument import InstrumentId
from honba.strategies.base import Parameter, Strategy
from honba.strategies.manifest import (
    ParameterSpec,
    StrategyManifest,
    Subscriptions,
    TimeframeSpec,
    Universe,
    sweep_space_from_manifest,
    trial_grid_from_manifest,
)
from honba.strategies.verify import VerifyError, verify_manifest
from honba.wire.wire import BarAggregation

NIFTY = InstrumentId("NIFTY50", "NSE")


class SmaSampleStrategy(Strategy):
    name = "sma_sample"
    warmup_bars = 20
    fast = Parameter(default=10, min=10, max=30, step=10, description="Fast SMA")
    slow = Parameter(default=50, min=50, max=100, step=25, description="Slow SMA")


def test_sweep_space_from_strategy_class_and_instance() -> None:
    space = sweep_space_from_manifest(SmaSampleStrategy)
    assert space == {
        "fast": [10, 20, 30],
        "slow": [50, 75, 100],
    }

    grid = trial_grid_from_manifest(SmaSampleStrategy)
    assert len(grid) == 3 * 3
    assert grid[0] == {"fast": 10, "slow": 50}
    assert grid[-1] == {"fast": 30, "slow": 100}


def test_sweep_space_from_manifest_object() -> None:
    manifest = StrategyManifest.build(
        name="sma_sample",
        source_hash="sha256:abc",
        universe=Universe.of_explicit([NIFTY]),
        driving_timeframe=TimeframeSpec(interval=1, aggregation=BarAggregation.DAY),
        subscriptions=Subscriptions.of([NIFTY]),
        parameters={
            "fast": ParameterSpec(default=10, range="10:30:10", min=10.0, max=30.0, step=10.0),
            "slow": ParameterSpec(default=50, range="50:100:25", min=50.0, max=100.0, step=25.0),
        },
    )

    space = sweep_space_from_manifest(manifest)
    assert space["fast"] == [10, 20, 30]
    assert space["slow"] == [50, 75, 100]

    # Verify compiles to IR with parameters preserved
    ir = verify_manifest(manifest)
    assert "parameters" in ir["manifest"]
    assert ir["manifest"]["parameters"]["fast"]["default"] == 10


def test_verify_manifest_rejects_out_of_range_parameter_default() -> None:
    manifest = StrategyManifest.build(
        name="sma_sample",
        source_hash="sha256:abc",
        universe=Universe.of_explicit([NIFTY]),
        driving_timeframe=TimeframeSpec(interval=1, aggregation=BarAggregation.DAY),
        subscriptions=Subscriptions.of([NIFTY]),
        parameters={
            "fast": ParameterSpec(default=5, range="10:30:10", min=10.0, max=30.0, step=10.0),
        },
    )

    with pytest.raises(VerifyError) as exc_info:
        verify_manifest(manifest)
    assert exc_info.value.code == "parameter_default_out_of_range"
