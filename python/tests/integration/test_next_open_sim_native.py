"""Replays ``schema/conformance/next_open_sim.json`` (chunks 1 and 2) through ``honba._honba``.

``NextOpenSimulator`` is the PyO3 binding of the Rust ``NextOpenSim`` (ADR 0016, chunk 3a). Every
scenario must end with the expectations committed from the Python reference
(``NextOpenExecution``), so the native simulator equals the reference. Also checks the cost
adapter against the Python India fill-cost functions and the error mapping.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from honba.entities.order import OrderSide
from honba.markets.india.costs import (
    nse_equity_delivery_fill_cost,
    nse_equity_intraday_fill_cost,
)

_honba = pytest.importorskip("honba._honba")
if not hasattr(_honba, "NextOpenSimulator"):  # pragma: no cover - stale extension
    pytest.fail("honba._honba lacks NextOpenSimulator: rebuild the extension")

ROOT = Path(__file__).resolve().parents[3]
DOC = json.loads((ROOT / "schema" / "conformance" / "next_open_sim.json").read_text("utf-8"))
SCENARIOS = [s for s in DOC["scenarios"] if s["chunk"] in (1, 2)]


def _vector_costs(spec: dict[str, int]) -> Any:
    """The vectors' parametric model, as a callable returning minor units."""

    def cost(side: str, quantity: float, price: float) -> int:
        notional = round_half_away(quantity * price * 100)
        tag = "buy" if side == "buy" else "sell"
        return spec.get(f"flat_{tag}", 0) + (notional * spec.get(f"bps_{tag}", 0) + 5000) // 10000

    return cost


def round_half_away(x: float) -> int:
    whole = int(x)
    if abs(x - whole) >= 0.5:
        return whole + (1 if x > 0 else -1)
    return whole


def _build(config: dict[str, Any]) -> Any:
    return _honba.NextOpenSimulator(
        config["cash"],
        config["currency"],
        settlement_days=config["settlement_days"],
        long_only=config["long_only"],
        lot_sizes=config["lot_sizes"],
        costs=_vector_costs(config["costs"]) if "costs" in config else None,
    )


def _bar(step: dict[str, Any]) -> dict[str, Any]:
    keys = ("symbol", "ts", "open", "high", "low", "close", "volume")
    return {k: step[k] for k in keys if k in step}


def _order(step: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in step.items() if k != "op"}


def _replay(scenario: dict[str, Any]) -> dict[str, Any]:
    sim = _build(scenario["config"])
    drains: list[dict[str, Any]] = []
    probes: list[dict[str, int]] = []
    for step in scenario["steps"]:
        op = step["op"]
        try:
            if op == "bar":
                sim.on_bar(_bar(step))
            elif op == "open_session":
                sim.open_session(step["ts"], [_bar(b) for b in step["bars"]])
            elif op == "session_open":
                sim.on_session_open(step["ts"], [_bar(b) for b in step["bars"]])
            elif op == "set_settlement_days":
                sim.set_settlement_days(step["days"])
            elif op == "probe":
                probes.append({"unsettled": sim.unsettled, "available_cash": sim.available_cash})
            elif op == "submit":
                sim.submit(_order(step))
            elif op == "cancel":
                sim.cancel(step["id"], step["now"])
            elif op == "drain":
                drains.append(
                    {
                        "fills": [
                            [
                                f["order_id"],
                                f["symbol"],
                                f["side"],
                                f["quantity"],
                                f["price"],
                                f["ts"],
                                f["costs"],
                            ]
                            for f in sim.drain_fills()
                        ],
                        "rejections": [
                            [
                                r["order_id"],
                                r["symbol"],
                                r["side"],
                                r["quantity"],
                                r["reason"],
                                r["ts"],
                            ]
                            for r in sim.drain_rejections()
                        ],
                    }
                )
            else:  # pragma: no cover
                raise AssertionError(f"unknown op {op!r}")
        except (ValueError, RuntimeError):
            assert step.get("error"), f"{scenario['name']}: unexpected error at {step}"
        else:
            assert not step.get("error"), f"{scenario['name']}: expected an error at {step}"
    final: dict[str, Any] = {
        "cash": sim.cash,
        "positions": {s: q for s, _e, q in sorted(sim.positions)},
        "working": sim.working_orders,
        "fees": sim.fees,
        "traded_notional": sim.traded_notional,
    }
    if scenario["chunk"] >= 2:
        final["unsettled"] = sim.unsettled
        final["available_cash"] = sim.available_cash
        if probes:
            final["probes"] = probes
    return {"drains": drains, "final": final}


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_native_simulator_matches_the_committed_python_reference_vectors(
    scenario: dict[str, Any],
) -> None:
    got = _replay(scenario)
    assert got["drains"] == scenario["expect"]["drains"]
    assert got["final"] == scenario["expect"]["final"]


def test_every_chunk_one_and_two_scenario_is_replayed() -> None:
    assert len(SCENARIOS) == 52


# -- error mapping ---------------------------------------------------------------------------
def _sim(**kw: Any) -> Any:
    return _honba.NextOpenSimulator(1_000_000, **kw)


def _bar_d(ts: int, open_: float = 100.0, symbol: str = "AAA") -> dict[str, Any]:
    return {"symbol": symbol, "ts": ts, "open": open_, "high": open_, "low": open_, "close": open_}


def test_rule_violations_are_value_errors_and_the_settlement_guard_a_runtime_error() -> None:
    sim = _sim()
    with pytest.raises(ValueError):
        sim.set_settlement_days(-1)
    sim.on_bar(_bar_d(5))
    with pytest.raises(ValueError, match="duplicate"):
        sim.on_bar(_bar_d(5))
    with pytest.raises(ValueError, match="non-monotonic"):
        sim.on_bar(_bar_d(4, symbol="BBB"))
    with pytest.raises(RuntimeError, match="first session"):
        sim.set_settlement_days(2)
    with pytest.raises(ValueError):
        sim.set_lot_size("AAA", 0.0)
    with pytest.raises(ValueError):
        _honba.NextOpenSimulator(-1)
    with pytest.raises(ValueError):
        _honba.NextOpenSimulator(1, "XYZ")
    with pytest.raises(ValueError, match="unknown cost pack"):
        _sim(costs="india.futures")
    with pytest.raises(TypeError):
        _sim(costs=3)


# -- python callable costs -------------------------------------------------------------------
class _Boom(Exception):
    pass


def test_a_cost_callable_exception_propagates_unchanged_and_leaves_the_order_working() -> None:
    def boom(side: str, quantity: float, price: float) -> int:
        raise _Boom("no costs today")

    sim = _sim(costs=boom)
    sim.on_bar(_bar_d(1))
    sim.submit({"id": "o", "symbol": "AAA", "side": "buy", "qty": 1, "ts": 1})
    with pytest.raises(_Boom, match="no costs today"):
        sim.on_bar(_bar_d(2))
    assert sim.working_orders == ["o"]
    assert sim.cash == 1_000_000


def test_a_negative_cost_is_a_value_error_and_a_non_int_a_type_error() -> None:
    sim = _sim(costs=lambda s, q, p: -1)
    sim.on_bar(_bar_d(1))
    sim.submit({"id": "o", "symbol": "AAA", "side": "buy", "qty": 1, "ts": 1})
    with pytest.raises(ValueError, match="negative"):
        sim.on_bar(_bar_d(2))
    assert sim.working_orders == ["o"]

    sim = _sim(costs=lambda s, q, p: "x")
    sim.on_bar(_bar_d(1))
    sim.submit({"id": "o", "symbol": "AAA", "side": "buy", "qty": 1, "ts": 1})
    with pytest.raises(TypeError):
        sim.on_bar(_bar_d(2))


def test_a_cost_callable_receives_side_quantity_and_price() -> None:
    seen: list[tuple[str, float, float]] = []

    def record(side: str, quantity: float, price: float) -> int:
        seen.append((side, quantity, price))
        return 7

    sim = _sim(costs=record)
    sim.on_bar(_bar_d(1))
    sim.submit({"id": "o", "symbol": "AAA", "side": "buy", "qty": 3, "ts": 1})
    sim.on_bar(_bar_d(2, 110.0))
    assert ("buy", 3.0, 110.0) in seen
    assert sim.fees == 7
    assert sim.drain_fills()[0]["costs"] == 7


def test_named_cost_pack_is_charged_through_the_simulator() -> None:
    sim = _honba.NextOpenSimulator(10_000_000, costs="india.equity.delivery")
    sim.on_bar(_bar_d(1, 2950.0))
    sim.submit({"id": "o", "symbol": "AAA", "side": "buy", "qty": 10, "ts": 1})
    sim.on_bar(_bar_d(2, 2950.0))
    assert sim.fees == nse_equity_delivery_fill_cost(OrderSide.BUY, 10, 2950.0).amount


# -- cost adapter parity ---------------------------------------------------------------------
QTYS = [0.001, 0.5, 1, 1.5, 3, 7, 10, 33, 100, 250, 999, 1000, 12345, 100000]
PRICES = [0.01, 0.05, 1.0, 9.99, 101.5, 2950.0, 2950.37, 19999.95, 123456.78]


@pytest.mark.parametrize(
    ("pack", "fn"),
    [
        ("india.equity.delivery", nse_equity_delivery_fill_cost),
        ("india.equity.intraday", nse_equity_intraday_fill_cost),
    ],
)
def test_rust_cost_adapter_equals_the_python_fill_cost_function_over_a_grid(
    pack: str, fn: Any
) -> None:
    mismatches = []
    for side, name in ((OrderSide.BUY, "buy"), (OrderSide.SELL, "sell")):
        for qty in QTYS:
            for price in PRICES:
                want = fn(side, qty, price).amount
                got = _honba.next_open_fill_cost(pack, name, qty, price)
                if got != want:
                    mismatches.append((name, qty, price, got, want))
    assert mismatches == []


# -- seeded holdings ---------------------------------------------------------------------------
def test_set_position_seeds_a_holding_a_sell_can_use() -> None:
    sim = _sim()
    sim.set_position("AAA", 4.0)
    sim.set_position("AAA", 2.0, "BSE")
    assert sorted(sim.positions) == [("AAA", "BSE", 2.0), ("AAA", "NSE", 4.0)]
    sim.on_bar(_bar_d(1))
    sim.submit({"id": "s", "symbol": "AAA", "side": "sell", "qty": 6, "ts": 1})
    sim.on_bar(_bar_d(2))
    assert sim.drain_fills()[0]["quantity"] == 4.0
    sim.set_position("AAA", 0.0, "BSE")
    assert sim.positions == []
    with pytest.raises(ValueError, match="finite"):
        sim.set_position("AAA", float("nan"))
