"""``honba.risk.check_state``: the pure state rules (Halted, reduce-only), ADR 0018 rules 1-2.

The Python twin of ``honba_risk::check_state``; the golden vectors pin it to Rust
(``tests/integration/test_risk_conformance.py``).
"""

from __future__ import annotations

import pytest

from honba.entities.order import OrderSide
from honba.risk import RiskRefusal, TradingState, check_state

pytest.importorskip("honba._honba")

B, S = OrderSide.BUY, OrderSide.SELL


def test_active_passes_everything() -> None:
    assert check_state(B, 1e9, 0.0, TradingState.ACTIVE) is None


def test_halted_refuses_with_its_code() -> None:
    got = check_state(S, 1.0, 10.0, TradingState.HALTED)
    assert got == RiskRefusal("risk_trading_halted", "trading_halted", {"rule": "trading_halted"})


@pytest.mark.parametrize(
    ("side", "qty", "pos", "ok"),
    [
        (S, 10.0, 10.0, True),  # closes the long exactly
        (S, 10.5, 10.0, False),  # crosses zero
        (B, 1.0, 10.0, False),  # adds
        (B, 4.0, -4.0, True),  # closes the short
        (S, 1.0, -4.0, False),
        (S, 1.0, 0.0, False),  # flat refuses all
        (B, 1.0, 0.0, False),
    ],
)
def test_reducing_passes_only_orders_that_stay_between_zero_and_the_position(
    side: OrderSide, qty: float, pos: float, ok: bool
) -> None:
    got = check_state(side, qty, pos, TradingState.REDUCING)
    assert (got is None) is ok
    if not ok:
        assert got is not None
        assert got.code == "risk_reduce_only_violation"
        assert got.context == {
            "rule": "reduce_only",
            "position": pos,
            "side": side.name.lower(),
            "quantity": qty,
        }
