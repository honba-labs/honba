"""Verify a manifest through the Rust compiler (docs/archive/plan.md E0-S8).

``honba.strategies.verify.verify_manifest`` and ``honba verify`` call
``honba._honba.verify_manifest``; the IR they return is the Rust ``StrategyIr``.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from honba.cli.main import app
from honba.entities.instrument import InstrumentId
from honba.strategies.manifest import StrategyManifest, Subscriptions, TimeframeSpec, Universe
from honba.strategies.verify import VerifyError, verify_manifest
from honba.wire.wire import BarAggregation

_honba = pytest.importorskip("honba._honba")

NIFTY = InstrumentId("NIFTY50", "NSE")


def manifest(**changes: object) -> StrategyManifest:
    m = StrategyManifest.build(
        "sma_crossover",
        "sha256:abc123",
        Universe.of_explicit([NIFTY]),
        TimeframeSpec(interval=1, aggregation=BarAggregation.DAY),
        subscriptions=Subscriptions.of([NIFTY], quotes=True),
        warmup_bars=20,
    )
    return m.model_copy(update=changes)


def test_a_python_manifest_compiles_to_the_rust_ir() -> None:
    m = manifest()
    ir = verify_manifest(m)
    assert ir["schema_version"] == _honba.SCHEMA_VERSION
    assert ir["strategy_api_version"] == _honba.STRATEGY_API_VERSION
    assert ir["manifest"] == m.to_json_dict()
    assert ir["warmup_bars"] == m.warmup_bars == 20
    nifty = [{"symbol": "NIFTY50", "exchange": "NSE"}]
    assert ir["universe"] == {"named": None, "instruments": nifty}
    assert ir["subscriptions"] == {"bars": nifty, "quotes": nifty, "trades": []}
    assert ir["timeframes"] == [ir["driving_timeframe"]] == [{"interval": 1, "aggregation": "day"}]


def test_dicts_and_json_text_are_accepted_too() -> None:
    m = manifest()
    assert verify_manifest(m.to_json_dict()) == verify_manifest(json.dumps(m.to_json_dict()))


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"name": " "}, "empty_name"),
        ({"subscriptions": Subscriptions(instruments=[])}, "no_subscriptions"),
        (
            {"subscriptions": Subscriptions.of([NIFTY, InstrumentId("TCS", "NSE")])},
            "subscription_outside_universe",
        ),
    ],
)
def test_rejections_carry_the_rust_error_code(changes: dict, code: str) -> None:
    with pytest.raises(VerifyError) as err:
        verify_manifest(manifest(**changes))
    assert err.value.code == code


def test_an_unknown_manifest_field_is_a_deserialize_error() -> None:
    data = {**manifest().to_json_dict(), "warmup_barz": 3}
    with pytest.raises(VerifyError) as err:
        verify_manifest(data)
    assert err.value.code == "deserialize"


def test_cli_verify_prints_the_ir_json(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest().to_json_dict()))
    result = CliRunner().invoke(app, ["verify", str(path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == verify_manifest(manifest())


def test_cli_verify_fails_with_the_code(tmp_path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest(name="").to_json_dict()))
    result = CliRunner().invoke(app, ["verify", str(path)])
    assert result.exit_code == 1
    assert "empty_name" in result.output
