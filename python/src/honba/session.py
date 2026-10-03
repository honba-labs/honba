"""Honba session orchestration.

This module is the single entry point for running a strategy end-to-end:

    load strategy  →  wire data + exchange + portfolio + indicators
                   →  drive StrategyRunner over historical events
                   →  return BacktestResult (report / metrics)

Design constraints (ADR 008 and workspace principles)
----------------------------------------------------
* A Strategy never imports data loaders, exchanges, or the wall clock.
  It only sees StrategyContext (self.ctx): clock, positions, cash,
  instrument metadata, and submit(OrderIntent).
* The same Strategy class must run unchanged in backtest, paper, and live.
  Only the Session changes which DataProvider and ExecutionPort are bound.
* Python owns orchestration. Rust (honba._honba / honba-sim / honba-data)
  owns hot paths once they exist; this file calls protocols, not concrete
  Rust types, so the public API stays stable when implementations move.

Typical usage
-------------
CLI::

    honba run path/to/strategy.py --symbol RELIANCE --from 2024-01-01 --to 2025-01-01

Python::

    from honba import Honba, Strategy

    class SmaCross(Strategy):
        name = "sma_cross"
        def on_bar(self, bar): ...

    result = Honba.backtest(
        SmaCross(),
        symbol="RELIANCE",
        start="2024-01-01",
        end="2025-01-01",
    ).run()
    result.print_report()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Protocol, Sequence

# ---------------------------------------------------------------------------
# Domain / strategy imports
# These are the stable contracts. Session depends on them; they must not
# depend on Session (no circular imports).
# ---------------------------------------------------------------------------
from honba.entities.bar import Bar
from honba.entities.instrument import Instrument, InstrumentId
from honba.entities.order import OrderIntent
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext, StrategyContext
from honba.strategies.runner import ExecutionPort, RunResult, StrategyRunner

# Optional: loader lives next to the strategy package. Import lazily in
# _resolve_strategy if you want to keep session importable without loader.
# from honba.strategies.loader import load_strategy


# =============================================================================
# Protocols — what Session needs from the rest of the SDK
# =============================================================================
# Defined here (or in dedicated modules) so Session does not hard-code NSE
# loaders or a particular simulator. Tests inject fakes; production injects
# real data / sim / broker adapters.


class DataProvider(Protocol):
    """Historical (or live) market data source.

    Implementations:
      * research loaders (NSE bhavcopy, BSE, broker API cache)
      * later: Rust honba-data behind honba._honba
    """

    def bars(
        self,
        instrument_id: InstrumentId,
        *,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> Sequence[Bar]:
        """Return bars in ascending time order for [start, end)."""
        ...

    def instrument(self, instrument_id: InstrumentId) -> Instrument:
        """Resolve symbol/venue to full instrument metadata (tick size, lot, etc.)."""
        ...


class CostModel(Protocol):
    """Per-fill transaction costs (brokerage, STT, slippage, etc.).

    India equity defaults live under markets/india/costs. Session only
    needs something that can adjust a Trade or price at fill time.
    """

    def apply(self, trade: Trade) -> Trade:
        """Return a cost-adjusted trade (fees deducted / added as appropriate)."""
        ...


class ReportPrinter(Protocol):
    """How BacktestResult is shown (table, JSON, HTML)."""

    def print(self, result: "BacktestResult", *, format: str = "table") -> None: ...


# Fill model names the Session understands. Concrete exchange simulators
# map these to bar-close, next-open, or more realistic matching engines.
FillModel = Literal["bar_close", "next_open"]


# =============================================================================
# Configuration
# =============================================================================


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """Everything needed to build a backtest session except the strategy itself.

    Frozen so a config can be hashed, logged, and replayed deterministically.
    All time fields are timezone-aware or naive-UTC by convention of the
    DataProvider; Session does not reinterpret them.
    """

    symbol: str
    venue: str = "NSE"
    start: str | date | datetime = ""  # required in practice; validated in __post_init__
    end: str | date | datetime = ""
    timeframe: str = "1d"
    cash: float = 1_000_000.0
    currency: str = "INR"
    # Named cost pack ("india.equity") or a CostModel instance. Resolved in Session.
    costs: str | CostModel = "india.equity"
    fill: FillModel = "bar_close"
    # Optional override; if None, Session uses the default registry provider.
    data: DataProvider | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("BacktestConfig.symbol is required")
        if self.cash <= 0:
            raise ValueError("BacktestConfig.cash must be positive")
        if not self.start or not self.end:
            raise ValueError("BacktestConfig.start and end are required")

    @property
    def instrument_id(self) -> InstrumentId:
        return InstrumentId(self.symbol, self.venue)


# =============================================================================
# Result
# =============================================================================


@dataclass
class BacktestResult:
    """Outcome of a single backtest run.

    Holds both the low-level runner output (intents, fills, final context)
    and higher-level metrics for reporting. Metrics are computed once in
    BacktestSession.run so print_report is pure presentation.
    """

    strategy_name: str
    config: BacktestConfig
    # Raw runner output
    intents: list[Any] = field(default_factory=list)
    fills: list[Trade] = field(default_factory=list)
    rejections: list[Any] = field(default_factory=list)
    ctx: StrategyContext | None = None
    # Derived
    metrics: dict[str, float] = field(default_factory=dict)
    equity_curve: list[tuple[int, float]] = field(default_factory=list)  # (ts_ns, equity)
    # Optional narrative / debug
    notes: list[str] = field(default_factory=list)

    def print_report(self, format: str = "tui") -> None:
        """Print a human- or machine-readable report.

        format:
          * "table" — Rich/stdout summary (default for CLI)
          * "json"  — metrics + trade list as JSON
          * "html"  — reserved for research notebooks
        """
        # Keep presentation out of the engine: delegate to report module when
        # it exists; fallback is a minimal stdout dump so this file is usable alone.
        try:
            from honba.report import print_backtest_report

            print_backtest_report(self, format=format)
            return
        except ImportError:
            pass

        # Minimal fallback
        m = self.metrics
        print(f"Strategy : {self.strategy_name}")
        print(f"Symbol   : {self.config.symbol}.{self.config.venue}")
        print(f"Period   : {self.config.start} → {self.config.end}")
        print(f"Fills    : {len(self.fills)}")
        print(f"Final equity : {m.get('final_equity', float('nan')):.2f}")
        print(f"Return %     : {m.get('total_return_pct', float('nan')):.2f}")
        print(f"Max DD %     : {m.get('max_drawdown_pct', float('nan')):.2f}")
        print(f"Trades       : {m.get('n_trades', 0):.0f}")


# =============================================================================
# Session
# =============================================================================


class BacktestSession:
    """Wired backtest: strategy + data + simulated exchange + runner.

    Constructed only via Honba.backtest(...). Call run() once; the session
    is not designed to be reused for a second run (create a new one instead
    so clocks, cash, and fill state stay deterministic).
    """

    def __init__(
        self,
        strategy: Strategy,
        config: BacktestConfig,
        *,
        data: DataProvider,
        execution: ExecutionPort,
        ctx: LedgerContext | None = None,
        cost_model: CostModel | None = None,
        # Optional hook: (bar_index, bar) -> None for progress / research
        on_bar: Callable[[int, Bar], None] | None = None,
    ) -> None:
        self.strategy = strategy
        self.config = config
        self.data = data
        self.execution = execution
        self.cost_model = cost_model
        self.on_bar = on_bar
        # Fresh ledger unless caller injects one (tests).
        self.ctx = ctx if ctx is not None else LedgerContext()
        # Runner binds strategy ↔ ctx and talks to execution.
        self.runner = StrategyRunner(strategy, execution, ctx=self.ctx)
        self._ran = False

    def run(self) -> BacktestResult:
        """Fetch bars, drive the strategy, compute metrics, return result.

        Steps:
          1. Resolve instrument metadata (lot size, tick, etc.) into context.
          2. Load bar series from DataProvider.
          3. Seed portfolio cash on the ledger.
          4. StrategyRunner.run(events) — start → each bar → stop.
          5. Build equity curve / metrics from fills + final positions.
        """
        if self._ran:
            raise RuntimeError(
                "BacktestSession.run() was already called; create a new session "
                "for another run so state stays deterministic"
            )
        self._ran = True

        instrument_id = self.config.instrument_id
        instrument = self.data.instrument(instrument_id)

        # --- 1. Context seeding -------------------------------------------------
        # StrategyContext implementations should expose register_instrument /
        # set_cash if available. LedgerContext is the default backtest ctx.
        self._seed_context(instrument)

        # --- 2. Data ------------------------------------------------------------
        start_dt = _parse_time(self.config.start)
        end_dt = _parse_time(self.config.end)
        bars = list(
            self.data.bars(
                instrument_id,
                timeframe=self.config.timeframe,
                start=start_dt,
                end=end_dt,
            )
        )
        if not bars:
            return BacktestResult(
                strategy_name=self.strategy.name,
                config=self.config,
                notes=["no bars returned for the requested range"],
            )

        # Events are (event, ts_init) as required by StrategyRunner.
        # ts_init is the bar's init/close timestamp in unix ns (Bar contract).
        events: list[tuple[Any, int]] = [(bar, _bar_ts(bar)) for bar in bars]

        if self.on_bar is not None:
            # Wrap execution so we can observe bars without changing the runner.
            # Prefer a thin callback after each on_event if you extend the runner;
            # for now we rely on the runner's observe path (execution.on_event).
            pass

        # --- 3. Drive -----------------------------------------------------------
        run_result: RunResult = self.runner.run(events)

        # Optional: apply cost model to fills if the simulator did not.
        fills = list(run_result.fills)
        if self.cost_model is not None:
            fills = [self.cost_model.apply(t) for t in fills]

        # --- 4. Metrics ---------------------------------------------------------
        metrics, equity_curve = _compute_metrics(
            fills=fills,
            bars=bars,
            initial_cash=self.config.cash,
            ctx=run_result.ctx,
        )

        return BacktestResult(
            strategy_name=self.strategy.name,
            config=self.config,
            intents=list(run_result.intents),
            fills=fills,
            rejections=list(run_result.rejections),
            ctx=run_result.ctx,
            metrics=metrics,
            equity_curve=equity_curve,
        )

    def _seed_context(self, instrument: Instrument) -> None:
        """Register instrument metadata and starting cash on the ledger.

        LedgerContext may grow explicit APIs (set_cash, register). Until then
        we use duck-typing so this file stays compatible with the current
        context implementation and with test doubles.
        """
        ctx = self.ctx
        if hasattr(ctx, "register_instrument"):
            ctx.register_instrument(instrument)  # type: ignore[attr-defined]
        if hasattr(ctx, "set_cash"):
            ctx.set_cash(self.config.cash)  # type: ignore[attr-defined]
        elif hasattr(ctx, "cash"):
            # Some ledgers expose a mutable cash attribute.
            try:
                setattr(ctx, "cash", float(self.config.cash))
            except Exception:
                pass


# =============================================================================
# Façade
# =============================================================================


class Honba:
    """Public factory for sessions.

    Keeps construction details (default data provider, cost packs, fill
    engines) in one place so CLI and library users share the same path.
    """

    @classmethod
    def backtest(
        cls,
        strategy: Strategy | type[Strategy] | str | Path,
        *,
        symbol: str,
        venue: str = "NSE",
        start: str | date | datetime,
        end: str | date | datetime,
        timeframe: str = "1d",
        cash: float = 1_000_000.0,
        currency: str = "INR",
        costs: str | CostModel = "india.equity",
        fill: FillModel = "bar_close",
        data: DataProvider | None = None,
        execution: ExecutionPort | None = None,
        on_bar: Callable[[int, Bar], None] | None = None,
    ) -> BacktestSession:
        """Build a BacktestSession ready for .run().

        Parameters
        ----------
        strategy:
          * Strategy instance
          * Strategy subclass (instantiated with no args)
          * path to a .py file containing one Strategy subclass
        symbol / venue:
          Primary instrument for this run (multi-leg later can take a list).
        start / end:
          Inclusive/exclusive bounds interpreted by the DataProvider.
        timeframe:
          Bar size string understood by the provider ("1d", "5m", ...).
        cash:
          Starting portfolio cash in account currency.
        costs:
          Named pack ("india.equity") or a CostModel instance.
        fill:
          "bar_close" or "next_open" — selects the simulated execution port
          when ``execution`` is not passed explicitly.
        data / execution:
          Optional overrides for tests or custom infrastructure.
        """
        config = BacktestConfig(
            symbol=symbol,
            venue=venue,
            start=start,
            end=end,
            timeframe=timeframe,
            cash=cash,
            currency=currency,
            costs=costs,
            fill=fill,
            data=data,
        )

        resolved_strategy = _resolve_strategy(strategy)
        resolved_data = data if data is not None else _default_data_provider()
        cost_model = _resolve_costs(costs)
        resolved_execution = (
            execution
            if execution is not None
            else _default_execution(fill=fill, cost_model=cost_model)
        )

        return BacktestSession(
            strategy=resolved_strategy,
            config=config,
            data=resolved_data,
            execution=resolved_execution,
            cost_model=None if execution is not None else cost_model,
            # If the user supplied a custom execution port, we assume it already
            # applies costs; otherwise Session may post-apply cost_model on fills.
            on_bar=on_bar,
        )

    # Future: paper / live factories share the same Strategy + different ports.
    # @classmethod
    # def paper(cls, strategy, *, broker: str = "paper", ...) -> PaperSession: ...
    # @classmethod
    # def live(cls, strategy, *, broker: str, ...) -> LiveSession: ...


# =============================================================================
# Resolution helpers (defaults, loading, parsing)
# =============================================================================


def _resolve_strategy(strategy: Strategy | type[Strategy] | str | Path) -> Strategy:
    """Turn instance / class / file path into a Strategy instance."""
    if isinstance(strategy, Strategy):
        return strategy

    if isinstance(strategy, type) and issubclass(strategy, Strategy):
        return strategy()

    # Path or string path → load module and pick Strategy subclass.
    path = Path(strategy)
    if not path.suffix == ".py":
        # Allow "package.module:ClassName" later; for now require a file.
        raise TypeError(
            f"strategy must be a Strategy instance, subclass, or .py path; got {strategy!r}"
        )

    try:
        from honba.strategies.loader import load_strategy
    except ImportError as e:
        raise ImportError(
            "honba.strategies.loader is required to load a strategy from a file path"
        ) from e

    cls = load_strategy(path)
    return cls()


def _default_data_provider() -> DataProvider:
    """Registry default: India market pack loaders (NSE daily, etc.).

    Raises a clear error if no provider is registered yet so callers know
    to pass data= explicitly in early development.
    """
    try:
        from honba.data import get_default_provider

        return get_default_provider()
    except ImportError as e:
        raise ImportError(
            "No default DataProvider. Pass data= to Honba.backtest(...) "
            "or implement honba.data.get_default_provider()"
        ) from e


def _resolve_costs(costs: str | CostModel) -> CostModel | None:
    if not isinstance(costs, str):
        return costs
    try:
        from honba.markets.india.costs import get_cost_model

        return get_cost_model(costs)
    except ImportError:
        # Costs optional during early bootstrap; simulator may be zero-cost.
        return None


def _default_execution(
    *,
    fill: FillModel,
    cost_model: CostModel | None,
) -> ExecutionPort:
    """Build a simulated execution port for the chosen fill model.

    Preferred path: honba.exchange.simulated (Python or Rust-backed).
    Fallback: testing.BarCloseFills from the strategy test harness so
    backtests can run before the full exchange package exists.
    """
    try:
        from honba.exchange.simulated import make_simulator

        return make_simulator(fill=fill, cost_model=cost_model)
    except ImportError:
        pass

    # Bootstrap: reuse the conformance simulator if present.
    try:
        from honba.strategies.testing import BarCloseFills

        # BarCloseFills may accept cost kwargs; keep call minimal.
        return BarCloseFills()  # type: ignore[return-value]
    except ImportError as e:
        raise ImportError(
            "No simulated ExecutionPort available. Implement "
            "honba.exchange.simulated.make_simulator or pass execution="
        ) from e


def _parse_time(value: str | date | datetime) -> datetime:
    """Normalize config time inputs to datetime for DataProvider."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    # ISO date or datetime string
    text = value.strip()
    if len(text) == 10:
        d = date.fromisoformat(text)
        return datetime(d.year, d.month, d.day)
    return datetime.fromisoformat(text)


def _bar_ts(bar: Bar) -> int:
    """Extract ts_init (unix ns) from a Bar for StrategyRunner.

    Bar field names may be ts_init, ts_event, or similar depending on the
    entities module; adapt here in one place.
    """
    for attr in ("ts_init", "ts_event", "ts", "timestamp"):
        if hasattr(bar, attr):
            val = getattr(bar, attr)
            if val is not None:
                return int(val)
    raise AttributeError(
        f"Bar {bar!r} has no known timestamp field (ts_init / ts_event / ts)"
    )


def _compute_metrics(
    *,
    fills: Sequence[Trade],
    bars: Sequence[Bar],
    initial_cash: float,
    ctx: StrategyContext,
) -> tuple[dict[str, float], list[tuple[int, float]]]:
    """Compute basic performance metrics and a coarse equity curve.

    This is intentionally simple so Session works before a full analytics
    crate exists. Replace with honba-analytics / Rust metrics when ready;
    keep the return shape stable (dict + list of (ts, equity)).
    """
    metrics: dict[str, float] = {
        "initial_cash": float(initial_cash),
        "n_fills": float(len(fills)),
        "n_trades": float(len(fills)),  # refine when round-trips are defined
    }

    # Final cash / equity from context if available
    final_cash = initial_cash
    if hasattr(ctx, "cash"):
        try:
            final_cash = float(getattr(ctx, "cash"))
        except Exception:
            pass
    metrics["final_cash"] = final_cash

    # Mark-to-market: last bar close * net position if we can read position
    final_equity = final_cash
    if bars:
        last = bars[-1]
        iid = getattr(last, "instrument_id", None)
        if iid is not None and hasattr(ctx, "position"):
            try:
                qty = float(ctx.position(iid))
                close = float(getattr(last, "close", 0.0) or 0.0)
                final_equity = final_cash + qty * close
            except Exception:
                pass
    metrics["final_equity"] = final_equity
    metrics["total_return_pct"] = (
        (final_equity - initial_cash) / initial_cash * 100.0 if initial_cash else 0.0
    )

    # Placeholder drawdown until a proper equity curve is built from fills
    metrics["max_drawdown_pct"] = float("nan")

    # Coarse curve: start and end only (analytics package should expand this)
    equity_curve: list[tuple[int, float]] = []
    if bars:
        equity_curve.append((_bar_ts(bars[0]), float(initial_cash)))
        equity_curve.append((_bar_ts(bars[-1]), float(final_equity)))

    return metrics, equity_curve