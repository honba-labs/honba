"""Unit tests for TradingState enforcement (E2-S1 rest).

Acceptance: `reducing_refuses_position_increasing_order`, `python_halt_blocks_orders_in_run`.
"""

from __future__ import annotations

from honba._honba import TradingState
from honba.entities.order import OrderSide
from honba.risk import RiskRefusal, check_state


class TestTradingStateTransitions:
    """Test TradingState.can_transition_to matrix."""

    def test_all_transitions_permitted(self) -> None:
        """All state transitions are currently permitted (permissive by design)."""
        states = [TradingState.ACTIVE, TradingState.REDUCING, TradingState.HALTED]
        for from_state in states:
            for to_state in states:
                assert from_state.can_transition_to(to_state)


class TestReducingEnforcement:
    """Test that Reducing state rejects position-increasing orders."""

    def test_reducing_allows_reducing_buy_when_long(self) -> None:
        """Long position: buy is refused, sell is allowed."""
        refusal = check_state(
            side=OrderSide.BUY,
            quantity=1.0,
            position=10.0,  # long 10
            trading_state=TradingState.REDUCING,
        )
        assert refusal is not None
        assert isinstance(refusal, RiskRefusal)
        assert refusal.code == "risk_reduce_only_violation"

    def test_reducing_allows_reducing_sell_when_long(self) -> None:
        """Long position: sell reduces position, so allowed."""
        refusal = check_state(
            side=OrderSide.SELL,
            quantity=5.0,
            position=10.0,  # long 10
            trading_state=TradingState.REDUCING,
        )
        assert refusal is None

    def test_reducing_refuses_sell_when_short(self) -> None:
        """Short position: sell increases position, so refused."""
        refusal = check_state(
            side=OrderSide.SELL,
            quantity=5.0,
            position=-10.0,  # short 10
            trading_state=TradingState.REDUCING,
        )
        assert refusal is not None
        assert isinstance(refusal, RiskRefusal)
        assert refusal.code == "risk_reduce_only_violation"

    def test_reducing_allows_buy_when_short(self) -> None:
        """Short position: buy reduces position, so allowed."""
        refusal = check_state(
            side=OrderSide.BUY,
            quantity=5.0,
            position=-10.0,  # short 10
            trading_state=TradingState.REDUCING,
        )
        assert refusal is None

    def test_reducing_refuses_flat_position(self) -> None:
        """Flat position: any order is refused."""
        for side in (OrderSide.BUY, OrderSide.SELL):
            refusal = check_state(
                side=side,
                quantity=1.0,
                position=0.0,
                trading_state=TradingState.REDUCING,
            )
            assert refusal is not None
            assert isinstance(refusal, RiskRefusal)
            assert refusal.code == "risk_reduce_only_violation"


class TestHaltedEnforcement:
    """Test that Halted state rejects all orders."""

    def test_halted_refuses_all_orders(self) -> None:
        for side in (OrderSide.BUY, OrderSide.SELL):
            refusal = check_state(
                side=side,
                quantity=1.0,
                position=0.0,
                trading_state=TradingState.HALTED,
            )
            assert refusal is not None
            assert isinstance(refusal, RiskRefusal)
            assert refusal.code == "risk_trading_halted"


class TestActiveEnforcement:
    """Test that Active state allows all orders (state check passes)."""

    def test_active_allows_all_orders(self) -> None:
        for side in (OrderSide.BUY, OrderSide.SELL):
            refusal = check_state(
                side=side,
                quantity=1.0,
                position=0.0,
                trading_state=TradingState.ACTIVE,
            )
            assert refusal is None


class TestPythonHaltBlocksOrdersInRun:
    """Test that HALTED trading state blocks orders in a Python runner."""

    def test_halted_state_blocks_order_submission(self) -> None:
        from honba._honba import TradingState
        from honba.entities.bar import Bar
        from honba.entities.instrument import InstrumentId
        from honba.strategies.base import Strategy
        from honba.strategies.runner import StrategyRunner
        from honba.strategies.testing import BarCloseFills

        class BuyStrategy(Strategy):
            name = "buy_strategy"

            def on_bar(self, bar: Bar) -> None:
                if not self.busy(bar.instrument_id):
                    self.buy(bar.instrument_id, 1.0)

        instrument = InstrumentId("TEST", "NSE")
        bars = [
            Bar(instrument, 1_000_000_000, 100.0, 101.0, 99.0, 100.0, 1000.0),
            Bar(instrument, 2_000_000_000, 100.0, 101.0, 99.0, 100.0, 1000.0),
        ]

        # Test with HALTED state
        runner = StrategyRunner(
            BuyStrategy(),
            BarCloseFills(),
        )
        runner._trading_state = TradingState.HALTED
        runner.run([(b, b.ts) for b in bars])

        # Should have no intents because HALTED blocks orders
        assert len(runner.intents) == 0


class TestPythonTradingStateExposure:
    """Test that TradingState is exposed in Python."""

    def test_trading_state_enum_accessible(self) -> None:
        assert TradingState.ACTIVE is not None
        assert TradingState.REDUCING is not None
        assert TradingState.HALTED is not None

    def test_trading_state_str_representation(self) -> None:
        assert str(TradingState.ACTIVE) == "active"
        assert str(TradingState.REDUCING) == "reducing"
        assert str(TradingState.HALTED) == "halted"

    def test_trading_state_can_transition_to(self) -> None:
        # All transitions currently permitted
        assert TradingState.ACTIVE.can_transition_to(TradingState.REDUCING)
        assert TradingState.ACTIVE.can_transition_to(TradingState.HALTED)
        assert TradingState.REDUCING.can_transition_to(TradingState.ACTIVE)
        assert TradingState.HALTED.can_transition_to(TradingState.ACTIVE)
