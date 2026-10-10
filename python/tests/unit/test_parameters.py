"""Unit tests for parameter range to schema and Parameter descriptor (E4-S2)."""

from __future__ import annotations

import pytest

from honba.strategies.base import Parameter, Strategy
from honba.strategies.manifest import ManifestError, ParameterSpec, param_range_to_schema


def test_param_range_to_schema_from_string() -> None:
    spec = param_range_to_schema("10:50:5", default=20, description="fast period")
    assert isinstance(spec, ParameterSpec)
    assert spec.range == "10:50:5"
    assert spec.min == 10.0
    assert spec.max == 50.0
    assert spec.step == 5.0
    assert spec.default == 20
    assert spec.description == "fast period"
    assert spec.discrete_values() == [10, 15, 20, 25, 30, 35, 40, 45, 50]


def test_param_range_to_schema_from_tuple() -> None:
    spec = param_range_to_schema((1.0, 5.0, 1.0), default=3.0)
    assert spec.min == 1.0
    assert spec.max == 5.0
    assert spec.step == 1.0
    assert spec.default == 3.0
    assert spec.discrete_values() == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_param_range_to_schema_from_parameter_instance() -> None:
    param = Parameter(default=25, min=10, max=50, step=5, description="test param")
    spec = param_range_to_schema(param)
    assert spec.default == 25
    assert spec.min == 10.0
    assert spec.max == 50.0
    assert spec.step == 5.0
    assert spec.description == "test param"


def test_param_range_to_schema_rejects_default_out_of_range() -> None:
    with pytest.raises(ManifestError) as exc_info:
        param_range_to_schema("10:50:5", default=5)
    assert exc_info.value.code == "parameter_default_out_of_range"

    with pytest.raises(ManifestError) as exc_info:
        param_range_to_schema("10:50:5", default=60)
    assert exc_info.value.code == "parameter_default_out_of_range"


def test_param_range_to_schema_rejects_invalid_bounds() -> None:
    with pytest.raises(ManifestError) as exc_info:
        param_range_to_schema("50:10:5", default=20)
    assert exc_info.value.code == "invalid_parameter_range"

    with pytest.raises(ManifestError) as exc_info:
        param_range_to_schema("10:50:-2", default=20)
    assert exc_info.value.code == "invalid_parameter_range"

    with pytest.raises(ManifestError) as exc_info:
        param_range_to_schema("10:50", default=20)
    assert exc_info.value.code == "invalid_parameter_range"


class MockStrategy(Strategy):
    name = "mock_strategy"
    fast = Parameter(default=10, min=5, max=30, step=5, description="fast window")
    slow = Parameter(default=50, min=20, max=100, step=10, description="slow window")


def test_parameter_descriptor_and_hyperparameters() -> None:
    params = MockStrategy.hyperparameters()
    assert set(params.keys()) == {"fast", "slow"}
    assert params["fast"].default == 10
    assert params["slow"].default == 50

    # Instance without override gets default
    instance = MockStrategy()
    assert instance.fast == 10
    assert instance.slow == 50

    # Instance with params gets override
    instance_with_config = MockStrategy(params={"fast": 15, "slow": 60})
    assert instance_with_config.fast == 15
    assert instance_with_config.slow == 60
