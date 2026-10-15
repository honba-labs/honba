"""OCO handling of DeclarativeStrategy protective orders (stop + take-profit)."""

from __future__ import annotations

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide, OrderType
from honba.entities.trade import Trade
from honba.strategies import DeclarativeStrategy, Entry
from honba.strategies.context import LedgerContext

IID = InstrumentId("NIFTY50", "NSE")


def bar(close: float = 100.0, ts: int = 1) -> Bar:
    return Bar(IID, ts, close, close, close, close, 1000.0)


class Rules(DeclarativeStrategy):
    name = "oco_rules"

    def __init__(self, entry=None, exit_=False, oco=None):
        self.entry = entry or Entry(10, stop_loss=95, take_profit=110)
        self.exit_ = exit_
        if oco is not None:
            self.oco = oco

    def should_long(self, bar):
        return True

    def should_exit(self, bar):
        return self.exit_

    def go_long(self, bar):
        return self.entry


class Desk:
    """Plays the runner: order ids, fills, and booking cancels back as releases."""

    def __init__(self, strategy: Rules):
        self.s = strategy
        self.ctx = LedgerContext()
        strategy.bind(self.ctx)
        self.n = 0
        self.orders: dict[str, OrderIntent] = {}
        self.filled: dict[str, float] = {}

    def send(self) -> dict[str, OrderIntent]:
        sent = {}
        for intent in self.s.drain_intents():
            oid = f"t-{self.n}"
            self.n += 1
            self.ctx.note_order(oid, intent)
            self.orders[oid] = intent
            sent[oid] = intent
        return sent

    def fill(self, oid: str, qty: float | None = None, price: float = 100.0) -> None:
        intent = self.orders[oid]
        q = intent.quantity if qty is None else qty
        self.filled[oid] = self.filled.get(oid, 0.0) + q
        if self.filled[oid] + 1e-9 >= intent.quantity:
            self.ctx.note_closed(oid)
        self.s.handle_fill(Trade(IID, intent.side, q, price, 1, oid))

    def cancels(self) -> list[str]:
        """Cancels the strategy asked for; the port honours each one (released)."""
        ids = self.ctx.drain_cancels()
        for oid in ids:
            self.s.handle_rejected(self.orders[oid])
            self.ctx.note_closed(oid)
        return ids

    def entered(self, entry_qty: float = 10) -> tuple[str, str]:
        """Entry submitted and filled; returns (stop id, take-profit id) once sent."""
        self.s.on_bar(bar())
        (entry_id,) = self.send()
        self.fill(entry_id, entry_qty)
        stop_id, take_id = self.send()
        return stop_id, take_id


def test_stop_fill_cancels_take_profit():
    d = Desk(Rules())
    stop, take = d.entered()
    d.fill(stop, price=95)
    assert d.cancels() == [take]


def test_take_profit_fill_cancels_stop():
    d = Desk(Rules())
    stop, take = d.entered()
    d.fill(take, price=110)
    assert d.cancels() == [stop]


def test_short_side_group_cancels_sibling():
    class Short(Rules):
        def should_long(self, bar):
            return False

        def should_short(self, bar):
            return True

        def go_short(self, bar):
            return Entry(4, stop_loss=105, take_profit=90)

    d = Desk(Short())
    d.s.on_bar(bar())
    (entry_id,) = d.send()
    d.fill(entry_id)
    stop, take = d.send()
    d.fill(take, price=90)
    assert d.cancels() == [stop]


def test_cancel_is_idempotent_and_ignores_unknown_or_terminal():
    d = Desk(Rules())
    stop, take = d.entered()
    d.fill(stop, price=95)
    assert d.cancels() == [take]
    d.ctx.cancel(d.orders[take])  # already cancelled
    d.ctx.cancel(d.orders[stop])  # filled
    d.ctx.cancel(OrderIntent.market_sell(IID, 1))  # never submitted
    assert d.ctx.drain_cancels() == []


def test_unknown_intent_cancel_is_a_noop():
    ctx = LedgerContext()
    ctx.cancel(OrderIntent.market_sell(IID, 1))
    assert ctx.drain_cancels() == []


def test_cancel_of_unsent_outbox_intent_withdraws_it():
    ctx = LedgerContext()
    intent = OrderIntent.limit_sell(IID, 3, 110)
    ctx.submit(intent)
    assert ctx.busy(IID)
    ctx.cancel(intent)
    assert ctx.drain_intents() == []
    assert not ctx.busy(IID)
    assert ctx.drain_cancels() == []


def test_manual_exit_cancels_both_and_sells():
    s = Rules(exit_=True)
    d = Desk(s)
    stop, take = d.entered()
    s.on_bar(bar(101.0, 2))  # protectives rest, yet should_exit is consulted
    assert d.cancels() == [stop, take]
    ((_, sell),) = d.send().items()
    assert (sell.side, sell.order_type, sell.quantity) == (OrderSide.SELL, OrderType.MARKET, 10)


def test_no_second_exit_while_exit_order_in_flight():
    s = Rules(exit_=True)
    d = Desk(s)
    d.entered()
    s.on_bar(bar(101.0, 2))
    d.cancels()
    d.send()
    s.on_bar(bar(101.0, 3))  # market exit still unfilled -> busy
    assert d.send() == {}
    assert d.cancels() == []


def test_not_busy_after_siblings_cancelled_and_flat():
    d = Desk(Rules())
    stop, _take = d.entered()
    d.fill(stop, price=95)
    d.cancels()
    assert not d.s.busy(IID)
    assert d.s.position(IID) == 0


def test_should_exit_not_blocked_by_resting_protectives_only():
    s = Rules()
    d = Desk(s)
    d.entered()
    assert s.busy(IID)  # the ledger still sees them
    s.exit_ = True
    s.on_bar(bar(101.0, 2))
    assert d.ctx.drain_cancels() != []


def test_entry_in_flight_still_blocks():
    s = Rules(exit_=True)
    d = Desk(s)
    s.on_bar(bar())
    d.send()
    s.on_bar(bar(100.0, 2))
    assert d.send() == {}


def test_partial_stop_fill_keeps_sibling_until_full():
    d = Desk(Rules())
    stop, take = d.entered()
    d.fill(stop, qty=4, price=95)
    assert d.cancels() == []
    d.fill(stop, qty=6, price=95)
    assert d.cancels() == [take]


def test_partial_entry_fills_make_independent_groups():
    d = Desk(Rules())
    d.s.on_bar(bar())
    (entry_id,) = d.send()
    d.fill(entry_id, 6)
    stop1, take1 = d.send()
    d.fill(entry_id, 4)
    stop2, take2 = d.send()
    d.fill(stop1, price=95)
    assert d.cancels() == [take1]  # the 4 lots stay protected
    d.fill(take2, price=110)
    assert d.cancels() == [stop2]


def test_single_protective_has_no_sibling_and_exit_is_consulted():
    s = Rules(entry=Entry(10, stop_loss=95), exit_=True)
    d = Desk(s)
    s.on_bar(bar())
    (entry_id,) = d.send()
    d.fill(entry_id)
    (stop,) = d.send()
    s.on_bar(bar(101.0, 2))
    assert d.cancels() == [stop]


def test_oco_false_keeps_legacy_behaviour():
    s = Rules(exit_=True, oco=False)
    d = Desk(s)
    stop, _take = d.entered()
    s.on_bar(bar(101.0, 2))  # blocked by resting protectives
    assert d.send() == {}
    d.fill(stop, price=95)
    assert d.ctx.drain_cancels() == []


def test_fill_without_order_id_is_ignored():
    d = Desk(Rules())
    d.entered()
    d.s.handle_fill(Trade(IID, OrderSide.SELL, 3, 95.0, 1))
    assert d.ctx.drain_cancels() == []


def test_closing_position_by_other_means_cancels_leftovers():
    d = Desk(Rules())
    stop, take = d.entered()
    d.s.handle_fill(Trade(IID, OrderSide.SELL, 10, 101.0, 1, "elsewhere"))  # flat
    assert d.cancels() == [stop, take]
