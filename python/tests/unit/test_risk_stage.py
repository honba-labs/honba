"""``honba._honba.RiskStage`` construction and request validation (ADR 0018 decision 10)."""

from __future__ import annotations

from typing import Any

import pytest

_honba = pytest.importorskip("honba._honba")

INSTRUMENT: dict[str, Any] = {
    "instrument_id": {"symbol": "X", "exchange": "NSE"},
    "kind": "equity",
    "currency": "INR",
    "lot_size": 1.0,
    "tick_size": 0.05,
}
REQUEST: dict[str, Any] = {
    "order_id": "A",
    "instrument_id": "X.NSE",
    "side": "buy",
    "quantity": 1.0,
    "price": 100.0,
    "position": 0.0,
    "trading_state": "active",
    "ts": 1,
}


def stage(limits: Any = None, **overrides: Any) -> Any:
    instrument = {**INSTRUMENT, **overrides}
    return _honba.RiskStage(limits or _honba.RiskLimits(), "INR", "null", [instrument])


def test_approves_a_plain_order() -> None:
    d = stage().check(REQUEST)
    assert d.approved
    assert (d.code, d.rule, d.context) == (None, None, {})


def test_instrument_override_adds_freeze_and_band() -> None:
    s = stage(max_order_quantity=10.0, band={"lower": 90.0, "upper": 110.0})
    over = s.check({**REQUEST, "quantity": 11.0})
    assert over.code == "risk_quantity_over_freeze"
    out = s.check({**REQUEST, "price": 111.0})
    assert out.code == "risk_price_band_exceeded"


def test_rate_limit_has_memory_across_checks() -> None:
    s = stage(_honba.RiskLimits(order_rate=(1, 1000)))
    assert s.check(REQUEST).approved
    second = s.check({**REQUEST, "order_id": "B", "ts": 2})
    assert second.code == "risk_order_rate_exceeded"
    assert second.context == {"rule": "order_rate", "count": 1, "max_orders": 1, "window_ms": 1000}


def test_unknown_instrument_is_refused_not_an_error() -> None:
    d = stage().check({**REQUEST, "instrument_id": "Z.NSE"})
    assert d.code == "risk_instrument_unknown"


@pytest.mark.parametrize(
    "patch",
    [
        {"side": "hold"},
        {"trading_state": "paused"},
        {"instrument_id": "NOEXCHANGE"},
        {"ts": -1},
        {"ts": 1.5},
        {"quantity": "many"},
        {"order_id": None},
    ],
)
def test_malformed_request_raises_value_error(patch: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="risk request"):
        stage().check({**REQUEST, **patch})


def test_missing_required_key_raises_value_error() -> None:
    req = {k: v for k, v in REQUEST.items() if k != "position"}
    with pytest.raises(ValueError, match="position"):
        stage().check(req)


def test_unknown_request_key_is_refused() -> None:
    with pytest.raises(ValueError, match="risk request"):
        stage().check({**REQUEST, "leverage": 5})


def test_construction_errors() -> None:
    with pytest.raises(ValueError, match="market"):
        _honba.RiskStage(_honba.RiskLimits(), "INR", "mars", [INSTRUMENT])
    with pytest.raises(ValueError, match="currency"):
        _honba.RiskStage(_honba.RiskLimits(), "XXX", "null", [INSTRUMENT])
    with pytest.raises(ValueError, match="lot_size"):
        _honba.RiskStage(_honba.RiskLimits(), "INR", "null", [{**INSTRUMENT, "lot_size": 0.0}])
    with pytest.raises(ValueError, match="band"):
        _honba.RiskStage(
            _honba.RiskLimits(), "INR", "null", [{**INSTRUMENT, "band": {"lower": 1.0}}]
        )
