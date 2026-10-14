"""Replays ``schema/conformance/l2_sim.json`` through both ``L2Execution`` backends (ADR 0030).

The expectations are hand-derived from the ADR 0030 section 3 rules (generator:
``scripts/gen_l2_vectors.py``), so the fixture is the authority: a backend that disagrees is the
one with the bug. Three replays per scenario: the pure-Python reference and the native
simulator through the public ``L2Execution`` port (events split into the legacy fill /
rejection rows), and the raw ``honba._honba.L2Simulator`` binding with its legacy drains.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from honba.backtest.l2_sim import L2Execution
from honba.domain.money import Currency, Money
from honba.domain.tick import AggressorSide, QuoteTick, TradeTick
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderType, TimeInForce
from honba.strategies.execution import Cancelled, Expired, Fill, Rejected

ROOT = Path(__file__).resolve().parents[3]
DOC = json.loads((ROOT / "schema" / "conformance" / "l2_sim.json").read_text("utf-8"))
SCENARIOS = DOC["scenarios"]


def _native_available() -> bool:
    try:
        from honba._native import native_attr

        return hasattr(native_attr("L2Simulator"), "on_session_close")
    except (ImportError, RuntimeError):
        return False


HAVE_NATIVE = _native_available()
BACKENDS = [
    "python",
    pytest.param(
        "native", marks=pytest.mark.skipif(not HAVE_NATIVE, reason="native extension not built")
    ),
]
_TYPES = {t.value: t for t in OrderType}
_TIFS = {t.value: t for t in TimeInForce}
_AGGRESSOR = {
    "buyer": AggressorSide.BUYER,
    "seller": AggressorSide.SELLER,
    "none": AggressorSide.NO_AGGRESSOR,
}


def _iid(symbol: str) -> InstrumentId:
    return InstrumentId(symbol, "NSE")


def _cost_fn(spec: dict[str, int]) -> Any:
    """The vectors' parametric model: ``flat + (notional_minor * bps + 5000) // 10000``."""

    def cost(side: OrderSide, quantity: float, price: float) -> Money:
        tag = "buy" if side is OrderSide.BUY else "sell"
        notional = Money.mul_qty(quantity, price, Currency.INR).amount
        minor = spec.get(f"flat_{tag}", 0) + (notional * spec.get(f"bps_{tag}", 0) + 5000) // 10000
        return Money.from_minor(minor, Currency.INR)

    return cost


def _intent(step: dict[str, Any]) -> OrderIntent:
    side = OrderSide(step["side"])
    kind = _TYPES[step["type"]]
    iid = _iid(step["symbol"])
    tif = _TIFS[step.get("tif", "day")]
    if kind is OrderType.TRAILING_STOP:
        return OrderIntent(iid, side, step["qty"], kind, time_in_force=tif, trail_amount=1.0)
    return OrderIntent(
        iid,
        side,
        step["qty"],
        kind,
        price=step.get("price"),
        time_in_force=tif,
        trigger_price=step.get("trigger"),
    )


def _rows(events: list[Any]) -> dict[str, list[Any]]:
    """Split an ordered event stream into the fixture's fill / rejection rows."""
    fills: list[Any] = []
    rejections: list[Any] = []
    for ev in events:
        if isinstance(ev, Fill):
            t = ev.trade
            fills.append(
                [
                    t.order_id,
                    t.instrument_id.symbol,
                    t.side.value,
                    t.quantity,
                    t.price,
                    t.ts,
                    t.costs.amount,
                ]
            )
        elif isinstance(ev, Rejected | Cancelled | Expired):
            reason = ev.reason if isinstance(ev, Rejected) else type(ev).__name__.lower()
            rejections.append(
                [
                    ev.order_id,
                    ev.intent.instrument_id.symbol,
                    ev.intent.side.value,
                    ev.intent.quantity,
                    reason,
                    ev.ts,
                ]
            )
    return {"fills": fills, "rejections": rejections}


def _replay_port(sc: dict[str, Any], backend: str) -> dict[str, Any]:
    cfg = sc["config"]
    port = L2Execution(
        cash=Money.from_minor(cfg["cash"], Currency.INR),
        long_only=cfg["long_only"],
        lot_sizes={_iid(s): lot for s, lot in cfg["lot_sizes"].items()},
        backend=backend,  # type: ignore[arg-type]
        **({"costs": _cost_fn(cfg["costs"])} if "costs" in cfg else {}),
    )
    assert port.backend == backend
    drains: list[dict[str, Any]] = []
    for step in sc["steps"]:
        op = step["op"]
        try:
            if op == "quote":
                q = QuoteTick(
                    _iid(step["symbol"]),
                    step["ts"],
                    step["bid"],
                    step["ask"],
                    step["bid_size"],
                    step["ask_size"],
                )
                port.on_event(q, q.ts)
            elif op == "trade":
                t = TradeTick(
                    _iid(step["symbol"]),
                    step["ts"],
                    step["price"],
                    step["size"],
                    _AGGRESSOR[step.get("aggressor", "buyer")],
                    f"T-{step['ts']}",
                )
                port.on_event(t, t.ts)
            elif op == "submit":
                port.submit(step["id"], _intent(step), step["ts"])
            elif op == "cancel":
                port.cancel(step["id"], step["now"])
            elif op == "advance_ts":
                port.advance_to(step["ts"])
            elif op == "session_close":
                port.on_session_close(step["ts"])
            elif op == "set_position":
                port.positions[_iid(step["symbol"])] = step["qty"]
            elif op == "drain":
                drains.append(_rows(port.drain_events()))
            else:  # pragma: no cover
                raise AssertionError(f"unknown op {op!r}")
        except (ValueError, RuntimeError):
            assert step.get("error"), f"{sc['name']}: unexpected error at {step}"
        else:
            assert not step.get("error"), f"{sc['name']}: expected an error at {step}"
    return {
        "drains": drains,
        "final": {
            "cash": port.cash.amount,
            "positions": {
                i.symbol: q for i, q in sorted(port.positions.items(), key=lambda kv: kv[0].symbol)
            },
            "working": port.working_orders,
            "fees": port.fees.amount,
            "traded_notional": port.traded_notional.amount,
        },
    }


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_l2_vector_replays_through_the_port(scenario: dict[str, Any], backend: str) -> None:
    got = _replay_port(scenario, backend)
    assert got["drains"] == scenario["expect"]["drains"]
    assert got["final"] == scenario["expect"]["final"]


def test_every_l2_scenario_is_replayed_and_the_file_is_the_expected_one() -> None:
    assert DOC["fixture_version"] == 1
    assert DOC["type"] == "L2Sim"
    assert len(SCENARIOS) == 20


# -- the raw binding ---------------------------------------------------------------------------
def _replay_raw(sc: dict[str, Any]) -> dict[str, Any]:
    from honba import _honba

    cfg = sc["config"]
    sim = _honba.L2Simulator(
        cfg["cash"],
        cfg["currency"],
        long_only=cfg["long_only"],
        lot_sizes=cfg["lot_sizes"],
        costs=(
            (lambda side, q, p: _cost_fn(cfg["costs"])(OrderSide(side), q, p).amount)
            if "costs" in cfg
            else None
        ),
    )
    drains: list[dict[str, Any]] = []
    for step in sc["steps"]:
        op = step["op"]
        body = {k: v for k, v in step.items() if k != "op"}
        if op == "quote":
            sim.on_quote(body)
        elif op == "trade":
            sim.on_trade(body)
        elif op == "submit":
            sim.submit(body)
        elif op == "cancel":
            sim.cancel(step["id"], step["now"])
        elif op == "advance_ts":
            sim.advance_to(step["ts"])
        elif op == "session_close":
            sim.on_session_close(step["ts"])
        elif op == "set_position":
            sim.set_position(step["symbol"], step["qty"])
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
                        [r["order_id"], r["symbol"], r["side"], r["quantity"], r["reason"], r["ts"]]
                        for r in sim.drain_rejections()
                    ],
                }
            )
        else:  # pragma: no cover
            raise AssertionError(f"unknown op {op!r}")
    return {
        "drains": drains,
        "final": {
            "cash": sim.cash,
            "positions": {s: q for s, _e, q in sorted(sim.positions)},
            "working": sim.working_orders,
            "fees": sim.fees,
            "traded_notional": sim.traded_notional,
        },
    }


@pytest.mark.skipif(not HAVE_NATIVE, reason="native extension not built")
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_l2_vector_replays_through_the_raw_binding(scenario: dict[str, Any]) -> None:
    got = _replay_raw(scenario)
    assert got["drains"] == scenario["expect"]["drains"]
    assert got["final"] == scenario["expect"]["final"]
