"""Tests for the market-participation risk gate."""

from __future__ import annotations

import datetime as dt

from honba.engine.event import ExecutionEvent, OrderIntentEvent
from honba.engine.risk import RiskLimits, RiskStage
from honba.engine.state import EngineState


def stage(limits: RiskLimits) -> RiskStage:
    s = RiskStage(limits)
    s.bind(EngineState())
    return s


def with_adv(
    adv: float | None,
    *,
    quantity: float = 10.0,
    side: str = "buy",
    order_id: str = "O1",
) -> OrderIntentEvent:
    ctx = {"adv": adv} if adv is not None else {}
    return OrderIntentEvent(
        ts=dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc),
        intent_id=order_id,
        symbol="INFY",
        exchange="NSE",
        side=side,
        quantity=quantity,
        limit_price=100.0,
        context=ctx,
    )


def test_participation_limit_accepts_when_under_share_of_adv() -> None:
    s = stage(RiskLimits(max_participation=0.05))
    d = s.check(with_adv(1000.0, quantity=40.0))  # 40 / 1000 = 4% <= 5%
    assert d.code == "accepted"


def test_participation_limit_rejects_when_over_share_of_adv() -> None:
    s = stage(RiskLimits(max_participation=0.05))
    d = s.check(with_adv(1000.0, quantity=60.0))  # 60 / 1000 = 6% > 5%
    assert d.code == "risk_max_participation_exceeded"
    assert "0.0600 > 0.0500" in d.reason


def test_participation_limit_rounds_at_boundary() -> None:
    s = stage(RiskLimits(max_participation=0.05))
    d = s.check(with_adv(1000.0, quantity=50.0))  # 5% exactly
    assert d.code == "accepted"


def test_missing_adv_causes_rejection_not_pass() -> None:
    s = stage(RiskLimits(max_participation=0.05))
    d = s.check(with_adv(None, quantity=10.0))
    assert d.code == "risk_missing_adv"
    assert "adv" in d.reason.lower()


def test_zero_or_negative_adv_causes_rejection() -> None:
    s = stage(RiskLimits(max_participation=0.05))
    for bad_adv in (0.0, -100.0):
        d = s.check(with_adv(bad_adv, quantity=10.0))
        assert d.code == "risk_invalid_adv", f"failed for {bad_adv}"


def test_unbounded_when_limit_is_none() -> None:
    s = stage(RiskLimits(max_participation=None))
    d = s.check(with_adv(10.0, quantity=1000.0))
    assert d.code == "accepted"


def test_participation_accumulates_prior_same_symbol_intents() -> None:
    s = stage(RiskLimits(max_participation=0.10))
    # First intent: 50 / 1000 = 5%
    d1 = s.check(with_adv(1000.0, quantity=50.0, order_id="O1"))
    assert d1.code == "accepted"
    # Second intent: (50 + 60) / 1000 = 11% > 10%
    d2 = s.check(with_adv(1000.0, quantity=60.0, order_id="O2"))
    assert d2.code == "risk_max_participation_exceeded"


def test_participation_counts_resting_orders_not_filled_intents_twice() -> None:
    s = stage(RiskLimits(max_participation=0.10))
    i1 = with_adv(1000.0, quantity=50.0, order_id="O1")
    s.check(i1)
    # Fill event arrives: O1 filled for 50
    s.on_execution(
        ExecutionEvent(
            ts=dt.datetime(2025, 1, 1, tzinfo=dt.timezone.utc),
            order_id="O1",
            symbol="INFY",
            exchange="NSE",
            side="buy",
            fill_quantity=50.0,
            fill_price=100.0,
        )
    )
    # Total volume traded on INFY is now 50. New intent for 40: 50 + 40 = 90 / 1000 = 9% <= 10%
    d2 = s.check(with_adv(1000.0, quantity=40.0, order_id="O2"))
    assert d2.code == "accepted"
    # But a third for 20: 90 + 20 = 110 / 1000 = 11% > 10%
    d3 = s.check(with_adv(1000.0, quantity=20.0, order_id="O3"))
    assert d3.code == "risk_max_participation_exceeded"


def test_participation_applies_to_sells_too() -> None:
    s = stage(RiskLimits(max_participation=0.01))
    d = s.check(with_adv(500.0, quantity=10.0, side="sell", order_id="S"))  # 10 > 5
    assert d.code == "risk_max_participation_exceeded"


def test_max_participation_round_trips_through_dict_forms() -> None:
    limits = RiskLimits(max_participation=0.01)
    assert limits.to_dict() == {
        "max_notional": None,
        "order_rate": None,
        "max_participation": 0.01,
        "max_short_shares": None,
        "max_drawdown": None,
    }
    restored = RiskLimits.from_dict(limits.to_dict())
    assert restored.max_participation == 0.01
    assert restored == limits
