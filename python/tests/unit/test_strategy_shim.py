"""Compatibility shim for pre-ADR-008 ``Strategy`` subclasses (one minor version)."""

import warnings

import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent, OrderSide
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext, StrategyContext
from honba.strategies.runner import StrategyRunner
from honba.strategies.testing import BarCloseFills, replay

X = InstrumentId("X", "NSE")


def bar(close: float, ts: int) -> Bar:
    return Bar(X, ts, close, close, close, close, 1.0)


class LegacyNoSuper(Strategy):
    """Old style: own __init__ that never calls super().__init__()."""

    name = "legacy_no_super"

    def __init__(self, qty: float) -> None:
        self.qty = qty

    def on_bar(self, bar: Bar) -> None:
        if self.position(bar.instrument_id) == 0 and not self.busy(bar.instrument_id):
            self.buy(bar.instrument_id, self.qty)


class LegacyWithSuper(LegacyNoSuper):
    name = "legacy_with_super"

    def __init__(self, qty: float) -> None:
        super().__init__(qty)
        super(LegacyNoSuper, self).__init__()


def test_legacy_subclasses_define_without_warnings():
    with warnings.catch_warnings():
        warnings.simplefilter("error")

        class Fresh(Strategy):
            name = "fresh"

            def on_bar(self, bar: Bar) -> None: ...

            def on_fill(self, fill: Trade) -> None: ...

        Fresh()


@pytest.mark.parametrize("cls", [LegacyNoSuper, LegacyWithSuper])
def test_legacy_subclass_gets_a_default_context_and_helpers_delegate(cls):
    s = cls(4)
    assert isinstance(s.ctx, LedgerContext)
    s.on_bar(bar(10.0, 1))
    assert s.busy(X) and s.ctx.busy(X)
    assert s.drain_intents() == [OrderIntent.market_buy(X, 4)]
    s.handle_fill(Trade(X, OrderSide.BUY, 4, 10.0))
    assert s.position(X) == s.ctx.position(X) == 4.0
    assert not s.busy(X)


@pytest.mark.parametrize("cls", [LegacyNoSuper, LegacyWithSuper])
def test_legacy_subclass_runs_identically_under_replay_and_the_runner(cls):
    bars = [bar(10.0, 1), bar(11.0, 2), bar(12.0, 3)]
    legacy = replay(cls(2), bars)
    result = StrategyRunner(cls(2), BarCloseFills()).run([(b, b.ts) for b in bars])
    assert [(f.side, f.quantity, f.price, f.ts) for f in legacy.fills] == [
        (f.side, f.quantity, f.price, f.ts) for f in result.fills
    ]


def test_replay_advances_the_context_clock():
    seen = []

    class Clocked(Strategy):
        name = "clocked"

        def on_bar(self, bar: Bar) -> None:
            seen.append(self.ctx.now())

    replay(Clocked(), [bar(1.0, 7), bar(1.0, 9)])
    assert seen == [7, 9]


def test_bind_replaces_the_context():
    class Spy(StrategyContext):
        def __init__(self):
            self.submitted = []

        def now(self):
            return 0

        def position(self, instrument_id):
            return 0.0

        def positions(self):
            return {}

        def cash(self):
            return 0.0

        def busy(self, instrument_id):
            return False

        def instrument(self, instrument_id):
            return None

        def submit(self, intent):
            self.submitted.append(intent)

    s = LegacyNoSuper(1)
    spy = Spy()
    s.bind(spy)
    assert s.ctx is spy
    s.on_bar(bar(1.0, 1))
    assert spy.submitted == [OrderIntent.market_buy(X, 1)]
    with pytest.raises(TypeError):
        s.drain_intents()  # legacy runner entry points need a LedgerContext


def test_bind_rejects_a_non_context():
    with pytest.raises(TypeError):
        LegacyNoSuper(1).bind(object())  # type: ignore[arg-type]


def test_overriding_a_legacy_runner_entry_point_warns_but_is_still_honoured():
    with pytest.warns(DeprecationWarning, match="handle_fill"):

        class CountsFills(LegacyNoSuper):
            name = "counts_fills"

            def __init__(self, qty: float) -> None:
                super().__init__(qty)
                self.seen = 0

            def handle_fill(self, fill: Trade) -> None:
                self.seen += 1
                super().handle_fill(fill)

    s = CountsFills(1)
    result = StrategyRunner(s, BarCloseFills()).run([(bar(5.0, 1), 1), (bar(6.0, 2), 2)])
    assert s.seen == len(result.fills) == 1
    assert s.position(X) == 1.0
