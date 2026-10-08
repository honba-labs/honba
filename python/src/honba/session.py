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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Protocol, cast

# ---------------------------------------------------------------------------
# Domain / strategy imports
# These are the stable contracts. Session depends on them; they must not
# depend on Session (no circular imports).
# ---------------------------------------------------------------------------
from honba.backtest.impact import MarketImpact
from honba.backtest.opening_auction import OpeningAuction
from honba.backtest.simulated import (
    FillCostFn,
    FillModel,
    NextOpenExecution,
    fill_costs_from_model,
    make_simulator,
    session_date,
)
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import Instrument, InstrumentId
from honba.entities.order import OrderSide
from honba.entities.trade import Trade
from honba.strategies.base import Strategy
from honba.strategies.context import LedgerContext, StrategyContext
from honba.strategies.execution import OrderRejection
from honba.strategies.runner import (
    ExecutionPortLike,
    IntentRejection,
    StrategyRunner,
    SubmittedIntent,
    SuppressedIntent,
)

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
        """Resolve symbol/exchange to full instrument metadata (tick size, lot, etc.)."""
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

    def print(self, result: BacktestResult, *, format: str = "table") -> None: ...


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
    exchange: str = "NSE"
    start: str | date | datetime = ""  # required in practice; validated in __post_init__
    end: str | date | datetime = ""
    timeframe: str = "1d"
    cash: float = 1_000_000.0
    currency: str = "INR"
    # Named cost pack ("india.equity", "none"), a per-fill cost function
    # (side, quantity, price) -> Money, or a CostModel charged per fill by the simulator.
    costs: str | FillCostFn | CostModel = "india.equity"
    # "next_open" (default): fill at the next session's open (honba.backtest.simulated).
    # "bar_close": fill at the decision bar's close (conformance simulator only).
    fill: FillModel = "next_open"
    # Optional override; if None, Session uses the default registry provider.
    data: DataProvider | None = None
    # Driving bars fed to the strategy before orders are allowed; None uses the
    # strategy's own ``warmup_bars`` (or its catalog config's).
    warmup_bars: int | None = None
    # Sale-proceeds settlement cycle in sessions; None uses the market pack as of the
    # run's first session date (honba.markets.india.settlement_days_for: NSE/BSE equity
    # T+2 before 2023-01-27, T+1 from then). Intraday timeframes require it explicitly.
    settlement_days: int | None = None
    # Square-root market impact (Balch pitfall #4): None (default) fills at the printed
    # open; a MarketImpact degrades every fill by kappa * sigma * sqrt(qty / ADV) using
    # past sessions only, and forces the Python simulator backend.
    impact: MarketImpact | None = None
    # Opening-auction realism (Balch pitfall #8): None (default) fills at the printed open;
    # an OpeningAuction adds an adverse spread buffer (spread_bps) to every open fill and
    # holds orders back delay_bars extra driving bars (intraday timeframes only). Also
    # forces the Python simulator backend.
    auction: OpeningAuction | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("BacktestConfig.symbol is required")
        if self.cash <= 0:
            raise ValueError("BacktestConfig.cash must be positive")
        if not self.start or not self.end:
            raise ValueError("BacktestConfig.start and end are required")

    @property
    def instrument_id(self) -> InstrumentId:
        return InstrumentId(self.symbol, self.exchange)


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
    intents: list[SubmittedIntent] = field(default_factory=list)
    fills: list[Trade] = field(default_factory=list)
    rejections: list[IntentRejection] = field(default_factory=list)
    # Orders (or parts) the simulator rejected or cancelled, incl. unfilled at the end.
    order_rejections: list[OrderRejection] = field(default_factory=list)
    # Intents the warm-up gate released instead of sending.
    suppressed: list[SuppressedIntent] = field(default_factory=list)
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
        print(f"Symbol   : {self.config.symbol}.{self.config.exchange}")
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
        execution: ExecutionPortLike,
        ctx: LedgerContext | None = None,
        # True when the simulator's settlement cycle is the market default, so run() re-resolves
        # it as of the first session's date once the bars are known.
        settlement_from_data: bool = False,
        # Optional hook: (bar_index, bar) -> None for progress / research
        on_bar: Callable[[int, Bar], None] | None = None,
    ) -> None:
        self.strategy = strategy
        self.config = config
        self.data = data
        self.execution = execution
        self._settlement_from_data = settlement_from_data
        self.on_bar = on_bar
        # Fresh ledger seeded with the starting cash unless the caller injects one (tests).
        self.ctx = (
            ctx
            if ctx is not None
            else LedgerContext(cash=config.cash, currency=Currency(config.currency))
        )
        # Runner binds strategy ↔ ctx, gates warm-up and talks to execution.
        self.runner = StrategyRunner(
            strategy, execution, ctx=self.ctx, warmup_bars=config.warmup_bars
        )
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

        if isinstance(self.execution, NextOpenExecution):
            self.execution.set_lot_size(instrument_id, instrument.lot_size)
        if self._settlement_from_data and isinstance(self.execution, NextOpenExecution):
            from honba.markets.india.settlement import settlement_days_for

            self.execution.set_settlement_days(
                settlement_days_for(self.config.exchange, as_of=session_date(_bar_ts(bars[0])))
            )

        # --- 3. Drive -----------------------------------------------------------
        # Events are (event, ts_init) as required by StrategyRunner; a simulator
        # with on_event sees each one before the strategy.
        runner = self.runner
        observe = getattr(self.execution, "on_event", None)
        runner.start()
        for index, bar in enumerate(bars):
            ts = _bar_ts(bar)
            if observe is not None:
                observe(bar, ts)
            runner.on_event(bar, ts)
            if self.on_bar is not None:
                self.on_bar(index, bar)
        # End of data: cancel what is still working so the ledger releases it.
        for submitted in list(runner.intents):
            runner.cancel(submitted.order_id)
        runner.stop()

        fills = list(runner.fills)

        # --- 4. Metrics ---------------------------------------------------------
        metrics, equity_curve = _compute_metrics(
            fills=fills,
            bars=bars,
            initial_cash=self.config.cash,
            ctx=runner.ctx,
        )

        return BacktestResult(
            strategy_name=self.strategy.name,
            config=self.config,
            intents=list(runner.intents),
            fills=fills,
            rejections=list(runner.rejections),
            order_rejections=list(runner.order_rejections),
            suppressed=list(runner.suppressed),
            ctx=runner.ctx,
            metrics=metrics,
            equity_curve=equity_curve,
        )

    def _seed_context(self, instrument: Instrument) -> None:
        """Register instrument metadata on the ledger (starting cash is set at construction)."""
        add = getattr(self.ctx, "add_instrument", None)
        if add is not None:
            add(instrument)


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
        exchange: str = "NSE",
        start: str | date | datetime,
        end: str | date | datetime,
        timeframe: str = "1d",
        cash: float = 1_000_000.0,
        currency: str = "INR",
        costs: str | FillCostFn | CostModel = "india.equity",
        fill: FillModel = "next_open",
        data: DataProvider | None = None,
        execution: ExecutionPortLike | None = None,
        on_bar: Callable[[int, Bar], None] | None = None,
        warmup_bars: int | None = None,
        settlement_days: int | None = None,
        impact: MarketImpact | None = None,
        auction: OpeningAuction | None = None,
    ) -> BacktestSession:
        """Build a BacktestSession ready for .run().

        Parameters
        ----------
        strategy:
          * Strategy instance
          * Strategy subclass (instantiated with no args)
          * path to a .py file containing one Strategy subclass
          * a registry name in the honba-strategies catalog ($HONBA_STRATEGIES_DIR),
            instantiated with its config.toml
        symbol / exchange:
          Primary instrument for this run (multi-leg later can take a list).
        start / end:
          Inclusive/exclusive bounds interpreted by the DataProvider.
        timeframe:
          Bar size string understood by the provider ("1d", "5m", ...).
        cash:
          Starting portfolio cash in account currency.
        costs:
          Named pack ("india.equity", "india.equity.intraday", "none"), a fill-cost
          function ``(side, quantity, price) -> Money``, or a CostModel whose
          ``apply`` costs are charged by the simulator (see ``fill_costs_from_model``).
        fill:
          "next_open" (default) or "bar_close" — selects the simulated execution
          port when ``execution`` is not passed explicitly.
        warmup_bars:
          Driving bars before orders are allowed; None uses the strategy's.
        settlement_days:
          Settlement cycle override; None uses the market pack as of the first session's
          date (NSE/BSE equity: T+2 before 2023-01-27, T+1 from then; a run that spans
          the change keeps the cycle of its first session). Required for intraday
          timeframes, where a session is a bar rather than a trading day.
        impact:
          Square-root market impact model (Balch pitfall #4): fills degrade by
          ``kappa * sigma_daily * sqrt(qty / ADV)`` from past sessions only, and costs are
          charged on the impacted price. None (default) fills at the printed open.
          Forces the Python simulator backend.
        auction:
          Opening-auction realism (Balch pitfall #8): an ``OpeningAuction`` charges every
          open fill an adverse ``spread_bps`` buffer and holds orders back ``delay_bars``
          extra driving bars (intraday ``timeframe`` only) so entries skip the opening
          turbulence. None (default) fills at the printed open. Forces the Python
          simulator backend.
        data / execution:
          Optional overrides for tests or custom infrastructure.
        """
        resolved_strategy, config_warmup = _resolve_strategy(strategy)
        config = BacktestConfig(
            symbol=symbol,
            exchange=exchange,
            start=start,
            end=end,
            timeframe=timeframe,
            cash=cash,
            currency=currency,
            costs=costs,
            fill=fill,
            data=data,
            warmup_bars=warmup_bars if warmup_bars is not None else config_warmup,
            settlement_days=settlement_days,
            impact=impact,
            auction=auction,
        )

        resolved_data = data if data is not None else _default_data_provider()
        cost_model = costs if _is_cost_model(costs) else None
        resolved_execution = (
            execution if execution is not None else _default_execution(config, cost_model)
        )

        return BacktestSession(
            strategy=resolved_strategy,
            config=config,
            data=resolved_data,
            execution=resolved_execution,
            # A custom execution port is assumed to apply its own costs; otherwise the
            # CostModel was wired into the simulator by _default_execution.
            settlement_from_data=execution is None and settlement_days is None,
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


def _resolve_strategy(
    strategy: Strategy | type[Strategy] | str | Path,
) -> tuple[Strategy, int | None]:
    """Turn instance / class / file path / catalog name into a Strategy instance.

    Returns the strategy and the ``warmup_bars`` of its catalog config (None otherwise).
    """
    if isinstance(strategy, Strategy):
        return strategy, None

    if isinstance(strategy, type) and issubclass(strategy, Strategy):
        return strategy(), None

    if not isinstance(strategy, (str, Path)):
        raise TypeError(
            "strategy must be a Strategy instance, subclass, .py path or catalog name; "
            f"got {strategy!r}"
        )

    from honba.strategies.loader import find_catalog, load_catalog_strategy, load_strategy

    path = Path(strategy)
    if path.suffix == ".py":
        return load_strategy(path)(), None

    # A bare registry name in the honba-strategies catalog.
    loaded = load_catalog_strategy(str(strategy), find_catalog())
    warmup = loaded.config.warmup_bars or None
    return loaded.instantiate(), warmup


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


def _is_cost_model(costs: object) -> bool:
    """A post-hoc CostModel (``apply(trade) -> Trade``), as opposed to a pack name or fill fn."""
    return not isinstance(costs, str) and hasattr(costs, "apply")


def _default_execution(config: BacktestConfig, cost_model: CostModel | None) -> ExecutionPortLike:
    """Build the simulated execution port for the config (``honba.backtest.simulated``).

    A pack name or fill-cost function is charged by the simulator itself; a post-hoc
    CostModel is adapted to a fill-cost function so the ledger, fills and metrics agree.
    """
    currency = Currency(config.currency)
    fill_costs = (
        fill_costs_from_model(cost_model, currency=currency)
        if cost_model is not None
        else cast("str | FillCostFn", config.costs)
    )
    return make_simulator(
        fill=config.fill,
        cash=Money.from_major(config.cash, Currency(config.currency)),
        costs=fill_costs,
        exchange=config.exchange,
        settlement_days=config.settlement_days,
        timeframe=config.timeframe,
        as_of=_parse_time(config.start).date(),
        impact=config.impact,
        auction=config.auction,
    )


def _parse_time(value: str | date | datetime) -> datetime:
    """Normalize config time inputs to datetime for DataProvider."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)  # noqa: DTZ001 - naive by contract; DataProvider receives naive midnight
    # ISO date or datetime string
    text = value.strip()
    if len(text) == 10:
        d = date.fromisoformat(text)
        return datetime(d.year, d.month, d.day)  # noqa: DTZ001 - naive by contract; DataProvider receives naive midnight
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
    raise AttributeError(f"Bar {bar!r} has no known timestamp field (ts_init / ts_event / ts)")


_QTY_EPS = 1e-9  # quantities closer than this are equal (float residue from fractional fills)


def _compute_metrics(
    *,
    fills: Sequence[Trade],
    bars: Sequence[Bar],
    initial_cash: float,
    ctx: StrategyContext,
) -> tuple[dict[str, float], list[tuple[int, float]]]:
    """Compute basic performance metrics and the per-session equity curve.

    The curve has one ``(ts, equity)`` point per distinct bar ``ts``: cash plus every
    position marked at its last known close (fills at ``ts`` are applied first, so a
    session's point is its close). Cash and positions are replayed from ``fills`` in
    integer ``Money``; ``final_cash`` and ``final_equity`` come from the ledger ``ctx``
    and marked at the last known close, so the curve ends on ``final_equity``.

    Metrics:

    * ``n_fills``: fills. ``n_trades``: round trips, i.e. times an instrument's position
      returned to flat; a trade still open at the end is not counted.
    * ``max_drawdown_pct``: largest peak-to-trough fall of the curve as a percent of the
      peak (a positive number); ``0.0`` for curves with fewer than two points or no fall.

    Intentionally simple so Session works before a full analytics crate exists; keep
    the return shape stable (dict + list of (ts, equity)).
    """
    ledger_cash = ctx.cash()
    currency = ledger_cash.currency if isinstance(ledger_cash, Money) else Currency.INR
    ordered_fills = sorted(fills, key=lambda f: f.ts)  # stable: submission order within a ts

    metrics: dict[str, float] = {
        "initial_cash": float(initial_cash),
        "n_fills": float(len(fills)),
        "n_trades": float(_round_trips(ordered_fills)),
    }

    # Replay cash and positions per session; mark every held instrument at its last close.
    cash = Money.from_major(initial_cash, currency)
    positions: dict[InstrumentId, float] = {}
    last_close: dict[InstrumentId, float] = {}
    equity_curve: list[tuple[int, float]] = []
    pending = iter(ordered_fills)
    fill = next(pending, None)
    ordered_bars = sorted(bars, key=_bar_ts)
    i = 0
    while i < len(ordered_bars):
        ts = _bar_ts(ordered_bars[i])
        while i < len(ordered_bars) and _bar_ts(ordered_bars[i]) == ts:
            last_close[ordered_bars[i].instrument_id] = float(ordered_bars[i].close)
            i += 1
        while fill is not None and fill.ts <= ts:
            cash = cash + _fill_cash_flow(fill, currency)
            signed = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity
            positions[fill.instrument_id] = positions.get(fill.instrument_id, 0.0) + signed
            last_close.setdefault(fill.instrument_id, fill.price)
            fill = next(pending, None)
        equity_curve.append((ts, cash.to_major() + _marked(positions, last_close)))

    final_cash = ledger_cash.to_major() if isinstance(ledger_cash, Money) else float(ledger_cash)
    metrics["final_cash"] = final_cash
    final_positions = ctx.positions()
    for f in ordered_fills:  # a held instrument with no bar is marked at its last fill
        last_close.setdefault(f.instrument_id, f.price)
    final_equity = final_cash + _marked(final_positions, last_close)
    metrics["final_equity"] = final_equity
    metrics["total_return_pct"] = (
        (final_equity - initial_cash) / initial_cash * 100.0 if initial_cash else 0.0
    )
    metrics["max_drawdown_pct"] = _max_drawdown_pct([eq for _, eq in equity_curve])
    return metrics, equity_curve


def _fill_cash_flow(fill: Trade, currency: Currency) -> Money:
    notional = Money.mul_qty(fill.quantity, fill.price, currency)
    costs = fill.costs if fill.costs.amount else Money.zero(currency)  # zero is currency-neutral
    if fill.side is OrderSide.BUY:
        return Money.zero(currency) - (notional + costs)
    return notional - costs


def _marked(positions: Mapping[InstrumentId, float], prices: Mapping[InstrumentId, float]) -> float:
    return sum(qty * prices.get(iid, 0.0) for iid, qty in positions.items() if abs(qty) > _QTY_EPS)


def _round_trips(fills: Sequence[Trade]) -> int:
    held: dict[InstrumentId, float] = {}
    trips = 0
    for f in fills:
        before = held.get(f.instrument_id, 0.0)
        after = before + (f.quantity if f.side is OrderSide.BUY else -f.quantity)
        if abs(after) <= _QTY_EPS:
            after = 0.0
            if abs(before) > _QTY_EPS:
                trips += 1
        held[f.instrument_id] = after
    return trips


def _max_drawdown_pct(equity: Sequence[float]) -> float:
    peak, worst = float("-inf"), 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak * 100.0)
    return worst
