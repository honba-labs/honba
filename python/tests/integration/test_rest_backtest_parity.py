"""``rest_backtest_roundtrip_matches_session`` (ROADMAP E4-S3, ADR 0017 decision 6).

The same seeded bars go through the Rust run service (``POST /backtests`` -> poll -> journal)
and through the Python ``BacktestSession``. Parity is defined on the **Rust-registered strategy
subset** only: every other strategy is 422 server-side (asserted below), and ``rsi_reversal``
has no Python twin in ``honba.strategies.reference`` yet, so it is run through REST but has no
session to compare with (skipped parity case, stated, not hidden).

What must agree, exactly: the fills (side, quantity, price, timestamp), the final position and
the closed-round-trip count. For the Python session the equivalent execution is pinned:
``fill="bar_close"`` (the Rust ``bar_fill`` model: fill at the decision bar's close), no costs
(Rust does not model them) and an explicit zero settlement lag.

The metrics are computed differently and are NOT expected to be equal. The documented
differences, asserted below so a change to either side is noticed:

* Rust ``net_pnl`` / ``total_return`` count **closed round trips only** (fills paired two at a
  time, a trailing open fill dropped; no mark-to-market). Session ``final_equity`` marks the
  open position at the last close. They differ by exactly the open leg's unrealised P&L.
* Rust ``total_return`` is a fraction of initial capital; session ``total_return_pct`` is a
  percentage, so ``total_return_pct == 100 * (final_equity - cash) / cash`` includes the
  unrealised leg.
* Rust ``max_drawdown`` is taken over the round-trip equity series; session
  ``max_drawdown_pct`` over the per-bar mark-to-market curve. Different series, different value.
* Rust ``sharpe`` is annualised at 252 periods over round-trip returns; the session has no
  Sharpe at all.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from _runs_support import END, START, TCS, RowsProvider, open_clients, scratch, walk, write_bars

from honba.client import BacktestResult, Client, ValidationApiError
from honba.session import BacktestResult as SessionResult
from honba.session import Honba
from honba.strategies.reference import BuyAndHold, SmaCrossover

pytest.importorskip("honba._honba")

CASH = 1_000_000.0
QUANTITY = 10.0  # the registry's fixed order size until strategy parameters are on the wire
ROWS = walk(300)
PYTHON_TWINS = {
    "buy_and_hold": lambda: BuyAndHold(TCS, QUANTITY),
    "sma_crossover": lambda: SmaCrossover(TCS, 3, 8, QUANTITY),  # the registry's (3, 8)
}


@pytest.fixture(scope="module")
def clients() -> Iterator[dict[str, Client]]:
    with scratch("rest-parity") as root:
        data = root / "bars"
        data.mkdir()
        write_bars(data, "TCS", ROWS)
        with open_clients(data, root / "journals") as found:
            yield found


@pytest.fixture(params=["inproc", "http"])
def client(clients: dict[str, Client], request: pytest.FixtureRequest) -> Client:
    if request.param not in clients:
        pytest.skip("honba binary not built (cargo build -p honba-cli, or set HONBA_BIN)")
    return clients[request.param]


def via_rest(client: Client, name: str) -> tuple[BacktestResult, list]:
    result = client.run_backtest(
        strategy=name, universe="TCS.NSE", start=START, end=END, seed=1, bar_spec="1m",
        initial_capital=CASH, timeout=60,
    )  # fmt: skip
    assert result.status == "completed", result
    return result, client.backtest_journal(result.run_id)


def via_session(name: str) -> SessionResult:
    return Honba.backtest(
        PYTHON_TWINS[name](), symbol="TCS", start=START, end=END, timeframe="1m", cash=CASH,
        costs="none", fill="bar_close", data=RowsProvider(ROWS), settlement_days=0,
    ).run()  # fmt: skip


def closed_pnl(fills: list[tuple[str, float, float]]) -> float:
    """Net P&L of fills paired two at a time (entry, exit); a trailing open fill is dropped.
    This is the Rust definition, recomputed independently from the fills."""
    total = 0.0
    for entry, exit_ in zip(fills[0::2], fills[1::2], strict=False):
        sign = 1.0 if entry[0] == "buy" else -1.0
        total += sign * entry[1] * (exit_[2] - entry[2])
    return total


def side(value: object) -> str:
    return getattr(value, "value", str(value)).lower()


@pytest.mark.parametrize("name", sorted(PYTHON_TWINS))
def test_rest_backtest_roundtrip_matches_session(client: Client, name: str) -> None:
    rest, journal = via_rest(client, name)
    session = via_session(name)

    # -- must agree exactly -----------------------------------------------------------------
    assert len(journal) == len(session.fills)
    for got, want in zip(journal, session.fills, strict=True):
        assert side(got.side) == side(want.side)
        assert got.quantity == pytest.approx(want.quantity)
        assert got.price == pytest.approx(want.price, abs=1e-9)
        assert got.ts_event.to_ns() == want.ts
    position = sum((f.quantity if side(f.side) == "buy" else -f.quantity) for f in journal)
    assert position == pytest.approx(session.ctx.position(TCS))
    assert rest.metrics is not None
    assert rest.metrics.trades == int(session.metrics["n_trades"])

    # -- documented metric differences ------------------------------------------------------
    fills = [(side(f.side), f.quantity, f.price) for f in journal]
    assert rest.metrics.net_pnl == pytest.approx(closed_pnl(fills), abs=1e-6)
    assert rest.metrics.total_return == pytest.approx(rest.metrics.net_pnl / CASH)
    last_close = ROWS[-1][4]
    unrealised = (
        0.0
        if len(fills) % 2 == 0
        else (1.0 if fills[-1][0] == "buy" else -1.0) * fills[-1][1] * (last_close - fills[-1][2])
    )
    marked = session.metrics["final_equity"] - CASH
    assert marked == pytest.approx(rest.metrics.net_pnl + unrealised, abs=1e-6)
    assert session.metrics["total_return_pct"] == pytest.approx(100.0 * marked / CASH)
    assert "sharpe" not in session.metrics
    assert rest.assumptions is not None
    assert rest.assumptions.metrics_basis == "closed_round_trips"
    assert rest.assumptions.periods_per_year == 252.0
    assert "mark_to_market_of_open_positions" in rest.assumptions.not_modelled
    assert "transaction_costs" in rest.assumptions.not_modelled


def test_buy_and_hold_shows_the_closed_trip_difference_plainly(client: Client) -> None:
    """One open buy: Rust reports no trade and zero P&L, the session marks it to market."""
    rest, _ = via_rest(client, "buy_and_hold")
    session = via_session("buy_and_hold")
    assert rest.metrics is not None
    assert (rest.metrics.trades, rest.metrics.net_pnl, rest.metrics.total_return) == (0, 0.0, 0.0)
    assert session.metrics["final_equity"] != CASH
    assert session.metrics["n_trades"] == 0.0


@pytest.mark.skip(
    reason="rsi_reversal is Rust-registered but has no Python twin in "
    "honba.strategies.reference; parity is undefined until one is added (E4-S3 follow-up)"
)
def test_rsi_reversal_matches_session() -> None: ...


def test_rsi_reversal_runs_through_rest_deterministically(client: Client) -> None:
    first, journal = via_rest(client, "rsi_reversal")
    second, again = via_rest(client, "rsi_reversal")
    assert first.model_dump(exclude={"run_id"}) == second.model_dump(exclude={"run_id"})
    assert journal == again


def test_strategies_outside_the_rust_registry_are_422(client: Client) -> None:
    for name in ("momentum_alpha", "ContractProbe", "sha256:" + "0" * 64):
        with pytest.raises(ValidationApiError) as caught:
            client.submit_backtest(strategy=name, universe="TCS.NSE", start=START, end=END, seed=1)
        assert caught.value.status == 422
        assert caught.value.context["field"] == "strategy"
