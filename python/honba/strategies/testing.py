"""Minimal deterministic replay harness for strategy tests.

Market intents fill at the close of the bar that produced them, with no costs.
Use ``honba.backtest`` for realistic simulation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from honba.entities.bar import Bar
from honba.entities.order import OrderIntent
from honba.entities.trade import Trade
from honba.strategies.base import Strategy


@dataclass
class ReplayResult:
    intents: list[OrderIntent] = field(default_factory=list)
    fills: list[Trade] = field(default_factory=list)


def replay(strategy: Strategy, bars: Iterable[Bar], fill_delay: int = 0) -> ReplayResult:
    """Replays ``bars``. ``fill_delay=0`` fills at the emitting bar's close;
    ``fill_delay=n`` fills at the open of the bar ``n`` bars later, like a live runner."""
    result = ReplayResult()
    queue: list[tuple[int, OrderIntent]] = []  # (due bar index, intent)
    strategy.on_start()
    for i, bar in enumerate(bars):
        due = [q for q in queue if q[0] <= i]
        queue = [q for q in queue if q[0] > i]
        for _, intent in due:
            _fill(strategy, result, intent, bar.open, bar.ts)
        strategy.on_bar(bar)
        for intent in strategy.drain_intents():
            result.intents.append(intent)
            if fill_delay == 0:
                _fill(strategy, result, intent, bar.close, bar.ts)
            else:
                queue.append((i + fill_delay, intent))
    strategy.on_stop()
    return result


def _fill(strategy: Strategy, result: ReplayResult, intent: OrderIntent, price: float, ts: int) -> None:
    fill = Trade(intent.instrument_id, intent.side, intent.quantity, price, ts)
    result.fills.append(fill)
    strategy.handle_fill(fill)
