"""A halted trading state blocks orders in a run started from Python (ADR 0018 decisions 7, 10).

``run_strategy(..., risk=...)`` puts the Rust ``RiskStage`` in front of the run's execution.
The risk spec carries the limits, seed positions, the initial trading state and scheduled state
changes (event time), so a Python caller can halt and resume a run deterministically.
"""

from __future__ import annotations

import pytest
from _risk_run import bar, order, run

_honba = pytest.importorskip("honba._honba")

BARS = [bar(100.0, 1_000), bar(100.0, 2_000), bar(100.0, 3_000), bar(100.0, 4_000)]
BUYS = [order(i, "buy", 1.0) for i in range(4)]


def test_halt_refuses_orders_until_resume() -> None:
    risk = {
        "limits": {},
        "state_changes": [
            {"ts_init": 2_000, "state": "halted"},
            {"ts_init": 4_000, "state": "active"},
        ],
    }
    out = run(_honba, BUYS, BARS, risk)

    reasons = [r["reason"] for r in out["order_rejections"]]
    assert reasons == ["risk_trading_halted", "risk_trading_halted"]
    assert [r["code"] for r in out["risk_refusals"]] == ["risk_trading_halted"] * 2
    assert [r["context"] for r in out["risk_refusals"]] == [{"rule": "trading_halted"}] * 2
    # the venue only ever saw the two orders placed while trading
    assert len(out["fills"]) == 2
    assert out["positions"] == [
        {"instrument_id": {"symbol": "X", "exchange": "NSE"}, "quantity": 2.0}
    ]


def test_a_run_started_halted_trades_nothing() -> None:
    out = run(_honba, BUYS, BARS, {"limits": {}, "trading_state": "halted"})
    assert out["fills"] == []
    assert len(out["order_rejections"]) == 4
    assert out["positions"] == []


def test_without_a_halt_every_order_trades() -> None:
    out = run(_honba, BUYS, BARS, {"limits": {}})
    assert out["order_rejections"] == []
    assert out["risk_refusals"] == []
    assert len(out["fills"]) == 4


def test_limits_apply_in_the_run() -> None:
    risk = {"limits": {"order_rate": {"max_orders": 2, "window_ms": 1}}}
    # all four bars lie within 1 ms of event time
    out = run(_honba, BUYS, BARS, risk)
    assert [r["code"] for r in out["risk_refusals"]] == ["risk_order_rate_exceeded"] * 2
    assert len(out["fills"]) == 2


def test_orders_for_unlisted_instruments_are_refused_not_crashed() -> None:
    other = {
        "bar": 0,
        "instrument_id": {"symbol": "Z", "exchange": "NSE"},
        "side": "buy",
        "quantity": 1.0,
    }
    out = run(_honba, [other], BARS, {"limits": {}})
    assert [r["code"] for r in out["risk_refusals"]] == ["risk_instrument_unknown"]


@pytest.mark.parametrize(
    "risk",
    [
        {"limits": {"max_notional": -1.0}},
        {"limits": {"bogus": 1}},
        {"limits": {}, "trading_state": "paused"},
        {"limits": {}, "state_changes": [{"ts_init": 1, "state": "paused"}]},
        {"limits": {}, "positions": [{"instrument_id": {"symbol": "X", "exchange": "NSE"}}]},
        {"limits": {}, "surprise": True},
    ],
)
def test_invalid_risk_spec_is_a_value_error(risk: dict) -> None:
    with pytest.raises(ValueError, match="risk"):
        run(_honba, BUYS, BARS, risk)


def test_risk_accepts_a_json_string_and_python_objects() -> None:
    import json

    spec = {"limits": {}, "trading_state": "halted"}
    as_text = run(_honba, BUYS, BARS, json.dumps(spec))
    as_dict = run(_honba, BUYS, BARS, spec)
    assert as_text == as_dict
    limits = _honba.RiskLimits(order_rate=(1, 1000))
    with_obj = run(
        _honba, BUYS, BARS, {"limits": limits, "trading_state": _honba.TradingState.HALTED}
    )
    assert len(with_obj["order_rejections"]) == 4
