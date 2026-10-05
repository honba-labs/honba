"""``honba.strategies.manifest``: the Python mirror of ``honba_strategy::StrategyManifest``."""

from __future__ import annotations

import pytest

from honba.entities.instrument import InstrumentId
from honba.strategies.manifest import (
    STRATEGY_API_VERSION,
    ManifestError,
    StrategyManifest,
    Subscriptions,
    TimeframeSpec,
    Universe,
)
from honba.wire.wire import BarAggregation

NIFTY = InstrumentId("NIFTY50", "NSE")


def sample(**changes: object) -> StrategyManifest:
    m = StrategyManifest.build(
        "sma_crossover",
        "sha256:abc123",
        Universe.of_explicit([NIFTY]),
        TimeframeSpec(interval=1, aggregation=BarAggregation.DAY),
        subscriptions=Subscriptions.of([NIFTY]),
        warmup_bars=20,
        schedules={"session_open": "09:15:00+05:30"},
    )
    return m.model_copy(update=changes)


def test_builder_stamps_the_contract_version_and_validates() -> None:
    m = sample()
    assert m.api_version == STRATEGY_API_VERSION == "1.0.0"
    m.validate_manifest()
    assert m.warmup_bars == 20


def test_instruments_is_the_sorted_union_of_subscriptions_and_explicit_universe() -> None:
    other = InstrumentId("BANKNIFTY", "NSE")
    m = sample(universe=Universe.of_explicit([other, NIFTY]))
    assert m.instruments() == [other, NIFTY]


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"name": " "}, "empty_name"),
        ({"source_hash": ""}, "empty_source_hash"),
        ({"api_version": "99.0.0"}, "unsupported_api_version"),
        (
            {"driving_timeframe": TimeframeSpec(interval=0, aggregation=BarAggregation.DAY)},
            "zero_interval",
        ),
        (
            {"universe": Universe.of_named("NIFTY50"), "subscriptions": Subscriptions.of([])},
            "named_universe_unresolved",
        ),
    ],
)
def test_validate_rejects_with_the_rust_error_code(changes: dict, code: str) -> None:
    with pytest.raises(ManifestError) as err:
        sample(**changes).validate_manifest()
    assert err.value.code == code


def test_json_roundtrip_matches_the_rust_shape() -> None:
    m = sample()
    data = m.to_json_dict()
    assert data["universe"] == {"explicit": [{"symbol": "NIFTY50", "exchange": "NSE"}]}
    assert data["driving_timeframe"] == {"interval": 1, "aggregation": "day"}
    assert StrategyManifest.model_validate(data) == m


def test_empty_schedules_are_omitted_like_rust() -> None:
    assert "schedules" not in sample(schedules={}).to_json_dict()
