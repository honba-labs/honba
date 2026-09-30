"""Embedded honba simulation module.

Hierarchical like helm/gem5: every object is a SimObject attached to a parent.
The top-level `Sim` collects its children by type on `instantiate()` and runs
the event loop.
"""
from __future__ import annotations

import traceback
from typing import Any, Dict, List, Optional


# --------------------------------------------------------------------------
# Base
# --------------------------------------------------------------------------

class SimObject:
    def __init__(self, name: str = "", parent: Optional["SimObject"] = None):
        self.name = name
        self.parent = parent
        self.children: List[SimObject] = []
        if parent is not None:
            parent.children.append(self)

    def descendants(self):
        for c in self.children:
            yield c
            yield from c.descendants()

    def find(self, name: str):
        for c in self.descendants():
            if c.name == name:
                return c
        return None

    def __repr__(self):
        return f"<{type(self).__name__} {self.name!r}>"


# --------------------------------------------------------------------------
# Domain leaves
# --------------------------------------------------------------------------

class Venue(SimObject):
    def __init__(self, code: str, name: str = "", parent=None):
        super().__init__(name or code, parent)
        self.code = code


class Instrument(SimObject):
    def __init__(self, symbol: str, venue, kind: str = "equity", parent=None):
        super().__init__(symbol, parent)
        self.symbol = symbol
        self.venue = venue if isinstance(venue, Venue) else Venue(venue)
        self.kind = kind


class Bar(SimObject):
    def __init__(self, instrument, ts, open, high, low, close, volume=0.0, parent=None):
        super().__init__(f"bar@{ts}", parent)
        self.instrument = instrument
        self.ts = ts
        self.open, self.high, self.low, self.close = open, high, low, close
        self.volume = volume


class Tick(SimObject):
    def __init__(self, instrument, ts, price, size=0.0, parent=None):
        super().__init__(f"tick@{ts}", parent)
        self.instrument = instrument
        self.ts = ts
        self.price = price
        self.size = size


class Fill(SimObject):
    def __init__(self, instrument, ts, price, qty, side, parent=None):
        super().__init__(f"fill@{ts}", parent)
        self.instrument = instrument
        self.ts = ts
        self.price = price
        self.qty = qty
        self.side = side


class Calendar(SimObject):
    """Trading calendar. Populate with holidays, or leave for 24/7 markets."""
    def __init__(self, name: str = "calendar", holidays=None, parent=None):
        super().__init__(name, parent)
        self.holidays = set(holidays or [])

    def is_trading_day(self, d) -> bool:
        return d.weekday() < 5 and d not in self.holidays


class CostModel(SimObject):
    """Brokerage + STT + exchange fees. Flat-rate default."""
    def __init__(self, name: str = "cost_model", brokerage_bps=3.0,
                 stt_bps=1.0, exchange_bps=0.3, parent=None):
        super().__init__(name, parent)
        self.brokerage_bps = brokerage_bps
        self.stt_bps = stt_bps
        self.exchange_bps = exchange_bps

    def round_trip_bps(self) -> float:
        return 2 * (self.brokerage_bps + self.stt_bps + self.exchange_bps)


# --------------------------------------------------------------------------
# Intent
# --------------------------------------------------------------------------

class OrderIntent:
    def __init__(self, instrument, side: str, qty: float, kind: str = "market"):
        self.instrument = instrument
        self.side = side
        self.qty = qty
        self.kind = kind

    def __repr__(self):
        return f"<OrderIntent {self.side} {self.qty} {self.instrument.symbol}>"


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------

class Feed(SimObject):
    def __init__(self, name: str = "feed", parent=None):
        super().__init__(name, parent)
        self._msgs: List[SimObject] = []
        self._idx = 0

    def push(self, msg: SimObject) -> None:
        msg.parent = self
        self.children.append(msg)
        self._msgs.append(msg)

    def next(self):
        if self._idx >= len(self._msgs):
            return None
        m = self._msgs[self._idx]
        self._idx += 1
        return m

    def reset(self):
        self._idx = 0

    def __len__(self):
        return len(self._msgs)


class VectorFeed(Feed):
    """Feed a list of closes as bars. Convenient for reference strategies."""
    def __init__(self, instrument, closes, start_ts=0, step_ns=1_000_000_000,
                 name="vector", parent=None):
        super().__init__(name, parent)
        self.instrument = instrument
        for i, c in enumerate(closes):
            ts = start_ts + i * step_ns
            self.push(Bar(instrument, ts, c, c, c, c))


class Execution(SimObject):
    def __init__(self, name: str = "execution", parent=None):
        super().__init__(name, parent)
        self.fills: List[Fill] = []
        self._pending: List[OrderIntent] = []

    def submit(self, intent: OrderIntent) -> None:
        self._pending.append(intent)

    def on_event(self, msg) -> None:
        pass

    def on_stop(self) -> None:
        pass


class BarFill(Execution):
    """Fills pending intents at the next bar's close, with bps slippage."""
    def __init__(self, name: str = "barfill", slippage_bps: float = 0.0,
                 cost_model: Optional[CostModel] = None, parent=None):
        super().__init__(name, parent)
        self.slippage_bps = slippage_bps
        self.cost_model = cost_model

    def on_event(self, msg) -> None:
        if not isinstance(msg, Bar):
            return
        for intent in self._pending:
            sign = 1 if intent.side == "buy" else -1
            price = msg.close * (1 + sign * self.slippage_bps / 10_000.0)
            if self.cost_model is not None:
                price *= 1 + sign * self.cost_model.round_trip_bps() / 10_000.0 / 2
            f = Fill(intent.instrument, msg.ts, price, intent.qty, intent.side)
            self.fills.append(f)
        self._pending.clear()


class Strategy(SimObject):
    def __init__(self, sim: "Sim", name: str, parent=None):
        super().__init__(name, parent if parent is not None else sim)
        self.sim = sim
        self.position = 0.0
        self.intents: List[OrderIntent] = []

    def on_start(self) -> None:
        pass

    def on_event(self, msg) -> None:
        pass

    def on_stop(self) -> None:
        pass

    def submit(self, intent: OrderIntent) -> None:
        self.intents.append(intent)
        if self.sim.execution is not None:
            self.sim.execution.submit(intent)


class BuyAndHold(Strategy):
    def __init__(self, sim, instrument, qty=1.0, name="buy_and_hold", parent=None):
        super().__init__(sim, name, parent)
        self.instrument = instrument
        self.qty = qty
        self._done = False

    def on_event(self, msg):
        if self._done or not isinstance(msg, Bar):
            return
        self.submit(OrderIntent(self.instrument, "buy", self.qty))
        self.position = self.qty
        self._done = True


class SmaCrossover(Strategy):
    def __init__(self, sim, instrument, fast=3, slow=8, qty=1.0,
                 name="sma_crossover", parent=None):
        super().__init__(sim, name, parent)
        self.instrument = instrument
        self.fast, self.slow, self.qty = fast, slow, qty
        self._prices: List[float] = []
        self._prev_diff: Optional[float] = None

    def _sma(self, n: int):
        if len(self._prices) < n:
            return None
        return sum(self._prices[-n:]) / n

    def on_event(self, msg):
        if not isinstance(msg, Bar):
            return
        self._prices.append(msg.close)
        if len(self._prices) < self.slow:
            return
        diff = self._sma(self.fast) - self._sma(self.slow)
        if self._prev_diff is not None:
            if self._prev_diff <= 0 < diff and self.position == 0:
                self.submit(OrderIntent(self.instrument, "buy", self.qty))
                self.position = self.qty
            elif self._prev_diff >= 0 > diff and self.position > 0:
                self.submit(OrderIntent(self.instrument, "sell", self.position))
                self.position = 0.0
        self._prev_diff = diff


class RsiReversal(Strategy):
    def __init__(self, sim, instrument, period=14, oversold=30, overbought=70,
                 qty=1.0, name="rsi_reversal", parent=None):
        super().__init__(sim, name, parent)
        self.instrument = instrument
        self.period, self.oversold, self.overbought, self.qty = period, oversold, overbought, qty
        self._prices: List[float] = []

    def _rsi(self):
        if len(self._prices) < self.period + 1:
            return None
        gains, losses = [], []
        for i in range(-self.period, 0):
            d = self._prices[i] - self._prices[i - 1]
            (gains if d >= 0 else losses).append(abs(d))
        ag = sum(gains) / self.period if gains else 0.0
        al = sum(losses) / self.period if losses else 0.0
        if al == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + ag / al)

    def on_event(self, msg):
        if not isinstance(msg, Bar):
            return
        self._prices.append(msg.close)
        r = self._rsi()
        if r is None:
            return
        if r < self.oversold and self.position == 0:
            self.submit(OrderIntent(self.instrument, "buy", self.qty))
            self.position = self.qty
        elif r > self.overbought and self.position > 0:
            self.submit(OrderIntent(self.instrument, "sell", self.position))
            self.position = 0.0


class Recorder(SimObject):
    def __init__(self, name: str = "recorder", parent=None):
        super().__init__(name, parent)
        self.events: List[SimObject] = []
        self.fills: List[Fill] = []

    def on_event(self, msg):
        self.events.append(msg)

    def on_stop(self, execution):
        if execution is not None:
            self.fills = list(execution.fills)

    def report(self, starting_equity: float = 1_000_000.0) -> "Report":
        equity = starting_equity
        curve = [equity]
        trips = []
        entry_price = None
        entry_qty = 0.0

        for f in self.fills:
            if f.side == "buy":
                entry_price = f.price
                entry_qty = f.qty
            else:
                if entry_price is not None:
                    pnl = (f.price - entry_price) * min(f.qty, entry_qty)
                    trips.append(pnl)
                    equity += pnl
                    curve.append(equity)
                entry_price = None
                entry_qty = 0.0

        wins = [p for p in trips if p > 0]
        losses = [p for p in trips if p < 0]
        return Report(
            fills=len(self.fills),
            round_trips=len(trips),
            net_pnl=sum(trips),
            win_rate=(len(wins) / len(trips)) if trips else 0.0,
            avg_win=(sum(wins) / len(wins)) if wins else 0.0,
            avg_loss=(sum(losses) / len(losses)) if losses else 0.0,
            final_equity=equity,
            equity_curve=curve,
        )


class Report:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def to_markdown(self) -> str:
        return (
            f"# Backtest Report\n\n"
            f"| metric | value |\n"
            f"|---|---|\n"
            f"| fills | {self.fills} |\n"
            f"| round trips | {self.round_trips} |\n"
            f"| net PnL | {self.net_pnl:.2f} |\n"
            f"| win rate | {self.win_rate:.2%} |\n"
            f"| avg win | {self.avg_win:.2f} |\n"
            f"| avg loss | {self.avg_loss:.2f} |\n"
            f"| final equity | {self.final_equity:.2f} |\n"
        )

    def __repr__(self):
        return (f"<Report fills={self.fills} trips={self.round_trips} "
                f"pnl={self.net_pnl:.2f} eq={self.final_equity:.2f}>")


# --------------------------------------------------------------------------
# Top-level Sim
# --------------------------------------------------------------------------

class Sim(SimObject):
    """Top-level backtest container. Collects children by type on instantiate()."""

    def __init__(self, name: str = "sim"):
        super().__init__(name)
        self.feed: Optional[Feed] = None
        self.execution: Optional[Execution] = None
        self.strategies: List[Strategy] = []
        self.recorders: List[Recorder] = []
        self.calendar: Optional[Calendar] = None
        self.cost_model: Optional[CostModel] = None

        self._instantiated = False
        self._event_count = 0
        self._first_ts = None
        self._last_ts = None

    def instantiate(self) -> None:
        self.feed = None
        self.execution = None
        self.strategies.clear()
        self.recorders.clear()
        self.calendar = None
        self.cost_model = None

        for c in self.children:
            if isinstance(c, Feed) and self.feed is None:
                self.feed = c
            elif isinstance(c, Execution) and self.execution is None:
                self.execution = c
            elif isinstance(c, Strategy):
                self.strategies.append(c)
            elif isinstance(c, Recorder):
                self.recorders.append(c)
            elif isinstance(c, Calendar) and self.calendar is None:
                self.calendar = c
            elif isinstance(c, CostModel) and self.cost_model is None:
                self.cost_model = c

        if self.feed is None:
            raise RuntimeError("sim has no Feed child")
        if self.execution is None:
            raise RuntimeError("sim has no Execution child")
        if not self.strategies:
            raise RuntimeError("sim has no Strategy child")
        self._instantiated = True

    def run(self, max_events: Optional[int] = None) -> int:
        if not self._instantiated:
            raise RuntimeError("call sim.instantiate() before sim.run()")

        for s in self.strategies:
            s.on_start()

        dispatched = 0
        while True:
            msg = self.feed.next()
            if msg is None:
                break
            if self._first_ts is None:
                self._first_ts = msg.ts
            self._last_ts = msg.ts
            self._event_count += 1

            # dispatch order: execution first (so fills land before strategies
            # see the same bar), then strategies, then recorders.
            self.execution.on_event(msg)
            for s in self.strategies:
                s.on_event(msg)
            for r in self.recorders:
                r.on_event(msg)

            dispatched += 1
            if max_events is not None and dispatched >= max_events:
                break

        self.execution.on_stop()
        for s in self.strategies:
            s.on_stop()
        for r in self.recorders:
            r.on_stop(self.execution)

        return self._event_count

    def stats(self) -> Dict[str, Any]:
        return {
            "events": self._event_count,
            "first_ts": self._first_ts,
            "last_ts": self._last_ts,
            "strategies": [s.name for s in self.strategies],
            "fills": len(self.execution.fills) if self.execution else 0,
        }


# --------------------------------------------------------------------------
# Entry point used by the Rust CLI
# --------------------------------------------------------------------------

def run_script(source: str, filename: str = "<script>",
               max_events: Optional[int] = None) -> Dict[str, Any]:
    ns: Dict[str, Any] = {
        "SimObject": SimObject,
        "Sim": Sim,
        "Venue": Venue,
        "Instrument": Instrument,
        "Bar": Bar,
        "Tick": Tick,
        "Fill": Fill,
        "Calendar": Calendar,
        "CostModel": CostModel,
        "OrderIntent": OrderIntent,
        "Feed": Feed,
        "VectorFeed": VectorFeed,
        "Execution": Execution,
        "BarFill": BarFill,
        "Strategy": Strategy,
        "BuyAndHold": BuyAndHold,
        "SmaCrossover": SmaCrossover,
        "RsiReversal": RsiReversal,
        "Recorder": Recorder,
        "Report": Report,
        "run": lambda sim, **kw: sim.run(max_events=max_events, **kw),
        "__name__": "__main__",
    }
    try:
        exec(compile(source, filename, "exec"), ns)
        return {"globals": ns, "exit_code": 0, "error": None}
    except Exception:
        return {"globals": ns, "exit_code": 1, "error": traceback.format_exc()}


__all__ = [
    "SimObject", "Sim", "Venue", "Instrument", "Bar", "Tick", "Fill",
    "Calendar", "CostModel", "OrderIntent",
    "Feed", "VectorFeed", "Execution", "BarFill",
    "Strategy", "BuyAndHold", "SmaCrossover", "RsiReversal",
    "Recorder", "Report", "run_script",
]
