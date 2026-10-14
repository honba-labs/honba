"""Seeded random scenarios must give identical results on both ``L2Execution`` backends.

ADR 0030, chunk E3-S1b-2: the pure-Python reference and the native
``honba._honba.L2Simulator`` behind the same class are driven through one generated stream of
quotes, trades, submits (every order type and time in force), cancels, clock advances, session
closes, seeded holdings, lot sizes and cost failures; every step's outcome must match exactly:
raised exception type, the ordered event stream, cash, fees, traded notional, positions, working
orders and books. Deterministic: fixed seeds, no clock, no network.
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from typing import Any

import pytest

from honba.backtest.l2_sim import L2Execution
from honba.backtest.simulated import FillCostFn, resolve_fill_costs
from honba.domain.money import Currency, Money
from honba.domain.tick import AggressorSide, QuoteTick, TradeTick
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderType, TimeInForce
from honba.strategies.execution import Cancelled, Expired, Fill, Rejected

try:
    from honba._native import native_attr

    _HAVE_NATIVE = hasattr(native_attr("L2Simulator"), "on_session_close")
except (ImportError, RuntimeError):  # pragma: no cover - extension not built
    _HAVE_NATIVE = False

pytestmark = pytest.mark.skipif(not _HAVE_NATIVE, reason="native extension not built")

SEEDS = range(400)
IDS = [InstrumentId("AAA", "NSE"), InstrumentId("BBB", "NSE"), InstrumentId("AAA", "BSE")]
QTYS = [1, 1, 2, 3, 5, 7, 10, 13, 25, 0.5, 1.5, 100, 1000, 0.1 + 0.2, 4.0000000001]
MIDS = [5.0, 9.99, 10.0, 33.33, 100.0, 250.5, 1234.56, 2950.0]
DAY_NS = 86_400_000_000_000
CLOSE_NS = 86_340_000_000_000


class Boom(Exception):
    """A cost function failure that must propagate unchanged on both backends."""


def vector_costs(spec: dict[str, int]) -> FillCostFn:
    def cost(side: OrderSide, quantity: float, price: float) -> Money:
        tag = "buy" if side is OrderSide.BUY else "sell"
        notional = Money.mul_qty(quantity, price, Currency.INR).amount
        minor = spec.get(f"flat_{tag}", 0) + (notional * spec.get(f"bps_{tag}", 0) + 5000) // 10000
        return Money.from_minor(minor, Currency.INR)

    return cost


def raising_costs(side: OrderSide, quantity: float, price: float) -> Money:
    if quantity in (5, 10):
        raise Boom("no costs for 5 or 10")
    return Money.from_minor(3, Currency.INR)


def build_costs(kind: str) -> FillCostFn | None:
    if kind == "none":
        return None
    if kind == "india.equity":
        return resolve_fill_costs("india.equity")
    if kind == "raising":
        return raising_costs
    if kind == "flat":
        return vector_costs({"flat_buy": 20, "flat_sell": 25})
    return vector_costs({"flat_buy": 10, "bps_buy": 25, "bps_sell": 40, "flat_sell": 5})


COST_KINDS = ["none"] * 3 + ["india.equity"] * 2 + ["flat", "bps", "bps", "raising"]


@dataclass
class Scenario:
    seed: int
    config: dict[str, Any]
    steps: list[dict[str, Any]]


def _quote(iid: InstrumentId, ts: int, rng: random.Random) -> QuoteTick:
    mid = rng.choice(MIDS)
    half = rng.choice([0.0, 0.01, 0.05, 0.5])
    return QuoteTick(
        iid,
        ts,
        round(mid - half, 2),
        round(mid + half, 2),
        rng.choice([0, 1, 5, 10, 50, 1000]),
        rng.choice([0, 1, 5, 10, 50, 1000]),
    )


def _trade(iid: InstrumentId, ts: int, rng: random.Random, ref: float) -> TradeTick:
    px = round(ref * rng.choice([0.97, 0.99, 1.0, 1.0, 1.01, 1.03]), 2)
    return TradeTick(
        iid,
        ts,
        px,
        rng.choice([0, 1, 3, 10, 100]),
        rng.choice(list(AggressorSide)),
        f"T-{ts}",
    )


def _intent(rng: random.Random, iid: InstrumentId, ref: float) -> OrderIntent:
    side = OrderSide.BUY if rng.random() < 0.55 else OrderSide.SELL
    qty = rng.choice(QTYS)
    tif = rng.choice(
        [TimeInForce.DAY] * 4
        + [TimeInForce.GTC] * 2
        + [TimeInForce.IOC, TimeInForce.FOK, TimeInForce.GTD]
    )
    px = round(ref * rng.choice([0.98, 0.995, 1.0, 1.005, 1.02]), 2)
    trig = round(ref * rng.choice([0.98, 0.99, 1.01, 1.02]), 2)
    r = rng.random()
    if r < 0.3:
        return OrderIntent(iid, side, qty, time_in_force=tif)
    if r < 0.7:
        return OrderIntent(iid, side, qty, OrderType.LIMIT, price=px, time_in_force=tif)
    if r < 0.82:
        return OrderIntent(
            iid, side, qty, OrderType.STOP_MARKET, trigger_price=trig, time_in_force=tif
        )
    if r < 0.94:
        return OrderIntent(
            iid, side, qty, OrderType.STOP_LIMIT, price=px, trigger_price=trig, time_in_force=tif
        )
    return OrderIntent(iid, side, qty, OrderType.TRAILING_STOP, trail_amount=1.0, time_in_force=tif)


def generate(seed: int) -> Scenario:
    rng = random.Random(seed)
    universe = rng.sample(IDS, rng.randint(1, len(IDS)))
    refs = {i: rng.choice(MIDS) for i in universe}
    config: dict[str, Any] = {
        "cash": rng.choice([0, 50_000, 100_000, 1_000_000, 5_000_000, 100_000_000]),
        "long_only": rng.random() < 0.8,
        "lot_sizes": {i: rng.choice([1.0, 5.0, 10.0, 0.5]) for i in universe if rng.random() < 0.3},
        "costs": rng.choice(COST_KINDS),
    }
    steps: list[dict[str, Any]] = []
    for i in universe:
        if rng.random() < 0.4:
            steps.append({"op": "seed", "iid": i, "qty": rng.choice([1.0, 4.0, 10.0, 100.0, 0.3])})
    ts = rng.randint(1, 5)
    if rng.random() < 0.3:
        ts = CLOSE_NS - rng.randint(1, 5_000_000_000)  # start just before a session close
    n_orders = 0
    order_ids: list[str] = []
    for _ in range(rng.randint(15, 40)):
        iid = rng.choice(universe)
        r = rng.random()
        if r < 0.3:
            q = _quote(iid, ts, rng)
            refs[iid] = (q.bid_price + q.ask_price) / 2 or refs[iid]
            steps.append({"op": "quote", "event": q})
        elif r < 0.5:
            steps.append({"op": "trade", "event": _trade(iid, ts, rng, refs[iid])})
        elif r < 0.78:
            if order_ids and rng.random() < 0.04:
                oid = rng.choice(order_ids)  # maybe still working: duplicate id error
            else:
                oid = f"o{n_orders}"
                n_orders += 1
                order_ids.append(oid)
            steps.append(
                {"op": "submit", "id": oid, "intent": _intent(rng, iid, refs[iid]), "ts": ts}
            )
        elif r < 0.84 and order_ids:
            cancel_id = rng.choice(order_ids) if rng.random() < 0.85 else "unknown"
            steps.append({"op": "cancel", "id": cancel_id, "now": ts})
        elif r < 0.88:
            steps.append({"op": "advance_ts", "ts": ts + rng.choice([0, 1, DAY_NS // 2, DAY_NS])})
        elif r < 0.9:
            steps.append({"op": "session_close", "ts": ts})
        elif r < 0.93:
            steps.append({"op": "set_lot_size", "iid": iid, "lot": rng.choice([2.0, 0.0])})
        elif r < 0.95:
            steps.append({"op": "seed", "iid": iid, "qty": rng.choice([3.0, 8.0, 0.0])})
        else:
            steps.append({"op": "drain"})
        ts += rng.choice([0, 1, 7, 1_000, 1_000_000, 3_600_000_000_000, DAY_NS // 3])
    steps.append({"op": "drain"})
    return Scenario(seed, config, steps)


def snapshot(port: L2Execution) -> dict[str, Any]:
    return {
        "cash": port.cash,
        "fees": port.fees,
        "traded_notional": port.traded_notional,
        "positions": dict(port.positions),
        "positions_order": list(port.positions),
        "working": list(port.working_orders),
        "books": {i: port.book(i) for i in IDS},
    }


def run(sc: Scenario, backend: str) -> list[Any]:
    cfg = sc.config
    port = L2Execution(
        cash=Money.from_minor(cfg["cash"], Currency.INR),
        long_only=cfg["long_only"],
        lot_sizes=dict(cfg["lot_sizes"]),
        backend=backend,  # type: ignore[arg-type]
        **({"costs": c} if (c := build_costs(cfg["costs"])) is not None else {}),
    )
    assert port.backend == backend
    log: list[Any] = [snapshot(port)]
    for step in sc.steps:
        op = step["op"]
        out: Any = None
        try:
            if op in ("quote", "trade"):
                port.on_event(step["event"], step["event"].ts)
            elif op == "submit":
                port.submit(step["id"], step["intent"], step["ts"])
            elif op == "cancel":
                port.cancel(step["id"], step["now"])
            elif op == "advance_ts":
                port.advance_to(step["ts"])
            elif op == "session_close":
                port.on_session_close(step["ts"])
            elif op == "set_lot_size":
                port.set_lot_size(step["iid"], step["lot"])
            elif op == "seed":
                port.positions[step["iid"]] = step["qty"]
            elif op == "drain":
                out = port.drain_events()
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
    """Guards against a vacuous fuzz: fills, every rejection kind, expiry, cancels, errors."""
    seen: Counter[str] = Counter()
    for seed in SEEDS:
        for entry in run(generate(seed), "python")[1:]:
            op, out, _snap = entry
            if op == "drain":
                for ev in out:
                    if isinstance(ev, Fill):
                        seen["fill"] += 1
                        seen["partial"] += not ev.complete
                        seen["fill_with_cost"] += ev.trade.costs.amount > 0
                    elif isinstance(ev, Rejected):
                        seen[f"reject:{ev.reason}"] += 1
                    elif isinstance(ev, Expired):
                        seen["expired"] += 1
                    elif isinstance(ev, Cancelled):
                        seen["cancelled"] += 1
            elif isinstance(out, tuple) and out[0] == "error":
                seen[f"error:{out[1]}"] += 1
    wanted = [
        "fill",
        "partial",
        "fill_with_cost",
        "reject:no_position",
        "reject:insufficient_funds",
        "reject:unsupported_order_type",
        "reject:fok_unfillable",
        "expired",
        "cancelled",
        "error:ValueError",
        "error:Boom",
    ]
    missing = [w for w in wanted if seen[w] == 0]
    assert not missing, f"scenarios never reach {missing}; saw {dict(seen)}"
