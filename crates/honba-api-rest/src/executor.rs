//! The concrete executor: one backtest on the calling thread (ADR 0017 decision 8).
//!
//! The wiring is the one `honba-sweep` runs per trial (a `BarFillEngine` paper sink, a
//! `StrategyRunner` over it, an `Engine` dispatching the bars), plus a tape that records
//! every dispatched message and every order lifecycle event in kernel processing order and
//! hands it to the [`JournalWriter`]. Nothing here reads the wall clock: `ts_init` comes from
//! the data, so the journal bytes depend only on the job and the bars.

use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use honba_analytics::{EquityStats, RoundTrip, TradeStats};
use honba_api::{
    parse_instrument_id, parse_timeframe, BacktestMetrics, JournalWriter, ResolvedBacktest,
    ResolvedRequest, RunExecutor, RunJob, RunOutcome,
};
use honba_config::{AccountConfig, ExecutionConfig, FillModel};
use honba_engine::{
    AlgoError, Engine, EngineOutput, ExecutionEngine, Handler, Result as EngineResult,
};
use honba_entities::{Currency, ExecutionEvent, Money, Trade};
use honba_messages::{
    ErrorCode, ErrorDetail, Event, InstrumentId, Message, Order, OrderStatus, UnixNanos,
};
use honba_ports::{BarReader, BarRequest, PortError};
use honba_sim::BarFillEngine;
use honba_strategy::{DynStrategy, LedgerContext, StrategyRunner};
use serde_json::{json, Value};

use crate::registry::StrategyRegistry;

/// Annualisation used for the Sharpe ratio; stated in the outcome's assumptions.
const PERIODS_PER_YEAR: f64 = 252.0;

/// Records appended between journal flushes, so a reader sees a growing prefix.
const FLUSH_EVERY: usize = 256;

/// Runs a resolved backtest with the synchronous kernel: `Engine` + `StrategyRunner` +
/// `BarFillEngine`, bars read through the [`BarReader`] port.
///
/// It owns no threads: a worker calls [`RunExecutor::execute`] on its own thread, which must
/// not be a tokio runtime thread (the bar read blocks on a private current-thread runtime).
pub struct BacktestExecutor {
    bars: Arc<dyn BarReader>,
    registry: StrategyRegistry,
    account: AccountConfig,
    execution: ExecutionConfig,
}

impl BacktestExecutor {
    /// An executor over `bars` running the strategies in `registry`, with the default
    /// account and execution config.
    pub fn new(bars: Arc<dyn BarReader>, registry: StrategyRegistry) -> Self {
        Self {
            bars,
            registry,
            account: AccountConfig::default(),
            execution: ExecutionConfig::default(),
        }
    }

    /// Overrides the account config (currency).
    #[must_use]
    pub fn with_account(mut self, account: AccountConfig) -> Self {
        self.account = account;
        self
    }
}

fn invalid(field: &str, reason: &str, message: impl Into<String>) -> ErrorDetail {
    ErrorDetail::new(ErrorCode::ValidationInvalidRequest, message)
        .with_context(json!({"field": field, "reason": reason}))
}

fn internal(reason: &str, message: impl Into<String>) -> ErrorDetail {
    ErrorDetail::new(ErrorCode::InternalError, message).with_context(json!({"reason": reason}))
}

fn port_error(e: &PortError) -> ErrorDetail {
    let code = match e {
        PortError::Unsupported(_) => ErrorCode::Unsupported,
        PortError::InvalidRequest(_) => ErrorCode::ValidationInvalidRequest,
        PortError::Unavailable(_) | PortError::Timeout | PortError::Transport(_) => {
            ErrorCode::MarketDataUnavailable
        }
        _ => ErrorCode::InternalError,
    };
    ErrorDetail::new(code, format!("bar read failed: {e}"))
        .with_context(json!({"reason": "bar_read"}))
}

fn engine_error(e: &AlgoError) -> ErrorDetail {
    internal("engine", format!("backtest failed: {e}"))
}

/// The one instrument of a universe. A list or a bare symbol is refused.
pub(crate) fn parse_universe(universe: &str) -> Result<InstrumentId, ErrorDetail> {
    let bad = |why: &str| {
        invalid(
            "universe",
            "unsupported_universe",
            format!("universe must be one SYMBOL.EXCHANGE instrument: {why}"),
        )
    };
    let text = universe.trim();
    if text.is_empty() || text.contains(',') || text.contains(char::is_whitespace) {
        return Err(bad("lists are not supported"));
    }
    parse_instrument_id(text).map_err(|_| bad("not SYMBOL.EXCHANGE"))
}

// ---------------------------------------------------------------------------------------
// The tape: what the journal records, in kernel processing order.
// ---------------------------------------------------------------------------------------

#[derive(Default)]
struct Tape {
    records: Vec<Message>,
    now: UnixNanos,
}

type SharedTape = Arc<Mutex<Tape>>;

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

/// First handler: records every message the engine dispatches.
struct Recorder(SharedTape);

impl Handler for Recorder {
    fn on_event(&mut self, event: &Event, ts_init: UnixNanos) -> EngineResult<EngineOutput> {
        let mut tape = lock(&self.0);
        tape.now = event.ts_event();
        tape.records.push(Message::new(event.clone(), ts_init));
        Ok(EngineOutput::None)
    }
}

/// The paper sink: a [`BarFillEngine`] that tees what the runner submits and what comes
/// back (as ADR 0019 wire events) onto the tape, and the fills onto the fill list.
#[derive(Clone)]
struct JournalSink {
    inner: BarFillEngine,
    tape: SharedTape,
    fills: Arc<Mutex<Vec<Trade>>>,
}

impl JournalSink {
    fn wire(&self, event: &ExecutionEvent, now: UnixNanos) -> Option<Event> {
        Some(match event {
            ExecutionEvent::Accepted {
                order_id,
                venue_order_id,
                ..
            } => Event::OrderAccepted {
                order_id: order_id.clone(),
                venue_order_id: venue_order_id.clone(),
                ts_event: now,
            },
            ExecutionEvent::Rejected {
                order_id, reason, ..
            } => Event::OrderRejected {
                order_id: order_id.clone(),
                reason: reason.clone(),
                ts_event: now,
            },
            ExecutionEvent::Fill {
                trade,
                complete: true,
                ..
            } => Event::OrderFilled {
                order_id: trade.order_id().clone(),
                last_qty: trade.quantity(),
                last_px: trade.price(),
                ts_event: now,
            },
            ExecutionEvent::Fill { trade, cum_qty, .. } => Event::OrderPartiallyFilled {
                order_id: trade.order_id().clone(),
                last_qty: trade.quantity(),
                last_px: trade.price(),
                cum_qty: *cum_qty,
                ts_event: now,
            },
            ExecutionEvent::CancelRequested { order_id, .. } => Event::OrderCancelRequested {
                order_id: order_id.clone(),
                ts_event: now,
            },
            ExecutionEvent::Cancelled { order_id, .. } => Event::OrderCancelled {
                order_id: order_id.clone(),
                ts_event: now,
            },
            ExecutionEvent::Expired { order_id, .. } => Event::OrderExpired {
                order_id: order_id.clone(),
                ts_event: now,
            },
            // `Submitted` is recorded as the order itself, at submit.
            _ => return None,
        })
    }
}

impl Handler for JournalSink {
    fn on_start(&mut self) -> EngineResult<()> {
        self.inner.on_start()
    }

    fn on_event(&mut self, event: &Event, ts_init: UnixNanos) -> EngineResult<EngineOutput> {
        self.inner.on_event(event, ts_init)
    }

    fn on_stop(&mut self) -> EngineResult<()> {
        self.inner.on_stop()
    }
}

impl ExecutionEngine for JournalSink {
    fn submit(&mut self, order: Order) -> EngineResult<()> {
        let now = lock(&self.tape).now;
        // Never earlier than the kernel clock, like `Engine`'s own `Order` record.
        let recorded = if order.ts_event() < now {
            let mut o = Order::new(
                order.order_id().clone(),
                order.instrument_id().clone(),
                order.side(),
                order.order_type(),
                order.quantity(),
                order.price(),
                order.time_in_force(),
                now,
                order.ts_init(),
            );
            if let Some(trigger) = order.trigger_price() {
                o = o.with_trigger_price(trigger);
            }
            o
        } else {
            order.clone()
        };
        lock(&self.tape).records.push(Message::new(
            Event::Order(recorded.with_status(OrderStatus::Submitted)),
            now,
        ));
        self.inner.submit(order)
    }

    fn cancel(&mut self, order_id: &str, now: UnixNanos) -> EngineResult<()> {
        self.inner.cancel(order_id, now)
    }

    fn drain_events(&mut self) -> EngineResult<Vec<ExecutionEvent>> {
        let events = self.inner.drain_events()?;
        let now = lock(&self.tape).now;
        for event in &events {
            if let ExecutionEvent::Fill { trade, .. } = event {
                lock(&self.fills).push(trade.clone());
            }
            if let Some(wire) = self.wire(event, now) {
                lock(&self.tape).records.push(Message::new(wire, now));
            }
        }
        Ok(events)
    }

    fn native_events(&self) -> bool {
        true
    }

    fn drain_fills(&mut self) -> EngineResult<Vec<Trade>> {
        self.inner.drain_fills()
    }
}

// ---------------------------------------------------------------------------------------
// Metrics.
// ---------------------------------------------------------------------------------------

/// Pairs fills two at a time (entry, exit) in fill order; a trailing open fill is dropped.
fn round_trips(fills: &[Trade]) -> Vec<RoundTrip> {
    fills
        .chunks(2)
        .filter_map(|pair| match pair {
            [entry, exit] => RoundTrip::from_fills(entry, exit).ok(),
            _ => None,
        })
        .collect()
}

fn metrics(fills: &[Trade], initial_capital: f64) -> Result<BacktestMetrics, ErrorDetail> {
    let trips = round_trips(fills);
    if trips.is_empty() {
        return Ok(BacktestMetrics::default());
    }
    let mut equity = initial_capital;
    let returns: Vec<f64> = trips
        .iter()
        .map(|trip| {
            let before = equity;
            equity += trip.net_pnl;
            if before == 0.0 {
                0.0
            } else {
                (equity - before) / before
            }
        })
        .collect();
    let analytics = |e: honba_analytics::AnalyticsError| internal("analytics", e.to_string());
    let trade_stats = TradeStats::from_round_trips(&trips).map_err(analytics)?;
    let equity_stats =
        EquityStats::from_returns(&returns, PERIODS_PER_YEAR, 0.0).map_err(analytics)?;
    Ok(BacktestMetrics {
        trades: trips.len() as u64,
        net_pnl: trade_stats.total_pnl,
        sharpe: equity_stats.sharpe.unwrap_or(0.0),
        max_drawdown: equity_stats.max_drawdown_pct,
        total_return: trade_stats.total_pnl / initial_capital,
    })
}

fn assumptions() -> Value {
    json!({
        "not_modelled": [
            "transaction_costs",
            "slippage",
            "market_impact",
            "partial_fills",
            "order_acknowledgement_latency",
            "mark_to_market_of_open_positions",
            "corporate_actions",
            "market_calendar"
        ],
        "timing": "market orders fill at the close of the bar on which the strategy decided",
        "fill_model": "bar_fill",
        "metrics_basis": "closed_round_trips",
        "periods_per_year": PERIODS_PER_YEAR
    })
}

// ---------------------------------------------------------------------------------------
// The run.
// ---------------------------------------------------------------------------------------

fn drain_tape(
    tape: &SharedTape,
    journal: &mut dyn JournalWriter,
    unflushed: &mut usize,
) -> Result<(), ErrorDetail> {
    let records = std::mem::take(&mut lock(tape).records);
    for record in &records {
        journal.append(record)?;
    }
    *unflushed += records.len();
    if *unflushed >= FLUSH_EVERY {
        journal.flush()?;
        *unflushed = 0;
    }
    Ok(())
}

impl BacktestExecutor {
    /// The registered name to run: the job's own id, or the name of its catalog IR.
    fn registered_name(&self, job: &RunJob) -> Result<&'static str, ErrorDetail> {
        [
            job.strategy_id.as_str(),
            job.strategy.manifest.name.as_str(),
        ]
        .into_iter()
        .find_map(|name| self.registry.names().into_iter().find(|n| *n == name))
        .ok_or_else(|| {
            invalid(
                "strategy",
                "no_rust_implementation",
                "strategy has no registered Rust implementation",
            )
        })
    }

    pub(crate) fn account_currency(&self) -> Result<Currency, ErrorDetail> {
        match self.account.currency.as_str() {
            "INR" => Ok(Currency::Inr),
            other => Err(ErrorDetail::new(
                ErrorCode::Unsupported,
                format!("account currency {other:?} is not supported"),
            )),
        }
    }

    fn read_bars(
        &self,
        instrument: &InstrumentId,
        spec: &ResolvedBacktest,
    ) -> Result<Vec<honba_messages::Bar>, ErrorDetail> {
        let bar_spec = parse_timeframe(&spec.bar_spec)
            .map_err(|e| invalid("bar_spec", "invalid_bar_spec", e.message))?;
        let request = BarRequest::new(
            instrument.clone(),
            bar_spec,
            Some(spec.start),
            Some(spec.end),
        )
        .map_err(|e| port_error(&e))?;
        let runtime = tokio::runtime::Builder::new_current_thread()
            .build()
            .map_err(|e| internal("runtime", format!("cannot start a reader runtime: {e}")))?;
        let bars = runtime
            .block_on(self.bars.read_bars(&request))
            .map_err(|e| port_error(&e))?;
        if bars.is_empty() {
            return Err(ErrorDetail::new(
                ErrorCode::MarketDataUnavailable,
                "no bars in the window",
            )
            .with_context(json!({"reason": "no_data"})));
        }
        Ok(bars)
    }

    fn backtest(
        &self,
        job: &RunJob,
        spec: &ResolvedBacktest,
        journal: &mut dyn JournalWriter,
    ) -> Result<RunOutcome, ErrorDetail> {
        if job.seed == 0 {
            return Err(invalid("seed", "zero_seed", "seed must be non-zero"));
        }
        if !matches!(self.execution.fill_model, FillModel::BarFill) {
            return Err(ErrorDetail::new(
                ErrorCode::Unsupported,
                "only the bar_fill model is available",
            ));
        }
        let currency = self.account_currency()?;
        let name = self.registered_name(job)?;
        let instrument = parse_universe(&spec.universe)?;
        let bars = self.read_bars(&instrument, spec)?;
        let strategy = self
            .registry
            .build(name, &instrument, job.seed)
            .ok_or_else(|| internal("registry", "registered strategy failed to build"))?;
        let cash = Money::from_major_f64(spec.initial_capital, currency)
            .map_err(|e| invalid("initial_capital", "not_representable", e.to_string()))?;

        let tape: SharedTape = Arc::default();
        let sink = JournalSink {
            inner: BarFillEngine::new().with_currency(currency),
            tape: Arc::clone(&tape),
            fills: Arc::default(),
        };
        let runner = StrategyRunner::with_context(
            DynStrategy::new(strategy),
            sink.clone(),
            LedgerContext::with_cash(cash),
        )
        .with_warmup_bars(job.strategy.warmup_bars);

        let mut engine = Engine::new();
        engine.add_handler(Recorder(Arc::clone(&tape)));
        engine.add_handler(sink.clone());
        engine.add_handler(runner);

        // Taken from the data and nothing else, like every `honba-sweep` trial.
        let ts_init = bars[0].ts_event();
        let mut unflushed = 0;
        engine.start().map_err(|e| engine_error(&e))?;
        for bar in bars {
            engine.inject(Message::new(Event::Bar(bar), ts_init));
            while engine.pump().map_err(|e| engine_error(&e))? {}
            drain_tape(&tape, journal, &mut unflushed)?;
        }
        engine.finish().map_err(|e| engine_error(&e))?;
        drain_tape(&tape, journal, &mut unflushed)?;
        journal.flush()?;

        let fills = std::mem::take(&mut *lock(&sink.fills));
        Ok(RunOutcome::Backtest {
            metrics: metrics(&fills, spec.initial_capital)?,
            assumptions: assumptions(),
        })
    }
}

impl RunExecutor for BacktestExecutor {
    fn execute(
        &self,
        job: RunJob,
        journal: &mut dyn JournalWriter,
    ) -> Result<RunOutcome, ErrorDetail> {
        match &job.request {
            ResolvedRequest::Backtest(spec) => self.backtest(&job, spec, journal),
            ResolvedRequest::Sweep(_) => Err(ErrorDetail::new(
                ErrorCode::NotImplemented,
                "sweeps are not executed by this executor yet",
            )),
        }
    }
}
