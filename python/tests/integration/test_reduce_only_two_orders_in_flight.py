"""Reduce-only counts same-side working orders (ADR 0018 decision 4, ADR 0019 decision 5).

Long 100, state `reducing`: the first sell 60 passes; the second sell 50, submitted in the same
event while the first is still working, would cross flat (100 - 60 - 50 < 0), so it is refused
with the *effective* position 40, not the booked 100.
"""

from __future__ import annotations

import pytest
from _risk_run import X, bar, order, run

_honba = pytest.importorskip("honba._honba")

RISK = {
    "limits": {},
    "trading_state": "reducing",
    "positions": [{"instrument_id": X, "quantity": 100.0}],
}


def test_second_sell_in_the_same_event_is_refused() -> None:
    out = run(_honba, [order(0, "sell", 60.0), order(0, "sell", 50.0)], [bar(100.0, 1_000)], RISK)
    assert len(out["risk_refusals"]) == 1
    refusal = out["risk_refusals"][0]
    assert refusal["code"] == "risk_reduce_only_violation"
    assert refusal["context"] == {
        "rule": "reduce_only",
        "position": 40.0,
        "side": "sell",
        "quantity": 50.0,
    }
    assert [r["reason"] for r in out["order_rejections"]] == ["risk_reduce_only_violation"]
    assert out["positions"] == [{"instrument_id": X, "quantity": 40.0}]


def test_the_remainder_can_be_sold_later_and_flat_refuses_everything() -> None:
    orders = [
        order(0, "sell", 60.0),
        order(1, "sell", 40.0),  # exactly flat: allowed
        order(2, "sell", 1.0),  # flat: reduce-only refuses all
        order(2, "buy", 1.0),
    ]
    bars = [bar(100.0, 1_000), bar(100.0, 2_000), bar(100.0, 3_000)]
    out = run(_honba, orders, bars, RISK)
    assert [r["code"] for r in out["risk_refusals"]] == ["risk_reduce_only_violation"] * 2
    assert out["positions"] == []


def test_without_reducing_state_the_same_orders_pass() -> None:
    risk = {**RISK, "trading_state": "active"}
    out = run(_honba, [order(0, "sell", 60.0), order(0, "sell", 50.0)], [bar(100.0, 1_000)], risk)
    assert out["risk_refusals"] == []
