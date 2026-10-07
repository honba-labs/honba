"""Seeded random scenarios must give identical results on both ``NextOpenExecution`` backends.

ADR 0016, chunk 3b: the pure-Python implementation (the reference) and the native
``honba._honba.NextOpenSimulator`` behind the same class are driven through one generated
sequence of operations (bars, session events, submits, cancels, drains, holdings seeded through
``positions``, lot sizes, settlement changes, error injections) and every step's outcome must
match exactly: raised exception type, fills, rejections, cash, fees, traded notional,
unsettled, available cash, positions and working orders. Deterministic: fixed seeds, no clock,
no network.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass
from typing import Any

import pytest

from honba.backtest.simulated import (
    FillCostFn,
    NextOpenExecution,
    SessionOpen,
    resolve_fill_costs,
)
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide

try:
    from honba._native import native_attr

    _HAVE_NATIVE = hasattr(native_attr("NextOpenSimulator"), "set_position")
except (ImportError, RuntimeError):  # pragma: no cover - extension not built
    _HAVE_NATIVE = False

pytestmark = pytest.mark.skipif(not _HAVE_NATIVE, reason="native extension not built")

SEEDS = range(400)
IDS = [
    InstrumentId("AAA", "NSE"),
    InstrumentId("BBB", "NSE"),
    InstrumentId("CCC", "NSE"),
    InstrumentId("AAA", "BSE"),
]
QTYS = [1, 1, 2, 3, 5, 7, 10, 13, 25, 0.5, 1.5, 100, 1000, 0.1 + 0.2, 4.0000000001]
OPENS = [5.0, 9.99, 10.0, 33.33, 100.0, 250.5, 1234.56, 2950.0, 19999.95]


class Boom(Exception):
    """A cost function failure that must propagate unchanged on both backends."""


# -- cost models (fresh callables per run; pure) -----------------------------------------------
def vector_costs(spec: dict[str, int]) -> FillCostFn:
    def cost(side: OrderSide, quantity: float, price: float) -> Money:
        tag = "buy" if side is OrderSide.BUY else "sell"
        notional = Money.mul_qty(quantity, price, Currency.INR).amount
        minor = spec.get(f"flat_{tag}", 0) + (notional * spec.get(f"bps_{tag}", 0) + 5000) // 10000
        return Money.from_minor(minor, Currency.INR)

    return cost


def wrapped_india() -> FillCostFn:
    inner = resolve_fill_costs("india.equity")

    def cost(side: OrderSide, quantity: float, price: float) -> Money:
        return inner(side, quantity, price)  # same numbers, but through the callable bridge

    return cost


def raising_costs(side: OrderSide, quantity: float, price: float) -> Money:
    if quantity == 13:
        raise Boom("no costs for 13")
    return Money.from_minor(3, Currency.INR)


def usd_costs(side: OrderSide, quantity: float, price: float) -> Money:
    return Money.from_minor(5, Currency.USD)


def build_costs(kind: str) -> FillCostFn | None:
    if kind == "none":
        return None
    if kind == "india.equity":
        return resolve_fill_costs("india.equity")
    if kind == "india.equity.intraday":
        return resolve_fill_costs("india.equity.intraday")
    if kind == "wrapped_india":
        return wrapped_india()
    if kind == "raising":
        return raising_costs
    if kind == "usd":
        return usd_costs
    if kind == "negative":
        return vector_costs({"flat_buy": -1, "flat_sell": -1})
    if kind == "flat":
        return vector_costs({"flat_buy": 20, "flat_sell": 25})
    return vector_costs({"flat_buy": 10, "bps_buy": 25, "bps_sell": 40, "flat_sell": 5})


COST_KINDS = (
    ["none"] * 3
    + ["india.equity"] * 3
    + ["india.equity.intraday", "wrapped_india", "flat", "bps"] * 2
    + ["raising", "usd", "negative"]
)


# -- scenario generation -------------------------------------------------------------------------
@dataclass
class Scenario:
    seed: int
    config: dict[str, Any]
    steps: list[dict[str, Any]]


def _open_price(rng: random.Random) -> float:
    r = rng.random()
    if r < 0.03:
        return math.nan
    if r < 0.06:
        return 0.0
    if r < 0.5:
        return rng.choice(OPENS)
    return round(rng.uniform(5.0, 3000.0), 2)


def _bar(iid: InstrumentId, ts: int, rng: random.Random) -> Bar:
    o = _open_price(rng)
    c = o if not math.isfinite(o) else round(o * rng.uniform(0.95, 1.05), 2)
    hi = o if not math.isfinite(o) else max(o, c)
    lo = o if not math.isfinite(o) else min(o, c)
    return Bar(iid, ts, o, hi, lo, c, 1_000.0)


def _intent(rng: random.Random, iid: InstrumentId) -> OrderIntent:
    side = OrderSide.BUY if rng.random() < 0.6 else OrderSide.SELL
    qty = rng.choice(QTYS)
    r = rng.random()
    if r < 0.93:
        return OrderIntent(iid, side, qty)
    from honba.entities.order import OrderType

    if r < 0.96:
        return OrderIntent(iid, side, qty, OrderType.LIMIT, price=101.5)
    if r < 0.98:
        return OrderIntent(iid, side, qty, OrderType.STOP_MARKET, trigger_price=99.0)
    return OrderIntent(iid, side, qty, OrderType.STOP_LIMIT, price=100.0, trigger_price=99.0)


def generate(seed: int) -> Scenario:
    rng = random.Random(seed)
    universe = rng.sample(IDS, rng.randint(1, len(IDS)))
    config: dict[str, Any] = {
        "cash": rng.choice([0, 50_000, 1_000_00, 10_000_00, 5_000_000, 100_000_000]),
        "settlement_days": rng.choice([0, 0, 1, 2, 3]),
        "long_only": rng.random() < 0.8,
        "lot_sizes": {i: rng.choice([1.0, 5.0, 10.0, 0.5]) for i in universe if rng.random() < 0.3},
        "costs": rng.choice(COST_KINDS),
    }
    mode = rng.choice(["bars", "bars", "open", "sopen", "sopen"])
    steps: list[dict[str, Any]] = []
    for i in universe:
        if rng.random() < 0.4:
            steps.append({"op": "seed", "iid": i, "qty": rng.choice([1.0, 4.0, 10.0, 100.0, 0.3])})
    if rng.random() < 0.15:
        steps.append({"op": "set_settlement_days", "days": rng.choice([-1, 0, 2])})
    n_orders = 0
    ts = rng.randint(1, 5)
    order_ids: list[str] = []
    for _session in range(rng.randint(4, 12)):
        bars = [_bar(i, ts, rng) for i in universe if rng.random() < 0.8]
        if mode == "bars":
            steps += [{"op": "bar", "bar": b} for b in bars]
            if bars and rng.random() < 0.05:
                steps.append({"op": "bar", "bar": bars[0]})  # duplicate: error
            if rng.random() < 0.04:
                steps.append({"op": "bar", "bar": _bar(universe[0], ts - 1, rng)})  # older: error
        elif mode == "open":
            steps.append({"op": "open_session", "ts": ts, "bars": bars})
            if rng.random() < 0.05:
                steps.append({"op": "open_session", "ts": ts, "bars": bars})  # not advancing
            if bars and rng.random() < 0.15:
                steps.append({"op": "bar", "bar": bars[0]})  # duplicate after open_session
        else:
            shown = [b for b in bars if rng.random() < 0.8]
            steps.append({"op": "session_open", "ts": ts, "bars": shown})
            steps += [{"op": "bar", "bar": b} for b in bars]  # incl. ones the event left out
            if rng.random() < 0.05:
                steps.append({"op": "session_open", "ts": ts, "bars": shown})  # not advancing
        for _ in range(rng.choice([0, 0, 1, 2, 3])):
            if order_ids and rng.random() < 0.03:
                oid = rng.choice(order_ids)  # maybe still working: duplicate id error
            else:
                oid = f"o{n_orders}"
                n_orders += 1
                order_ids.append(oid)
            steps.append(
                {"op": "submit", "id": oid, "intent": _intent(rng, rng.choice(universe)), "ts": ts}
            )
        if order_ids and rng.random() < 0.2:
            cancel_id = rng.choice(order_ids) if rng.random() < 0.85 else "unknown"
            steps.append({"op": "cancel", "id": cancel_id, "now": ts})
        if rng.random() < 0.08:
            steps.append(
                {"op": "set_lot_size", "iid": rng.choice(universe), "lot": rng.choice([2.0, 0.0])}
            )
        if rng.random() < 0.05:
            steps.append({"op": "set_settlement_days", "days": rng.choice([1, 2])})
        if rng.random() < 0.04:
            steps.append({"op": "seed", "iid": rng.choice(universe), "qty": rng.choice([3.0, 8.0])})
        if rng.random() < 0.6:
            steps.append({"op": "drain"})
        ts += rng.randint(1, 3)
    steps.append({"op": "drain"})
    return Scenario(seed, config, steps)


# -- running -------------------------------------------------------------------------------------
def snapshot(port: NextOpenExecution) -> dict[str, Any]:
    return {
        "cash": port.cash,
        "fees": port.fees,
        "traded_notional": port.traded_notional,
        "unsettled": port.unsettled,
        "available_cash": port.available_cash,
        "positions": dict(port.positions),
        "working": list(port.working_orders),
        "settlement_days": port.settlement_days,
    }


def run(sc: Scenario, backend: str) -> list[Any]:
    cfg = sc.config
    port = NextOpenExecution(
        cash=Money.from_minor(cfg["cash"], Currency.INR),
        settlement_days=cfg["settlement_days"],
        long_only=cfg["long_only"],
        lot_sizes=dict(cfg["lot_sizes"]),
        backend=backend,
        **({"costs": c} if (c := build_costs(cfg["costs"])) is not None else {}),
    )
    assert port.backend == backend
    log: list[Any] = [snapshot(port)]
    for step in sc.steps:
        op = step["op"]
        out: Any = None
        try:
            if op == "bar":
                port.on_event(step["bar"], step["bar"].ts)
            elif op == "open_session":
                port.open_session(step["ts"], step["bars"])
            elif op == "session_open":
                port.on_event(SessionOpen(step["ts"], tuple(step["bars"])), step["ts"])
            elif op == "submit":
                port.submit(step["id"], step["intent"], step["ts"])
            elif op == "cancel":
                port.cancel(step["id"], step["now"])
            elif op == "set_settlement_days":
                port.set_settlement_days(step["days"])
            elif op == "set_lot_size":
                port.set_lot_size(step["iid"], step["lot"])
            elif op == "seed":
                port.positions[step["iid"]] = step["qty"]
            elif op == "drain":
                out = (port.drain_fills(), port.drain_rejections())
            else:  # pragma: no cover
                raise AssertionError(op)
        except (ValueError, RuntimeError, Boom) as exc:
            out = ("error", type(exc).__name__)
        log.append((op, out, snapshot(port)))
    return log


def _first_difference(a: list[Any], b: list[Any]) -> str:
    for n, (x, y) in enumerate(zip(a, b, strict=False)):
        if x != y:
            return f"step {n}:\n  python: {x!r}\n  native: {y!r}"
    return f"lengths differ: {len(a)} vs {len(b)}"


@pytest.mark.parametrize("seed", SEEDS)
def test_both_backends_give_identical_results(seed: int) -> None:
    sc = generate(seed)
    py, nat = run(sc, "python"), run(sc, "native")
    assert py == nat, f"seed {seed} config {sc.config}\n" + _first_difference(py, nat)


def test_the_generated_corpus_exercises_the_interesting_paths() -> None:
    """Guards against a vacuous fuzz: fills, every rejection reason, errors, settlement, costs."""
    seen: Counter[str] = Counter()
    for seed in SEEDS:
        sc = generate(seed)
        for entry in run(sc, "python")[1:]:
            op, out, snap = entry
            if op == "drain":
                fills, rejections = out
                seen["fill"] += len(fills)
                seen["fill_with_cost"] += sum(1 for f in fills if f.costs.amount > 0)
                for r in rejections:
                    seen[f"reject:{r.reason}"] += 1
            elif isinstance(out, tuple) and out[0] == "error":
                seen[f"error:{out[1]}"] += 1
            if snap["unsettled"].amount > 0:
                seen["unsettled"] += 1
    wanted = [
        "fill",
        "fill_with_cost",
        "reject:no_position",
        "reject:insufficient_funds",
        "reject:unsupported_order_type",
        "reject:cancelled",
        "error:ValueError",
        "error:RuntimeError",
        "error:Boom",
        "unsettled",
    ]
    missing = [w for w in wanted if seen[w] == 0]
    assert not missing, f"scenarios never reach {missing}; saw {dict(seen)}"
