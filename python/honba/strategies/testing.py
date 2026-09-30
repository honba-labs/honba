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


def replay(strategy: Strategy, bars: Iterable[Bar]) -> ReplayResult:
    result = ReplayResult()
    strategy.on_start()
    for bar in bars:
        strategy.on_bar(bar)
        for intent in strategy.drain_intents():
            result.intents.append(intent)
            fill = Trade(intent.instrument_id, intent.side, intent.quantity, bar.close, bar.ts)
            result.fills.append(fill)
            strategy.handle_fill(fill)
    strategy.on_stop()
    return result
