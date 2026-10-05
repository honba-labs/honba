"""``LedgerContext``: the reference ``StrategyContext`` (ADR 008)."""

import pytest

from honba.domain.money import Currency, Money
from honba.entities.instrument import Instrument, InstrumentId, InstrumentKind
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.trade import Trade
from honba.strategies.context import LedgerContext, StrategyContext

NIFTY = InstrumentId("NIFTY50", "NSE")
INFY = InstrumentId("INFY", "NSE")
ACME_BSE = InstrumentId("ACME", "BSE")
ACME_NSE = InstrumentId("ACME", "NSE")


def test_is_a_strategy_context_and_starts_empty():
    ctx = LedgerContext()
    assert isinstance(ctx, StrategyContext)
    assert (ctx.now(), ctx.cash(), ctx.positions()) == (0, Money(0, Currency.INR), {})
    assert ctx.position(NIFTY) == 0.0
    assert not ctx.busy(NIFTY)
    assert ctx.instrument(NIFTY) is None


def test_clock_is_set_by_the_runner():
    ctx = LedgerContext()
    ctx.set_now(1_700_000_000_000_000_000)
    assert ctx.now() == 1_700_000_000_000_000_000


def test_instrument_lookup():
    nifty = Instrument(NIFTY, InstrumentKind.INDEX, lot_size=75.0, tick_size=0.05)
    ctx = LedgerContext(instruments=[nifty])
    assert ctx.instrument(NIFTY) is nifty
    infy = Instrument(INFY, InstrumentKind.EQUITY, lot_size=1.0, tick_size=0.05)
    ctx.add_instrument(infy)
    assert ctx.instrument(INFY) is infy


def test_submit_queues_in_order_and_marks_busy_until_drained_and_filled():
    ctx = LedgerContext()
    first, second = OrderIntent.market_buy(NIFTY, 10), OrderIntent.limit_sell(INFY, 2, 1500.0)
    ctx.submit(first)
    ctx.submit(second)
    assert ctx.busy(NIFTY) and ctx.busy(INFY)
    assert ctx.drain_intents() == [first, second]
    assert ctx.drain_intents() == []
    assert ctx.busy(NIFTY)  # drained is not filled
    ctx.apply_fill(Trade(NIFTY, OrderSide.BUY, 4, 100.0))
    assert ctx.busy(NIFTY)  # partial fill
    ctx.apply_fill(Trade(NIFTY, OrderSide.BUY, 6, 100.0))
    assert not ctx.busy(NIFTY)


def test_release_clears_pending_for_a_rejected_intent():
    ctx = LedgerContext()
    intent = OrderIntent.market_sell(NIFTY, 5)
    ctx.submit(intent)
    ctx.release(intent)
    assert not ctx.busy(NIFTY)


def test_fills_move_position_and_cash_including_costs():
    # Integer minor units (ADR 0011); mirrors the Rust ledger test.
    ctx = LedgerContext(cash=1_000.0)
    ctx.apply_fill(Trade(NIFTY, OrderSide.BUY, 3, 100.0, costs=Money(150, Currency.INR)))
    assert ctx.position(NIFTY) == 3.0
    assert ctx.cash() == Money(100_000 - 30_000 - 150, Currency.INR)
    ctx.apply_fill(Trade(NIFTY, OrderSide.SELL, 5, 110.0, costs=Money(200, Currency.INR)))
    assert ctx.position(NIFTY) == -2.0  # signed: short 2
    assert ctx.cash() == Money(69_850 + 55_000 - 200, Currency.INR)


def test_cash_accepts_money_and_rejects_non_finite_major_units():
    assert LedgerContext(cash=Money(5, Currency.USD)).cash() == Money(5, Currency.USD)
    assert LedgerContext(cash=0.125).cash() == Money(13, Currency.INR)  # half away from zero
    with pytest.raises(ValueError):
        LedgerContext(cash=float("nan"))


def test_notional_rounds_once_per_fill_to_minor_units():
    ctx = LedgerContext(cash=Money(100_000, Currency.INR))
    ctx.apply_fill(Trade(NIFTY, OrderSide.BUY, 3, 33.333))
    assert ctx.cash() == Money(90_000, Currency.INR)


def test_a_fill_the_ledger_cannot_book_is_refused_whole():
    ctx = LedgerContext(cash=Money(100_000, Currency.INR))
    ctx.submit(OrderIntent.market_buy(NIFTY, 3))
    with pytest.raises(ValueError, match="currency"):
        ctx.apply_fill(Trade(NIFTY, OrderSide.BUY, 3, 100.0, costs=Money(150, Currency.USD)))
    with pytest.raises(ValueError):
        ctx.apply_fill(Trade(NIFTY, OrderSide.BUY, 1e12, 1e12))
    assert ctx.cash() == Money(100_000, Currency.INR)
    assert ctx.position(NIFTY) == 0.0
    assert ctx.busy(NIFTY)  # the refused fill did not fill the intent


def test_positions_lists_non_flat_ordered_by_symbol_then_exchange():
    ctx = LedgerContext()
    for iid in (NIFTY, ACME_NSE, INFY, ACME_BSE):
        ctx.apply_fill(Trade(iid, OrderSide.BUY, 1, 10.0))
    ctx.apply_fill(Trade(INFY, OrderSide.SELL, 1, 10.0))  # flat again
    assert list(ctx.positions().items()) == [(ACME_BSE, 1.0), (ACME_NSE, 1.0), (NIFTY, 1.0)]


def _invalid_intent(quantity: float) -> OrderIntent:
    """An intent whose invariants are broken after construction (``OrderIntent`` validates
    on construction, so this takes a deliberate bypass: a duck-typed or unpickled value)."""
    intent = OrderIntent.market_buy(NIFTY, 1.0)
    object.__setattr__(intent, "quantity", quantity)
    return intent


def test_invalid_intent_does_not_touch_pending_state():
    # Mirrors the Rust regression (commit 4c3a95a): a NaN quantity wiped other pending orders.
    ctx = LedgerContext()
    ctx.submit(OrderIntent.market_buy(NIFTY, 10.0))
    for bad in (float("nan"), float("inf"), float("-inf"), -1.0):
        intent = _invalid_intent(bad)
        ctx.submit(intent)
        assert ctx.busy(NIFTY), f"pending wiped by quantity {bad}"
        ctx.release(intent)  # the runner releases rejected intents
        assert ctx.busy(NIFTY), f"pending wiped by release of {bad}"
    ctx.apply_fill(Trade(NIFTY, OrderSide.BUY, 10, 1.0))
    assert not ctx.busy(NIFTY)
    assert len(ctx.drain_intents()) == 5  # invalid ones are still handed to the runner to reject
