"""Volume participation cap in RiskStage (Balch pitfall #5: capacity delusions).

The notional and order-rate rules exist; the participation rule caps an order at
a fraction of its average daily volume (``max_participation * ADV``). The ADV
travels in the request itself, so the stage never queries a store; a missing or
non-positive ADV fails closed instead of guessing.
"""

from __future__ import annotations

import pytest

from honba._honba import RiskLimits, RiskStage

INSTRUMENT: dict = {
    "instrument_id": {"symbol": "X", "exchange": "NSE"},
    "kind": "equity",
    "currency": "INR",
    "lot_size": 1.0,
    "tick_size": 0.05,
}

REQUEST: dict = {
    "order_id": "A",
    "instrument_id": "X.NSE",
    "side": "buy",
    "quantity": 100.0,
    "price": 100.0,
    "position": 0.0,
    "trading_state": "active",
    "ts": 1,
}


def stage(limits: RiskLimits, **overrides) -> RiskStage:
    instrument = {**INSTRUMENT, **overrides}
    return RiskStage(limits, "INR", "null", [instrument])


def with_adv(adv: float | None, **overrides) -> dict:
    req = dict(REQUEST)
    if adv is None:
        req.pop("adv", None)
    else:
        req["adv"] = adv
    req.update(overrides)
    return req


def test_max_participation_accepts_fractions_up_to_one() -> None:
    assert RiskLimits(max_participation=0.025).max_participation == 0.025
    assert RiskLimits(max_participation=1.0).max_participation == 1.0
    assert RiskLimits().max_participation is None


@pytest.mark.parametrize("value", [0.0, -0.01, 1.01, 2.0])
def test_max_participation_rejects_non_fractions(value: float) -> None:
    with pytest.raises(ValueError, match="max_participation"):
        RiskLimits(max_participation=value)


def test_max_participation_rejects_nan() -> None:
    with pytest.raises(ValueError, match="max_participation"):
        RiskLimits(max_participation=float("nan"))


def test_order_over_the_participation_cap_is_refused() -> None:
    s = stage(RiskLimits(max_participation=0.025))
    d = s.check(with_adv(1_000.0, quantity=100.0))  # 100 > 2.5% of 1000 = 25
    assert not d.approved
    assert d.code == "risk_max_participation_exceeded"
    assert d.rule == "max_participation"
    assert d.context == {
        "rule": "max_participation",
        "quantity": 100.0,
        "max_quantity": 25.0,
        "adv": 1_000.0,
        "participation": 0.025,
    }


def test_order_exactly_at_the_participation_cap_is_approved() -> None:
    s = stage(RiskLimits(max_participation=0.025))
    assert s.check(with_adv(1_000.0, quantity=25.0)).approved


def test_missing_adv_fails_closed() -> None:
    s = stage(RiskLimits(max_participation=0.025))
    d = s.check(with_adv(None, quantity=1.0))
    assert not d.approved
    assert d.code == "risk_max_participation_exceeded"
    assert d.context["reason"] == "missing_adv"


@pytest.mark.parametrize("adv", [0.0, -1.0])
def test_non_positive_adv_fails_closed(adv: float) -> None:
    s = stage(RiskLimits(max_participation=0.025))
    d = s.check(with_adv(adv, quantity=1.0))
    assert not d.approved
    assert d.code == "risk_max_participation_exceeded"
    assert d.context["reason"] == "non_positive_adv"


def test_rule_is_off_when_no_limit_is_set() -> None:
    s = stage(RiskLimits())
    assert s.check(with_adv(1_000.0, quantity=10_000.0)).approved
    assert s.check(with_adv(None, quantity=10_000.0)).approved


def test_participation_refusal_wins_over_notional() -> None:
    # Can the market absorb this? is asked before the account-value cap.
    s = stage(RiskLimits(max_notional=100_000.0, max_participation=0.01))
    d = s.check(with_adv(1_000.0, quantity=100.0))  # 100 > 1% of 1000 = 10
    assert d.code == "risk_max_participation_exceeded"


def test_participation_applies_to_sells_too() -> None:
    s = stage(RiskLimits(max_participation=0.01))
    s_free = stage(RiskLimits(max_participation=0.01))
    s_free.positions if False else None
    d = s.check(with_adv(500.0, quantity=10.0, side="sell", order_id="S"))  # 10 > 5
    assert d.code == "risk_max_participation_exceeded"


def test_max_participation_round_trips_through_dict_forms() -> None:
    limits = RiskLimits(max_participation=0.01)
    assert limits.to_dict() == {
        "max_notional": None,
        "order_rate": None,
        "max_participation": 0.01,
    }
    assert RiskLimits.from_dict(limits.to_dict()) == limits
    assert RiskLimits.from_dict({"max_participation": 0.02}).max_participation == 0.02


@pytest.mark.parametrize(
    "table",
    [
        {"max_participation": 0.0},
        {"max_participation": 1.5},
        {"max_participation": "lots"},
        {"max_participation": None, "burst": 3},
    ],
)
def test_from_dict_refuses_bad_max_participation(table: dict) -> None:
    with pytest.raises(ValueError, match="risk|max_participation"):
        RiskLimits.from_dict(table)
