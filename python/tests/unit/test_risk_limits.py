"""``honba._honba.RiskLimits`` and ``honba.risk`` (ADR 0018 decisions 8 and 10)."""

from __future__ import annotations

import math

import pytest

_honba = pytest.importorskip("honba._honba")
RiskLimits = _honba.RiskLimits


def test_default_has_no_limits() -> None:
    limits = RiskLimits()
    assert limits.max_notional is None
    assert limits.order_rate is None


def test_constructor_keeps_the_values() -> None:
    limits = RiskLimits(max_notional=500_000.0, order_rate=(30, 1000))
    assert limits.max_notional == 500_000.0
    assert limits.order_rate == (30, 1000)


def test_from_dict_parses_the_toml_table_shape() -> None:
    parsed = RiskLimits.from_dict(
        {"max_notional": 500000.0, "order_rate": {"max_orders": 30, "window_ms": 1000}}
    )
    assert parsed == RiskLimits(max_notional=500_000.0, order_rate=(30, 1000))


def test_from_dict_empty_is_the_default() -> None:
    assert RiskLimits.from_dict({}) == RiskLimits()


def test_to_dict_round_trips_through_from_dict() -> None:
    limits = RiskLimits(max_notional=1.5, order_rate=(2, 10))
    assert limits.to_dict() == {
        "max_notional": 1.5,
        "order_rate": {"max_orders": 2, "window_ms": 10},
        "max_participation": None,
    }
    assert RiskLimits.from_dict(limits.to_dict()) == limits
    assert RiskLimits().to_dict() == {
        "max_notional": None,
        "order_rate": None,
        "max_participation": None,
    }


@pytest.mark.parametrize(
    "table",
    [
        {"max_notionl": 1.0},
        {"order_rate": {"max_orders": 1, "window_ms": 1, "burst": 3}},
        {"order_rate": {"max_orders": 1}},
        {"max_notional": "lots"},
        {"order_rate": [1, 2]},
    ],
)
def test_from_dict_refuses_unknown_or_malformed_keys(table: dict) -> None:
    with pytest.raises(ValueError, match="risk"):
        RiskLimits.from_dict(table)


@pytest.mark.parametrize("bad", [0.0, -1.0, math.nan, math.inf, -math.inf])
def test_invalid_max_notional_refused_everywhere(bad: float) -> None:
    with pytest.raises(ValueError, match="max_notional"):
        RiskLimits(max_notional=bad)
    if math.isfinite(bad):  # JSON has no NaN/inf spelling to parse
        with pytest.raises(ValueError, match="max_notional"):
            RiskLimits.from_dict({"max_notional": bad})


@pytest.mark.parametrize("bad", [(0, 1000), (1, 0), (0, 0)])
def test_invalid_order_rate_refused_everywhere(bad: tuple[int, int]) -> None:
    with pytest.raises(ValueError, match="order_rate"):
        RiskLimits(order_rate=bad)
    with pytest.raises(ValueError, match="order_rate"):
        RiskLimits.from_dict({"order_rate": {"max_orders": bad[0], "window_ms": bad[1]}})


def test_order_rate_negative_is_refused() -> None:
    with pytest.raises((ValueError, OverflowError)):
        RiskLimits(order_rate=(-1, 1000))


def test_require_live_needs_both_limits() -> None:
    both = RiskLimits(max_notional=1000.0, order_rate=(5, 1000))
    assert both.require_live() is None
    for partial in (
        RiskLimits(),
        RiskLimits(max_notional=1000.0),
        RiskLimits(order_rate=(5, 1000)),
    ):
        with pytest.raises(ValueError, match="live run"):
            partial.require_live()


def test_repr_shows_the_limits() -> None:
    assert "500000" in repr(RiskLimits(max_notional=500_000.0))


# ---- honba.risk: thin re-export module and [risk] loader -------------------------------


def test_risk_module_reexports_the_native_types() -> None:
    from honba import risk

    assert risk.RiskLimits is _honba.RiskLimits
    assert risk.RiskStage is _honba.RiskStage
    assert risk.TradingState is _honba.TradingState
    assert risk.RiskDecision is _honba.RiskDecision
    assert {"RiskLimits", "RiskStage", "TradingState", "RiskDecision"} <= set(risk.__all__)


def test_limits_from_config_reads_the_risk_table() -> None:
    from honba.risk import limits_from_config

    config = {
        "account": {"currency": "INR"},
        "risk": {"max_notional": 250000.0, "order_rate": {"max_orders": 10, "window_ms": 500}},
    }
    assert limits_from_config(config) == RiskLimits(max_notional=250_000.0, order_rate=(10, 500))


def test_limits_from_config_without_risk_section_is_the_default() -> None:
    from honba.risk import limits_from_config

    assert limits_from_config({"account": {}}) == RiskLimits()


def test_limits_from_config_refuses_unknown_key_in_risk_table() -> None:
    from honba.risk import limits_from_config

    with pytest.raises(ValueError, match="risk"):
        limits_from_config({"risk": {"max_loss": 1.0}})
