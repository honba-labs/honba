"""The runner warm-up gate (``StrategyManifest.warmup_bars``).

During warm-up the strategy sees every bar (its indicators converge) but nothing it
submits reaches the execution port: intents are released in the context and recorded
as suppressed. The cross-language vectors live in ``schema/conformance/warmup_gate.json``
(``tests/integration/test_warmup_conformance.py``); these are the unit-level rules.
"""

from __future__ import annotations

import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.config import StrategyConfig
from honba.strategies.runner import StrategyRunner

X = InstrumentId("X", "NSE")


def bar(ts: int) -> Bar:
    return Bar(X, ts, 10.0, 10.0, 10.0, 10.0, 1.0)


class BuyEveryBar(Strategy):
    name = "every"

    def __init__(self) -> None:
        self.seen = 0

    def on_bar(self, bar: Bar) -> None:
        self.seen += 1
        self.ctx.submit(OrderIntent.market_buy(X, 1))


class Port:
    def __init__(self) -> None:
        self.orders: list[tuple[str, int]] = []

    def submit(self, order_id: str, intent: OrderIntent, ts: int) -> None:
        self.orders.append((order_id, ts))

    def drain_fills(self) -> list[Trade]:
        return []


def test_warmup_bars_reach_the_strategy_but_no_order_reaches_the_port() -> None:
    strategy, port = BuyEveryBar(), Port()
    runner = StrategyRunner(strategy, port, warmup_bars=2)
    result = runner.run([(bar(t), t) for t in (1, 2, 3)])
    assert strategy.seen == 3
    assert port.orders == [("every-0", 3)]
    assert [(s.ts_init, s.intent.quantity) for s in result.suppressed] == [(1, 1), (2, 1)]
    assert result.rejections == []


def test_suppressed_intents_are_released_so_nothing_stays_busy() -> None:
    runner = StrategyRunner(BuyEveryBar(), Port(), warmup_bars=1)
    runner.start()
    runner.on_event(bar(1), 1)
    assert not runner.ctx.busy(X)
    assert runner.warming_up
    runner.on_event(bar(2), 2)
    assert runner.ctx.busy(X)
    assert not runner.warming_up


def test_invalid_intents_are_still_rejected_during_warmup() -> None:
    class Bad(Strategy):
        name = "bad"

        def on_bar(self, bar: Bar) -> None:
            intent = OrderIntent.market_buy(X, 1)
            object.__setattr__(intent, "quantity", -1.0)
            self.ctx.submit(intent)

    result = StrategyRunner(Bad(), Port(), warmup_bars=5).run([(bar(1), 1)])
    assert len(result.rejections) == 1 and result.suppressed == []


def test_warmup_defaults_to_the_strategy_class_attribute() -> None:
    class Slow(BuyEveryBar):
        name = "slow"
        warmup_bars = 2

    assert StrategyRunner(Slow(), Port()).warmup_bars == 2
    assert StrategyRunner(Slow(), Port(), warmup_bars=0).warmup_bars == 0
    assert StrategyRunner(BuyEveryBar(), Port()).warmup_bars == 0


@pytest.mark.parametrize("bad", [-1, 1.5, True, "3"])
def test_warmup_must_be_a_non_negative_int(bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        StrategyRunner(BuyEveryBar(), Port(), warmup_bars=bad)  # type: ignore[arg-type]


def test_config_carries_warmup_bars(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('name = "s"\nsymbol = "X"\nwarmup_bars = 30\n')
    assert StrategyConfig.from_toml(path).warmup_bars == 30
    assert StrategyConfig(name="s", symbol="X").warmup_bars == 0
    with pytest.raises(ValueError):
        StrategyConfig(name="s", symbol="X", warmup_bars=-1)
