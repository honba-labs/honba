"""Regenerates ``schema/conformance/next_open_sim.json`` from the PYTHON ``NextOpenExecution``.

The Python simulator (``honba.backtest.simulated``) is the reference (ADR 0016): every expected
fill, rejection, cash and position below is computed by it, once, and committed. The Rust
``honba_sim::NextOpenSim`` (``crates/honba-sim/tests/next_open_conformance.rs``) and the Python
conformance test (``python/tests/integration/test_next_open_sim_conformance.py``) replay the file;
the Python test imports :func:`replay` from here, so the generator and the test cannot drift.

Run from ``python/``:  ``python ../scripts/gen_next_open_vectors.py``  (``--check`` only compares).
Deterministic: no wall clock, no randomness.

Scenario schema (``fixture_version`` 1)::

    {"name", "chunk", "config": {"cash", "currency", "long_only", "settlement_days", "lot_sizes"},
     "steps": [...], "expect": {...}}

``cash`` is integer minor units. Steps, replayed in order (symbols are NSE instruments, prices and
quantities are plain numbers):

* ``{"op": "bar", "symbol", "ts", "open", "high", "low", "close"}``: feed one bar event.
* ``{"op": "open_session", "ts", "bars": [bar, ...]}``: open a session with all its bars.
* ``{"op": "submit", "id", "symbol", "side": "buy"|"sell", "type": "market"|"limit"|
  "stop_market"|"stop_limit", "qty", "ts", "price"?, "trigger"?}``
* ``{"op": "cancel", "id", "now"}``: ``now`` is the engine time of the cancel (informational for
  Python, whose session ts is the same value in every vector).
* ``{"op": "drain"}``: drain fills and rejections; recorded in ``expect.drains``.

A step that raises in the reference gets ``"error": true`` (the exception type is not compared).
``expect``: ``drains`` (list of ``{"fills", "rejections"}``) and ``final`` with ``cash``,
``positions`` (symbol -> quantity, sorted), ``working`` (order ids), ``fees``,
``traded_notional`` (minor units). A fill is
``[order_id, symbol, side, quantity, price, ts, costs]`` and a rejection
``[order_id, symbol, side, quantity, reason, ts]``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from honba.backtest.simulated import NextOpenExecution
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderType

OUT = Path(__file__).resolve().parents[1] / "schema" / "conformance" / "next_open_sim.json"
FIXTURE_VERSION = 1


def _iid(symbol: str) -> InstrumentId:
    return InstrumentId(symbol, "NSE")


def _bar(step: dict[str, Any]) -> Bar:
    return Bar(
        _iid(step["symbol"]),
        step["ts"],
        step["open"],
        step["high"],
        step["low"],
        step["close"],
        step.get("volume", 1000.0),
    )


def _intent(step: dict[str, Any]) -> OrderIntent:
    iid, qty = _iid(step["symbol"]), step["qty"]
    side = OrderSide.BUY if step["side"] == "buy" else OrderSide.SELL
    kind = step["type"]
    if kind == "market":
        return OrderIntent(iid, side, qty)
    if kind == "limit":
        return OrderIntent(iid, side, qty, OrderType.LIMIT, step["price"])
    if kind == "stop_market":
        return OrderIntent(iid, side, qty, OrderType.STOP_MARKET, None, step["trigger"])
    return OrderIntent(iid, side, qty, OrderType.STOP_LIMIT, step["price"], step["trigger"])


def _side(side: OrderSide) -> str:
    return "buy" if side is OrderSide.BUY else "sell"


def _port(config: dict[str, Any]) -> NextOpenExecution:
    cash = Money.from_minor(config["cash"], Currency[config["currency"]])
    lots = {_iid(s): lot for s, lot in config.get("lot_sizes", {}).items()}
    return NextOpenExecution(
        cash=cash,
        settlement_days=config.get("settlement_days", 0),
        long_only=config.get("long_only", True),
        lot_sizes=lots,
    )


def replay(scenario: dict[str, Any]) -> dict[str, Any]:
    """Runs ``scenario`` through the Python reference; returns its steps (errors flagged) + expect."""
    port = _port(scenario["config"])
    steps: list[dict[str, Any]] = []
    drains: list[dict[str, Any]] = []
    for raw in scenario["steps"]:
        step = {k: v for k, v in raw.items() if k != "error"}
        try:
            op = step["op"]
            if op == "bar":
                port.on_event(_bar(step), step["ts"])
            elif op == "open_session":
                port.open_session(step["ts"], [_bar(b) for b in step["bars"]])
            elif op == "submit":
                port.submit(step["id"], _intent(step), step["ts"])
            elif op == "cancel":
                port.cancel(step["id"])
            elif op == "drain":
                drains.append(
                    {
                        "fills": [
                            [
                                f.order_id,
                                f.instrument_id.symbol,
                                _side(f.side),
                                f.quantity,
                                f.price,
                                f.ts,
                                f.costs.amount,
                            ]
                            for f in port.drain_fills()
                        ],
                        "rejections": [
                            [
                                r.order_id,
                                r.intent.instrument_id.symbol,
                                _side(r.intent.side),
                                r.intent.quantity,
                                r.reason,
                                r.ts,
                            ]
                            for r in port.drain_rejections()
                        ],
                    }
                )
            else:
                raise AssertionError(f"unknown op {op!r}")
        except ValueError:
            step["error"] = True
        steps.append(step)
    expect = {
        "drains": drains,
        "cash": port.cash.amount,
        "positions": {i.symbol: q for i, q in sorted(port.positions.items(), key=_pos_key)},
        "working": port.working_orders,
        "fees": port.fees.amount,
        "traded_notional": port.traded_notional.amount,
    }
    return {"steps": steps, "expect": expect}


def _pos_key(item: tuple[InstrumentId, float]) -> tuple[str, str]:
    return (item[0].symbol, item[0].exchange)


# -- scenario authoring ------------------------------------------------------------------------
def b(symbol: str, ts: int, open_: float, close: float | None = None) -> dict[str, Any]:
    c = open_ if close is None else close
    return {
        "op": "bar",
        "symbol": symbol,
        "ts": ts,
        "open": open_,
        "high": max(open_, c),
        "low": min(open_, c),
        "close": c,
    }


def sess(ts: int, *bars: dict[str, Any]) -> dict[str, Any]:
    return {"op": "open_session", "ts": ts, "bars": [{**x, "op": "bar"} for x in bars]}


def order(
    oid: str, side: str, symbol: str, qty: float, ts: int, kind: str = "market", **extra: float
) -> dict[str, Any]:
    return {
        "op": "submit",
        "id": oid,
        "symbol": symbol,
        "side": side,
        "type": kind,
        "qty": qty,
        "ts": ts,
        **extra,
    }


def cancel(oid: str, now: int) -> dict[str, Any]:
    return {"op": "cancel", "id": oid, "now": now}


DRAIN: dict[str, Any] = {"op": "drain"}


def cfg(cash_rupees: float, **kw: Any) -> dict[str, Any]:
    return {
        "cash": round(cash_rupees * 100),
        "currency": "INR",
        "long_only": True,
        "settlement_days": 0,
        "lot_sizes": {},
        **kw,
    }


def scenario(name: str, config: dict[str, Any], steps: list[dict[str, Any]], chunk: int = 1):
    return {"name": name, "chunk": chunk, "config": config, "steps": steps}


def scenarios() -> list[dict[str, Any]]:
    return [
        scenario(
            "fills_at_next_open_not_decision_close",
            cfg(10_000),
            [
                b("AAA", 1, 100.0, 105.0),
                order("o-0", "buy", "AAA", 10, 1),
                DRAIN,
                b("AAA", 2, 110.0, 120.0),
                DRAIN,
            ],
        ),
        scenario(
            "order_waits_for_its_instrument_to_print",
            cfg(10_000),
            [
                b("AAA", 1, 100.0),
                order("o-0", "buy", "BBB", 1, 1),
                b("AAA", 2, 100.0),
                DRAIN,
                b("BBB", 3, 50.0),
                DRAIN,
            ],
        ),
        scenario(
            "same_session_bar_does_not_fill_an_order_submitted_in_it",
            cfg(10_000),
            [
                b("AAA", 1, 100.0),
                order("o-0", "buy", "BBB", 1, 1),
                b("BBB", 1, 50.0),
                DRAIN,
                b("BBB", 2, 60.0),
                DRAIN,
            ],
        ),
        scenario(
            "order_submitted_before_any_session_fills_at_the_first_open",
            cfg(10_000),
            [order("o-0", "buy", "AAA", 2, 0), b("AAA", 1, 100.0), DRAIN],
        ),
        scenario(
            "two_bars_one_ts_open_their_own_instruments",
            cfg(10_000),
            [
                b("AAA", 1, 100.0),
                order("o-0", "buy", "AAA", 1, 1),
                order("o-1", "buy", "BBB", 1, 1),
                b("AAA", 2, 101.0),
                b("BBB", 2, 51.0),
                DRAIN,
            ],
        ),
        scenario(
            "sells_fill_before_buys_in_a_session",
            cfg(1_000),
            [
                sess(1, b("AAA", 1, 100.0), b("BBB", 1, 100.0)),
                order("o-0", "buy", "AAA", 10, 1),
                sess(2, b("AAA", 2, 100.0), b("BBB", 2, 100.0)),
                DRAIN,
                order("o-1", "buy", "BBB", 10, 2),
                order("o-2", "sell", "AAA", 10, 2),
                sess(3, b("AAA", 3, 100.0), b("BBB", 3, 100.0)),
                DRAIN,
            ],
        ),
        scenario(
            "buys_fill_in_submission_order_until_cash_runs_out",
            cfg(1_000),
            [
                b("AAA", 1, 10.0),
                order("o-0", "buy", "AAA", 60, 1),
                order("o-1", "buy", "AAA", 60, 1),
                b("AAA", 2, 10.0),
                DRAIN,
            ],
        ),
        scenario(
            "sell_is_capped_at_the_position",
            cfg(10_000),
            [
                b("AAA", 1, 100.0),
                order("o-0", "buy", "AAA", 5, 1),
                b("AAA", 2, 100.0),
                order("o-1", "sell", "AAA", 8, 2),
                b("AAA", 3, 100.0),
                DRAIN,
            ],
        ),
        scenario(
            "sell_without_a_position_is_rejected",
            cfg(10_000),
            [b("AAA", 1, 100.0), order("o-0", "sell", "AAA", 3, 1), b("AAA", 2, 100.0), DRAIN],
        ),
        scenario(
            "sell_a_hair_over_the_position_is_float_residue",
            cfg(10_000),
            [
                b("AAA", 1, 100.0),
                order("o-0", "buy", "AAA", 0.3, 1),
                b("AAA", 2, 100.0),
                order("o-1", "sell", "AAA", 0.3 + 5e-10, 2),
                b("AAA", 3, 100.0),
                DRAIN,
            ],
        ),
        scenario(
            "short_sell_when_not_long_only",
            cfg(10_000, long_only=False),
            [
                b("AAA", 1, 100.0),
                order("o-0", "sell", "AAA", 4, 1),
                b("AAA", 2, 100.0),
                DRAIN,
            ],
        ),
        scenario(
            "buy_is_cut_to_whole_units_the_cash_allows",
            cfg(1_050),
            [
                b("AAA", 1, 100.0),
                order("o-0", "buy", "AAA", 20, 1),
                b("AAA", 2, 100.0),
                DRAIN,
            ],
        ),
        scenario(
            "buy_is_cut_to_whole_lots",
            cfg(805, lot_sizes={"AAA": 3}),
            [
                b("AAA", 1, 10.0),
                order("o-0", "buy", "AAA", 90, 1),
                b("AAA", 2, 10.0),
                DRAIN,
            ],
        ),
        scenario(
            "buy_with_no_cash_for_one_unit_is_rejected_whole",
            cfg(50),
            [b("AAA", 1, 100.0), order("o-0", "buy", "AAA", 2, 1), b("AAA", 2, 100.0), DRAIN],
        ),
        scenario(
            "fractional_quantity_notional_rounds_half_away_from_zero",
            cfg(1_000),
            [
                b("AAA", 1, 33.33),
                order("o-0", "buy", "AAA", 0.5, 1),
                order("o-1", "buy", "AAA", 1.5, 1),
                b("AAA", 2, 33.33),
                DRAIN,
            ],
        ),
        scenario(
            "unusable_open_means_the_instrument_did_not_print",
            cfg(10_000),
            [
                b("AAA", 1, 100.0),
                order("o-0", "buy", "AAA", 1, 1),
                b("AAA", 2, 0.0),
                DRAIN,
                b("AAA", 3, 90.0),
                DRAIN,
            ],
        ),
        scenario(
            "limit_and_stop_orders_are_unsupported",
            cfg(10_000),
            [
                b("AAA", 1, 100.0),
                order("o-0", "buy", "AAA", 1, 1, "limit", price=99.0),
                order("o-1", "sell", "AAA", 1, 1, "stop_market", trigger=95.0),
                order("o-2", "buy", "AAA", 1, 1, "stop_limit", price=101.0, trigger=100.0),
                DRAIN,
                b("AAA", 2, 100.0),
                DRAIN,
            ],
        ),
        scenario(
            "cancel_a_working_order",
            cfg(10_000),
            [
                b("AAA", 1, 100.0),
                order("o-0", "buy", "AAA", 3, 1),
                cancel("o-0", 1),
                b("AAA", 2, 100.0),
                DRAIN,
            ],
        ),
        scenario(
            "late_cancel_is_stamped_with_the_cancel_time",
            cfg(10_000),
            [
                b("AAA", 2, 100.0),
                order("o-0", "buy", "BBB", 3, 2),
                b("AAA", 5, 100.0),
                cancel("o-0", 5),
                DRAIN,
            ],
        ),
        scenario(
            "cancel_of_unknown_filled_or_cancelled_orders_is_a_no_op",
            cfg(10_000),
            [
                b("AAA", 1, 100.0),
                order("o-0", "buy", "AAA", 1, 1),
                order("o-1", "buy", "BBB", 1, 1),
                b("AAA", 2, 100.0),
                cancel("o-0", 2),
                cancel("nope", 2),
                cancel("o-1", 2),
                cancel("o-1", 2),
                DRAIN,
            ],
        ),
        scenario(
            "cancel_before_any_session_is_stamped_zero",
            cfg(10_000),
            [order("o-0", "buy", "AAA", 1, 0), cancel("o-0", 0), DRAIN],
        ),
        scenario(
            "a_cut_order_has_nothing_left_to_cancel",
            cfg(1_050),
            [
                b("AAA", 1, 100.0),
                order("o-0", "buy", "AAA", 20, 1),
                b("AAA", 2, 100.0),
                cancel("o-0", 2),
                DRAIN,
            ],
        ),
        scenario(
            "drain_twice_repeats_nothing",
            cfg(10_000),
            [
                b("AAA", 1, 100.0),
                order("o-0", "buy", "AAA", 1, 1),
                order("o-1", "buy", "BBB", 1, 1),
                cancel("o-1", 1),
                b("AAA", 2, 100.0),
                DRAIN,
                DRAIN,
            ],
        ),
        scenario(
            "duplicate_working_order_id_is_an_error",
            cfg(10_000),
            [
                b("AAA", 1, 100.0),
                order("o-0", "buy", "AAA", 1, 1),
                order("o-0", "buy", "AAA", 2, 1),
                b("AAA", 2, 100.0),
                DRAIN,
            ],
        ),
        scenario(
            "a_finished_order_id_may_be_reused",
            cfg(10_000),
            [
                b("AAA", 1, 100.0),
                order("o-0", "buy", "AAA", 1, 1),
                b("AAA", 2, 100.0),
                order("o-0", "buy", "AAA", 2, 2),
                b("AAA", 3, 100.0),
                DRAIN,
            ],
        ),
        scenario(
            "bar_before_the_session_is_non_monotonic",
            cfg(10_000),
            [
                b("AAA", 5, 100.0),
                order("o-0", "buy", "AAA", 1, 5),
                b("AAA", 4, 100.0),
                b("AAA", 6, 100.0),
                DRAIN,
            ],
        ),
        scenario(
            "second_bar_for_an_instrument_in_a_session_is_an_error",
            cfg(10_000),
            [b("AAA", 1, 100.0), b("AAA", 1, 101.0), b("BBB", 1, 50.0), b("AAA", 2, 100.0), DRAIN],
        ),
        scenario(
            "open_session_must_advance",
            cfg(10_000),
            [sess(3, b("AAA", 3, 100.0)), sess(3, b("AAA", 3, 100.0)), sess(2, b("AAA", 2, 1.0))]
            + [order("o-0", "buy", "AAA", 1, 3), sess(4, b("AAA", 4, 100.0)), DRAIN],
        ),
        scenario(
            "session_key_need_not_be_the_bar_ts",
            cfg(10_000),
            [
                sess(10, b("AAA", 1, 100.0)),
                order("o-0", "buy", "AAA", 1, 1),
                sess(20, b("AAA", 2, 101.0)),
                DRAIN,
            ],
        ),
    ]


def build() -> dict[str, Any]:
    out = []
    for sc in scenarios():
        res = replay(sc)
        out.append({**sc, "steps": res["steps"], "expect": res["expect"]})
    return {
        "fixture_version": FIXTURE_VERSION,
        "type": "NextOpenSim",
        "description": (
            "Next-open simulator vectors generated from honba.backtest.simulated.NextOpenExecution "
            "(ADR 0016). See scripts/gen_next_open_vectors.py for the step and expectation schema. "
            "`chunk` says which implementation chunk covers the scenario; a runner replays only "
            "the chunks it implements."
        ),
        "scenarios": out,
    }


def render(doc: dict[str, Any]) -> str:
    """One scenario header per block, one step per line: reviewable diffs, stable bytes."""
    lines = [
        "{",
        f'  "fixture_version": {doc["fixture_version"]},',
        f'  "type": {json.dumps(doc["type"])},',
        f'  "description": {json.dumps(doc["description"])},',
        '  "scenarios": [',
    ]
    blocks = []
    for sc in doc["scenarios"]:
        steps = ",\n".join(f"        {json.dumps(s)}" for s in sc["steps"])
        drains = ",\n".join(f"        {json.dumps(d)}" for d in sc["expect"]["drains"])
        rest = {k: v for k, v in sc["expect"].items() if k != "drains"}
        blocks.append(
            "    {\n"
            f'      "name": {json.dumps(sc["name"])},\n'
            f'      "chunk": {sc["chunk"]},\n'
            f'      "config": {json.dumps(sc["config"])},\n'
            f'      "steps": [\n{steps}\n      ],\n'
            f'      "expect": {{\n        "drains": [\n{drains}\n        ],\n'
            f'        "final": {json.dumps(rest)}\n      }}\n'
            "    }"
        )
    lines.append(",\n".join(blocks))
    lines += ["  ]", "}", ""]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    text = render(build())
    json.loads(text)  # the hand-rendered text must be valid JSON
    if "--check" in argv:
        return 0 if OUT.read_text(encoding="utf-8") == text else 1
    OUT.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
