"""``honba._honba.TradingState``: the engine's trading state, wire spelling on ``str()``
(ADR 0018 decisions 2 and 10)."""

from __future__ import annotations

import pytest

_honba = pytest.importorskip("honba._honba")


def test_has_exactly_the_three_states() -> None:
    ts = _honba.TradingState
    names = {n for n in dir(ts) if n.isupper()}
    assert names == {"ACTIVE", "REDUCING", "HALTED"}


@pytest.mark.parametrize(
    ("member", "wire"),
    [("ACTIVE", "active"), ("REDUCING", "reducing"), ("HALTED", "halted")],
)
def test_str_is_the_wire_spelling(member: str, wire: str) -> None:
    assert str(getattr(_honba.TradingState, member)) == wire


def test_members_compare_and_hash_by_identity_of_state() -> None:
    ts = _honba.TradingState
    assert ts.ACTIVE == ts.ACTIVE
    assert ts.ACTIVE != ts.HALTED
    assert len({ts.ACTIVE, ts.ACTIVE, ts.REDUCING, ts.HALTED}) == 3


def test_a_member_is_accepted_where_a_state_is_expected() -> None:
    stage = _honba.RiskStage(
        _honba.RiskLimits(),
        "INR",
        "null",
        [
            {
                "instrument_id": {"symbol": "X", "exchange": "NSE"},
                "kind": "equity",
                "currency": "INR",
                "lot_size": 1.0,
                "tick_size": 0.05,
            }
        ],
    )
    decision = stage.check(
        {
            "order_id": "A",
            "instrument_id": "X.NSE",
            "side": "buy",
            "quantity": 1.0,
            "price": 100.0,
            "position": 0.0,
            "trading_state": _honba.TradingState.HALTED,
            "ts": 1,
        }
    )
    assert not decision.approved
    assert decision.code == "risk_trading_halted"
