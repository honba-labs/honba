"""Look-ahead (non-causality) check for untrusted strategies (E5-S5).

Ports the Rust truncation property (``crates/honba-strategy/src/tests/look_ahead.rs``) to Python:
run the strategy over the full bar series and over every prefix, and require the prefix run's
decisions to be exactly the full run's decisions up to the prefix's last bar. A strategy that
peeked at a future bar emits a different decision on the shortest prefix that hides the bar it
peeked at.

``build`` receives the run's own bars, mirroring the Rust ``build(&bars[..prefix])`` shape. That is
what lets a vectorized strategy (one that precomputes over its input) be caught rather than being
causally vacuous: the verifier builds the strategy from the bars it is about to run on.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from honba.entities.bar import Bar
from honba.entities.order import OrderIntent
from honba.strategies.base import Strategy
from honba.strategies.testing import replay

__all__ = [
    "LookAheadReport",
    "LookAheadViolation",
    "check_look_ahead",
    "intent_key",
]

#: A decision is ``(event-time ns, canonical intent)``.
Decision = tuple[int, tuple[Any, ...]]


def intent_key(intent: OrderIntent) -> tuple[Any, ...]:
    """A hashable, comparable canonical form of an ``OrderIntent``."""
    instrument = intent.instrument_id
    return (
        instrument.symbol,
        instrument.exchange,
        intent.side.value,
        intent.order_type.value,
        float(intent.quantity),
        intent.price,
        getattr(intent.time_in_force, "value", None),
        intent.trigger_price,
        getattr(intent, "trail_amount", None),
        getattr(intent, "trail_percent", None),
    )


@dataclass(frozen=True, slots=True)
class LookAheadViolation:
    """A prefix whose run emitted different decisions than the full run's prefix of decisions."""

    prefix_len: int
    expected: tuple[Decision, ...]
    got: tuple[Decision, ...]

    def message(self) -> str:
        return (
            f"decisions up to bar {self.prefix_len} changed when the future was truncated "
            f"(expected {len(self.expected)}, got {len(self.got)})"
        )


@dataclass(frozen=True, slots=True)
class LookAheadReport:
    """Result of :func:`check_look_ahead`."""

    checked_prefixes: int
    violations: tuple[LookAheadViolation, ...]

    @property
    def ok(self) -> bool:
        return not self.violations


class _RecordingStrategy:
    """Duck-types the strategy surface ``replay`` uses and timestamps each submitted intent."""

    def __init__(self, inner: Strategy) -> None:
        self._inner = inner
        self._now = 0
        self.decisions: list[Decision] = []

    @property
    def ctx(self) -> Any:
        return self._inner.ctx

    def on_start(self) -> None:
        self._inner.on_start()

    def on_bar(self, bar: Bar) -> None:
        self._now = bar.ts
        self._inner.on_bar(bar)

    def drain_intents(self) -> list[OrderIntent]:
        intents = self._inner.drain_intents()
        for intent in intents:
            self.decisions.append((self._now, intent_key(intent)))
        return intents

    def handle_fill(self, fill: Any) -> None:
        self._inner.handle_fill(fill)

    def on_stop(self) -> None:
        self._inner.on_stop()


def _decisions(build: Callable[[Sequence[Bar]], Strategy], bars: list[Bar]) -> tuple[Decision, ...]:
    recorder = _RecordingStrategy(build(bars))
    replay(recorder, bars)  # type: ignore[arg-type]  # duck-types the replay surface
    return tuple(recorder.decisions)


def check_look_ahead(
    build: Callable[[Sequence[Bar]], Strategy], bars: Iterable[Bar]
) -> LookAheadReport:
    """Run ``build`` over the full series and every prefix; report causality violations.

    ``build`` is called once per prefix with the bars that run is over, so a strategy built from
    its own data (the vectorized look-ahead shape) sees only the bars it is run on.
    """
    series = list(bars)
    full = _decisions(build, series)
    violations: list[LookAheadViolation] = []
    for prefix_len in range(1, len(series) + 1):
        prefix = _decisions(build, series[:prefix_len])
        last_ts = series[prefix_len - 1].ts
        expected = tuple(decision for decision in full if decision[0] <= last_ts)
        if prefix != expected:
            violations.append(LookAheadViolation(prefix_len, expected, prefix))
    return LookAheadReport(len(series), tuple(violations))
