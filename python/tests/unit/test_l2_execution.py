"""``L2Execution`` / ``make_simulator(fill="l2")``: wiring, backend selection, the port surface."""

from __future__ import annotations

import pytest

from honba.backtest import L2Execution, TopOfBook, make_simulator
from honba.backtest.l2_sim import resolve_l2_backend
from honba.backtest.opening_auction import OpeningAuction
from honba.domain.money import Currency, Money
from honba.domain.tick import AggressorSide, QuoteTick, TradeTick
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderType, TimeInForce
from honba.strategies.execution import (
    Cancelled,
    CancelRequested,
    Expired,
    Fill,
    Rejected,
    cancel_order,
)

AAA = InstrumentId("AAA", "NSE")
CASH = Money.from_minor(1_000_000, Currency.INR)


def _have_native() -> bool:
    try:
        return resolve_l2_backend("native") == "native"
    except (ImportError, RuntimeError):
        return False


BACKENDS = [
    "python",
    pytest.param(
        "native", marks=pytest.mark.skipif(not _have_native(), reason="native extension not built")
    ),
]


def _quote(ts: int, bid: float = 99.0, ask: float = 101.0, size: float = 10) -> QuoteTick:
    return QuoteTick(AAA, ts, bid, ask, size, size)


@pytest.mark.parametrize("backend", BACKENDS)
def test_trailing_stop_is_rejected_unsupported(backend: str) -> None:
    port = L2Execution(cash=CASH, backend=backend)  # type: ignore[arg-type]
    intent = OrderIntent.trailing_stop_sell(AAA, 1, trail_amount=1.0)
    port.submit("T", intent, 5)
    (ev,) = port.drain_events()
    assert isinstance(ev, Rejected)
    assert (ev.order_id, ev.reason, ev.ts, ev.intent) == ("T", "unsupported_order_type", 5, intent)
    assert port.working_orders == []


@pytest.mark.parametrize("backend", BACKENDS)
def test_market_buy_fills_and_the_legacy_shim_agrees(backend: str) -> None:
    port = L2Execution(cash=CASH, backend=backend)  # type: ignore[arg-type]
    port.on_event(_quote(1), 1)
    port.submit("B", OrderIntent.market_buy(AAA, 4), 2)
    fills = port.drain_fills()
    assert [(t.order_id, t.quantity, t.price, t.ts) for t in fills] == [("B", 4, 101.0, 2)]
    assert port.cash == Money.from_minor(1_000_000 - 40_400, Currency.INR)
    assert dict(port.positions) == {AAA: 4}
    assert port.book(AAA) == TopOfBook(99.0, 101.0, 10, 6)


@pytest.mark.parametrize("backend", BACKENDS)
def test_cancel_emits_requested_then_cancelled_both_stamped_now(backend: str) -> None:
    port = L2Execution(cash=CASH, backend=backend)  # type: ignore[arg-type]
    port.on_event(_quote(1), 1)
    port.submit("L", OrderIntent.limit_buy(AAA, 3, 90.0), 2)
    assert port.working_orders == ["L"]
    assert cancel_order(port, "L", 77)
    req, done = port.drain_events()
    assert req == CancelRequested("L", 77)
    assert isinstance(done, Cancelled) and (done.order_id, done.ts) == ("L", 77)
    assert port.working_orders == []
    port.cancel("unknown", 80)  # a no-op
    assert port.drain_events() == []


@pytest.mark.parametrize("backend", BACKENDS)
def test_day_expiry_gtc_survives_and_trades_trigger_resting_fills(backend: str) -> None:
    port = L2Execution(cash=CASH, backend=backend)  # type: ignore[arg-type]
    port.on_event(_quote(1), 1)
    port.submit("D", OrderIntent.limit_buy(AAA, 2, 95.0), 2)
    gtc = OrderIntent(AAA, OrderSide.BUY, 2, OrderType.LIMIT, 95.0, TimeInForce.GTC)
    port.submit("G", gtc, 2)
    port.advance_to(86_340_000_000_000)
    (ev,) = port.drain_events()
    assert isinstance(ev, Expired) and (ev.order_id, ev.ts) == ("D", 86_340_000_000_000)
    port.on_event(TradeTick(AAA, 86_340_000_000_001, 94.0, 5, AggressorSide.SELLER, "t"), 0)
    (fill,) = port.drain_events()
    assert isinstance(fill, Fill) and fill.trade.price == 95.0 and fill.complete


@pytest.mark.parametrize("backend", BACKENDS)
def test_duplicate_working_id_and_sideless_orders_are_value_errors(backend: str) -> None:
    port = L2Execution(cash=CASH, backend=backend)  # type: ignore[arg-type]
    port.submit("L", OrderIntent.limit_buy(AAA, 3, 90.0), 2)
    with pytest.raises(ValueError, match="already working"):
        port.submit("L", OrderIntent.limit_buy(AAA, 3, 90.0), 3)
    with pytest.raises(ValueError):
        port.set_lot_size(AAA, 0.0)
    with pytest.raises(ValueError):
        L2Execution(cash=Money.from_minor(-1, Currency.INR), backend=backend)  # type: ignore[arg-type]


def test_backend_selection_follows_the_argument_then_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HONBA_SIM_BACKEND", raising=False)
    assert L2Execution(cash=CASH, backend="python").backend == "python"
    monkeypatch.setenv("HONBA_SIM_BACKEND", "python")
    assert L2Execution(cash=CASH).backend == "python"
    with pytest.raises(ValueError, match="backend must be"):
        L2Execution(cash=CASH, backend="gpu")  # type: ignore[arg-type]


def test_make_simulator_l2_builds_an_l2_port_and_refuses_l1_only_models() -> None:
    port = make_simulator(fill="l2", cash=CASH, costs="india.equity")
    assert isinstance(port, L2Execution)
    with pytest.raises(ValueError, match="impact"):
        make_simulator(fill="l2", cash=CASH, impact=object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="auction"):
        make_simulator(fill="l2", cash=CASH, auction=OpeningAuction(spread_bps=5.0))
    with pytest.raises(ValueError, match="expected 'next_open', 'l2' or 'bar_close'"):
        make_simulator(fill="nope", cash=CASH)  # type: ignore[arg-type]


@pytest.mark.skipif(not _have_native(), reason="native extension not built")
def test_the_binding_rejects_bad_ticks_with_value_error() -> None:
    from honba import _honba

    sim = _honba.L2Simulator(1_000)
    q = {"symbol": "AAA", "ts": 1, "bid": 11.0, "ask": 10.0, "bid_size": 1, "ask_size": 1}
    with pytest.raises(ValueError, match="bid_price must be <= ask_price"):
        sim.on_quote(q)
    with pytest.raises(ValueError, match="sizes"):
        sim.on_quote({**q, "bid": 9.0, "ask_size": -1})
    with pytest.raises(ValueError, match="finite"):
        sim.on_trade({"symbol": "AAA", "ts": 1, "price": float("nan"), "size": 1})
    with pytest.raises(ValueError, match="time in force"):
        sim.submit({"id": "x", "symbol": "AAA", "side": "buy", "qty": 1, "ts": 1, "tif": "x"})
    with pytest.raises(TypeError):
        _honba.L2Simulator(1_000, costs=3)  # type: ignore[arg-type]
    assert sim.book("AAA") is None
