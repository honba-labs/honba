//! The `StrategyRunner`: glues a strategy to an execution engine.

use std::collections::HashMap;

use honba_engine::{
    AlgoError, AuditKind, AuditLog, ExecutionEngine, Handler, OrderRejection, Result,
};
use honba_entities::{ExecutionEvent, Trade};
use honba_messages::{
    Event, InstrumentId, Order, OrderEvent, OrderId, OrderSide, OrderState, OrderStatus,
    TradingState, UnixNanos,
};
use honba_risk::{
    check_state, RiskCheck, RiskDecision, RiskLimits, RiskRefusal, RiskRequest, RiskStage,
};

use crate::context::{LedgerContext, StrategyContext};
use crate::intent::{IntentError, OrderIntent};
use crate::strategy::{Strategy, StrategyAdapter};

/// An intent the runner turned into an order and submitted.
#[derive(Clone, Debug, PartialEq)]
pub struct SubmittedIntent {
    /// The `ts_init` of the event during which it was submitted (the order's timestamp).
    pub ts_init: UnixNanos,
    /// The intent, as the strategy submitted it.
    pub intent: OrderIntent,
    /// The id of the order it became (`"{strategy name}-{n}"`).
    pub order_id: OrderId,
}

/// A valid intent the warm-up gate released instead of submitting.
#[derive(Clone, Debug, PartialEq)]
pub struct SuppressedIntent {
    /// The `ts_init` of the event during which it was drained.
    pub ts_init: UnixNanos,
    /// The intent, as the strategy submitted it.
    pub intent: OrderIntent,
}

/// An intent the runner refused to turn into an order.
#[derive(Clone, Debug, PartialEq)]
pub struct IntentRejection {
    /// The rejected intent, as the strategy emitted it.
    pub intent: OrderIntent,
    /// Which invariant it broke.
    pub error: IntentError,
    /// The `ts_init` of the event during which it was emitted.
    pub ts_init: UnixNanos,
}

/// Wraps a [`Strategy`] with an [`ExecutionEngine`], closing the loop
/// (ADR 008; the Python `StrategyRunner` follows the same steps):
///
/// 1. Set the context clock to the event's `ts_init` and dispatch a bar,
///    quote or trade event to its hook (other events reach no hook).
/// 2. Drain every [`OrderIntent`] submitted since the last drain, including
///    those from [`Strategy::on_start`] and from the previous event's
///    [`Strategy::on_fill`].
/// 3. Validate them, convert them into orders with ids `"{name}-{n}"` and
///    submit them. An intent that breaks the [`OrderIntent`] invariants is
///    not submitted: it is released in the context, recorded as an
///    [`IntentRejection`] (see [`Self::rejections`]) and reported through
///    [`Strategy::on_intent_rejected`]; the run continues.
/// 4. Drain the execution engine's one ordered event stream
///    ([`ExecutionEngine::drain_events`], ADR 0019 decision 4) and walk it in
///    order, keeping an [`OrderState`] per order: a fill is booked in the
///    context, then passed to [`Strategy::on_fill`]; a rejection, cancellation
///    or expiry releases the unfilled remainder it carries (for the
///    instrument and side it names) and is recorded as an
///    [`OrderRejection`]. A repeated or illegal terminal event for an order
///    releases nothing, so `filled + released == ordered` per order.
///
/// Intents submitted in [`Strategy::on_stop`] are discarded. Submitted
/// intents and fills are available via [`Self::submitted`] and [`Self::fills`].
///
/// **Warm-up gate** ([`StrategyManifest::warmup_bars`](crate::StrategyManifest),
/// set with [`Self::with_warmup_bars`]). A *driving bar* is a bar event whose
/// `ts_init` differs from the previous bar event's, so bars of several
/// instruments at one time count once. While at most `warmup_bars` driving
/// bars have been seen (and before the first one), the strategy still receives
/// every event, but each valid intent is released in the context and recorded
/// as a [`SuppressedIntent`] (see [`Self::suppressed`]) instead of becoming an
/// order; it consumes no order id. Invalid intents are rejected as usual. The
/// Python runner follows the same rule; both run
/// `schema/conformance/warmup_gate.json`.
///
/// ```
/// use honba_engine::Engine;
/// use honba_sim::BarFillEngine;
/// use honba_strategy::{BuyAndHold, StrategyRunner};
/// use honba_testing::VecFeed;
/// use honba_messages::{InstrumentId, Exchange};
///
/// let strategy = BuyAndHold::new(InstrumentId::new("X", Exchange::new("NSE")), 10.0);
/// let execution = BarFillEngine::new();
///
/// let mut engine = Engine::new();
/// engine.add_handler(execution.clone());          // sees bars, updates last price
/// engine.add_handler(StrategyRunner::new(strategy, execution.clone()));
///
/// // ...run with a feed...
/// ```
pub struct StrategyRunner<S: Strategy, E: ExecutionEngine> {
    adapter: StrategyAdapter<S>,
    execution: E,
    order_seq: u64,
    submitted: Vec<SubmittedIntent>,
    fills: Vec<Trade>,
    rejections: Vec<IntentRejection>,
    order_rejections: Vec<OrderRejection>,
    warmup_bars: u32,
    bars_seen: u64,
    last_bar_ts: Option<UnixNanos>,
    /// `ts_init` of the latest event: the time a cancel is processed at.
    now: UnixNanos,
    suppressed: Vec<SuppressedIntent>,
    states: HashMap<String, OrderState>,
    released: HashMap<InstrumentId, f64>,
    risk: Option<RiskStage>,
    trading_state: TradingState,
    audit: AuditLog,
    /// Instrument and side per order id: what working exposure needs.
    sides: HashMap<String, (InstrumentId, OrderSide)>,
    /// Last bar close or trade price per instrument: the stage's `reference_price`.
    last_px: HashMap<InstrumentId, f64>,
    /// Pre-gate `Rejected` events, booked ahead of the next drain.
    pre_gate: Vec<ExecutionEvent>,
}

impl<S: Strategy, E: ExecutionEngine> StrategyRunner<S, E> {
    /// Creates a runner from a strategy and an execution engine, with an
    /// empty [`LedgerContext`].
    pub fn new(strategy: S, execution: E) -> Self {
        Self::with_context(strategy, execution, LedgerContext::new())
    }

    /// Creates a runner whose strategy reads and writes `ctx` (initial cash,
    /// instrument metadata).
    pub fn with_context(strategy: S, execution: E, ctx: LedgerContext) -> Self {
        Self {
            adapter: StrategyAdapter::with_context(strategy, ctx),
            execution,
            order_seq: 0,
            submitted: Vec::new(),
            fills: Vec::new(),
            rejections: Vec::new(),
            order_rejections: Vec::new(),
            warmup_bars: 0,
            bars_seen: 0,
            last_bar_ts: None,
            now: UnixNanos::from_u64(0),
            suppressed: Vec::new(),
            states: HashMap::new(),
            released: HashMap::new(),
            risk: None,
            trading_state: TradingState::Active,
            audit: AuditLog::new(),
            sides: HashMap::new(),
            last_px: HashMap::new(),
            pre_gate: Vec::new(),
        }
    }

    /// Puts `stage` in front of the execution engine: every order the runner submits passes it,
    /// and a refusal is audited ([`Self::audit`]) as [`AuditKind::RiskRefused`] then
    /// [`AuditKind::OrderRejected`], never reaches the execution engine, and is booked as an
    /// [`OrderRejection`] whose reason is the `ErrorCode` wire spelling (ADR 0018 decisions
    /// 6-7).
    ///
    /// Without a stage the runner still applies the state rules (`Halted`, reduce-only). A run
    /// holds at most one stage: the runner reports it through [`Handler::holds_risk_stage`], so
    /// an `Engine` that already has one refuses to start.
    #[must_use]
    pub fn with_risk(mut self, stage: RiskStage) -> Self {
        self.risk = Some(stage);
        self
    }

    /// The runner's own audit trail: one [`AuditKind::RiskRefused`] and one
    /// [`AuditKind::OrderRejected`] per order it refused before submit. The `Engine`'s log
    /// does not include them (the runner bypasses `Engine::submit`).
    pub fn audit(&self) -> &AuditLog {
        &self.audit
    }

    /// The trading state the runner enforces: [`TradingState::Active`] until the hosting engine
    /// reports a change through [`Handler::on_trading_state`].
    pub fn trading_state(&self) -> TradingState {
        self.trading_state
    }

    /// The live-run guard (ADR 0018 decision 8): `Ok` only when the runner's stage was built
    /// with both `max_notional` and `order_rate`. An assembler building a non-simulated run
    /// calls this before starting; a runner without a stage fails like one without limits.
    pub fn require_live_limits(&self) -> Result<()> {
        let limits = match &self.risk {
            Some(stage) => stage.limits().clone(),
            None => RiskLimits::default(),
        };
        limits.require_live().map_err(AlgoError::from)
    }

    /// The signed open quantity (`+` buy, `-` sell) of this runner's working orders on
    /// `instrument_id` and `side` (ADR 0019 decision 5).
    fn working_exposure(&self, instrument_id: &InstrumentId, side: OrderSide) -> f64 {
        let sign = match side {
            OrderSide::Buy => 1.0,
            OrderSide::Sell => -1.0,
            _ => return 0.0,
        };
        sign * self
            .states
            .iter()
            .filter(|(_, st)| {
                matches!(
                    st.status,
                    OrderStatus::Submitted | OrderStatus::Accepted | OrderStatus::PartiallyFilled
                )
            })
            .filter(|(id, _)| {
                self.sides
                    .get(*id)
                    .is_some_and(|(i, s)| i == instrument_id && *s == side)
            })
            .map(|(_, st)| st.quantity - st.filled_qty)
            .sum::<f64>()
    }

    /// Runs the stage (or, without one, the state rules) over `order`, which is being submitted
    /// during the event at `ts_init`.
    fn risk_check(&mut self, order: &Order, ts_init: UnixNanos) -> Option<RiskRefusal> {
        let instrument = order.instrument_id();
        let request = RiskRequest {
            order_id: order.order_id().clone(),
            instrument_id: instrument.clone(),
            side: order.side(),
            quantity: order.quantity(),
            price: order.price(),
            trigger_price: order.trigger_price(),
            reference_price: self.last_px.get(instrument).copied(),
            position: self.adapter.context().position(instrument)
                + self.working_exposure(instrument, order.side()),
            trading_state: self.trading_state,
            ts: ts_init,
        };
        match self.risk.as_mut() {
            Some(stage) => match stage.check(&request) {
                RiskDecision::Approved => None,
                RiskDecision::Refused(refusal) => Some(refusal),
            },
            None => check_state(&request),
        }
    }

    /// Refuses `order` before it reaches the execution engine (ADR 0018 decision 6):
    /// audited, `Initialized -> Rejected`, and a `Rejected` event queued ahead of the next
    /// drain so the refusal travels the same stream as a venue reject.
    fn refuse(&mut self, order: &Order, refusal: RiskRefusal, ts_init: UnixNanos) {
        let order_id = order.order_id().as_str().to_string();
        let reason = refusal.error_code().as_str().to_string();
        self.audit.record(AuditKind::RiskRefused {
            order_id: order_id.clone(),
            refusal,
        });
        self.audit.record(AuditKind::OrderRejected {
            order_id: order_id.clone(),
            reason: reason.clone(),
        });
        self.states.insert(order_id.clone(), OrderState::new());
        self.sides
            .insert(order_id, (order.instrument_id().clone(), order.side()));
        self.pre_gate.push(ExecutionEvent::Rejected {
            order_id: order.order_id().clone(),
            instrument_id: order.instrument_id().clone(),
            side: order.side(),
            quantity: order.quantity(),
            reason,
            venue_order_id: None,
            ts: ts_init,
        });
    }

    /// Suppresses orders until `warmup_bars` driving bars have been seen.
    #[must_use]
    pub fn with_warmup_bars(mut self, warmup_bars: u32) -> Self {
        self.warmup_bars = warmup_bars;
        self
    }

    /// The number of warm-up bars this runner enforces.
    pub fn warmup_bars(&self) -> u32 {
        self.warmup_bars
    }

    /// True while orders are suppressed: at most `warmup_bars` driving bars seen.
    pub fn warming_up(&self) -> bool {
        self.warmup_bars > 0 && self.bars_seen <= u64::from(self.warmup_bars)
    }

    /// Returns every intent the warm-up gate released, in order.
    pub fn suppressed(&self) -> &[SuppressedIntent] {
        &self.suppressed
    }

    /// Returns the strategy's context (clock, positions, cash).
    pub fn context(&self) -> &LedgerContext {
        self.adapter.context()
    }

    /// Returns every intent submitted as an order, in order.
    pub fn submitted(&self) -> &[SubmittedIntent] {
        &self.submitted
    }

    /// Returns a shared reference to the wrapped strategy.
    pub fn strategy(&self) -> &S {
        self.adapter.inner()
    }

    /// Returns a mutable reference to the wrapped strategy.
    pub fn strategy_mut(&mut self) -> &mut S {
        self.adapter.inner_mut()
    }

    /// Returns all fills produced during the run.
    pub fn fills(&self) -> &[Trade] {
        &self.fills
    }

    /// Returns every intent rejected during the run, in emission order.
    pub fn rejections(&self) -> &[IntentRejection] {
        &self.rejections
    }

    /// Returns every order (or part of one) the execution engine rejected or
    /// cancelled, in drain order. Each was released in the context.
    pub fn order_rejections(&self) -> &[OrderRejection] {
        &self.order_rejections
    }

    /// The FSM state the runner tracked for `order_id`, if it submitted it.
    pub fn order_state(&self, order_id: &str) -> Option<&OrderState> {
        self.states.get(order_id)
    }

    /// The total quantity released for `instrument_id` so far: the unfilled
    /// remainders of its rejected, cancelled and expired orders.
    pub fn released_quantity(&self, instrument_id: &InstrumentId) -> f64 {
        self.released.get(instrument_id).copied().unwrap_or(0.0)
    }

    /// Asks the execution engine to cancel `order_id` and books what it
    /// reports at once (a cancelled [`OrderRejection`] for the unfilled
    /// remainder, stamped with the time of the latest event, after any fill
    /// the engine produced first). Cancelling an unknown or finished order
    /// does nothing.
    pub fn cancel(&mut self, order_id: &str) -> Result<()> {
        self.execution.cancel(order_id, self.now)?;
        if let Some(state) = self.states.get_mut(order_id) {
            // Duplicate or illegal (finished order): the FSM keeps its state.
            let _ = state.apply(&OrderEvent::CancelRequested);
        }
        self.book_events()
    }

    /// Drains the engine once and walks the stream in order. A failure does
    /// not lose the other events: each is still booked and the first error is
    /// returned afterwards.
    fn book_events(&mut self) -> Result<()> {
        let mut events = std::mem::take(&mut self.pre_gate);
        events.extend(self.execution.drain_events()?);
        let mut first_error = None;
        for event in events {
            match event {
                ExecutionEvent::Fill { trade, .. } => {
                    if let Some(state) = self.states.get_mut(trade.order_id().as_str()) {
                        // An illegal fill (overfill) leaves the FSM unchanged.
                        let _ = state.apply(&event_fill(&trade, state));
                    }
                    let (strategy, ctx) = self.adapter.parts_mut();
                    if let Err(e) = ctx.apply_fill(&trade) {
                        first_error
                            .get_or_insert(AlgoError::Component(format!("booking fill: {e}")));
                        continue;
                    }
                    if let Err(e) = strategy.on_fill(ctx, &trade) {
                        first_error.get_or_insert(e);
                    }
                    self.fills.push(trade);
                }
                ExecutionEvent::Rejected {
                    ref order_id,
                    ref instrument_id,
                    side,
                    quantity,
                    ref reason,
                    ts,
                    ..
                } => {
                    let r = OrderRejection::rejected(
                        order_id.clone(),
                        instrument_id.clone(),
                        side,
                        quantity,
                        reason.clone(),
                        ts,
                    );
                    self.release(&event, r);
                }
                ExecutionEvent::Cancelled {
                    ref order_id,
                    ref instrument_id,
                    side,
                    quantity,
                    ts,
                    ..
                } => {
                    let r = OrderRejection::cancelled(
                        order_id.clone(),
                        instrument_id.clone(),
                        side,
                        quantity,
                        ts,
                    );
                    self.release(&event, r);
                }
                ExecutionEvent::Expired {
                    ref order_id,
                    ref instrument_id,
                    side,
                    quantity,
                    ts,
                    ..
                } => {
                    let r = OrderRejection::rejected(
                        order_id.clone(),
                        instrument_id.clone(),
                        side,
                        quantity,
                        OrderRejection::EXPIRED,
                        ts,
                    );
                    self.release(&event, r);
                }
                // Acknowledgements move the FSM only; the rest is synthesised
                // by a submitter and carries nothing to book.
                other => {
                    if let Some(state) = self.states.get_mut(other.order_id().as_str()) {
                        let _ = state.apply(&other.order_event());
                    }
                }
            }
        }
        first_error.map_or(Ok(()), Err)
    }

    /// Applies a terminal `event` to the order's FSM and, unless it is a
    /// duplicate or illegal, releases the remainder and records `r`.
    fn release(&mut self, event: &ExecutionEvent, r: OrderRejection) {
        if let Some(state) = self.states.get_mut(r.order_id.as_str()) {
            if !matches!(state.apply(&event.order_event()), Ok(true)) {
                return;
            }
        }
        self.adapter
            .parts_mut()
            .1
            .release_remainder(&r.instrument_id, r.side, r.quantity);
        if r.quantity.is_finite() && r.quantity > 0.0 {
            *self.released.entry(r.instrument_id.clone()).or_insert(0.0) += r.quantity;
        }
        self.order_rejections.push(r);
    }

    /// Consumes the runner, returning the strategy, execution engine, and fills.
    pub fn into_parts(self) -> (S, E, Vec<Trade>) {
        let Self {
            adapter,
            execution,
            fills,
            ..
        } = self;
        (adapter.into_inner(), execution, fills)
    }

    /// Validates and submits `intents` in order. Every intent it takes is
    /// either submitted, suppressed or released, including the one that fails;
    /// the caller releases the ones left in the iterator.
    fn submit_drained(
        &mut self,
        intents: &mut std::vec::IntoIter<OrderIntent>,
        ts_init: UnixNanos,
    ) -> Result<()> {
        for intent in intents.by_ref() {
            if let Err(error) = intent.validate() {
                let (strategy, ctx) = self.adapter.parts_mut();
                ctx.release(&intent);
                let hook = strategy.on_intent_rejected(ctx, &intent, &error);
                self.rejections.push(IntentRejection {
                    intent,
                    error,
                    ts_init,
                });
                hook?;
                continue;
            }
            if self.warming_up() {
                self.adapter.parts_mut().1.release(&intent);
                self.suppressed.push(SuppressedIntent { ts_init, intent });
                continue;
            }
            let id = self.next_order_id();
            let order = match intent.clone().into_order(id.clone(), ts_init) {
                Ok(order) => order,
                Err(e) => {
                    self.adapter.parts_mut().1.release(&intent);
                    return Err(AlgoError::Component(e.to_string()));
                }
            };
            if let Some(refusal) = self.risk_check(&order, ts_init) {
                self.refuse(&order, refusal, ts_init);
                continue;
            }
            if let Err(e) = self.execution.submit(order) {
                self.adapter.parts_mut().1.release(&intent);
                return Err(e);
            }
            let mut state = OrderState::new();
            let _ = state.apply(&OrderEvent::Submitted {
                quantity: intent.quantity,
            });
            self.states.insert(id.as_str().to_string(), state);
            self.sides.insert(
                id.as_str().to_string(),
                (intent.instrument_id.clone(), intent.side),
            );
            // Recorded only once the execution port accepted it (Python parity).
            self.submitted.push(SubmittedIntent {
                ts_init,
                intent,
                order_id: id,
            });
        }
        Ok(())
    }

    /// Remembers the last bar close or trade price per instrument for the risk request.
    fn observe_price(&mut self, event: &Event) {
        match event {
            Event::Bar(bar) => {
                self.last_px
                    .insert(bar.bar_type().instrument_id().clone(), bar.close());
            }
            Event::Trade(tick) => {
                self.last_px
                    .insert(tick.instrument_id().clone(), tick.price());
            }
            _ => {}
        }
    }

    fn next_order_id(&mut self) -> OrderId {
        let id = format!("{}-{}", self.adapter.inner().name(), self.order_seq);
        self.order_seq += 1;
        OrderId::new(id)
    }
}

impl<S: Strategy, E: ExecutionEngine> Handler for StrategyRunner<S, E> {
    fn on_start(&mut self) -> Result<()> {
        self.adapter.on_start()?;
        Ok(())
    }

    fn on_event(
        &mut self,
        event: &Event,
        ts_init: UnixNanos,
    ) -> Result<honba_engine::EngineOutput> {
        self.now = ts_init;
        // A new driving bar advances the warm-up count.
        if matches!(event, Event::Bar(_)) && self.last_bar_ts != Some(ts_init) {
            self.bars_seen += 1;
            self.last_bar_ts = Some(ts_init);
        }

        self.observe_price(event);

        // 1. Set the clock and dispatch to the strategy.
        self.adapter.on_event(event, ts_init)?;

        // 2-3. Drain intents, validate and submit them. An error is terminal
        // for the run, but the intents not yet submitted are released first so
        // the context never keeps an instrument busy for an order that was
        // never sent.
        let mut intents = self.adapter.drain_intents().into_iter();
        if let Err(e) = self.submit_drained(&mut intents, ts_init) {
            for intent in intents {
                self.adapter.parts_mut().1.release(&intent);
            }
            return Err(e);
        }

        // 4. Drain the one ordered event stream: book fills, release the rest.
        self.book_events()?;

        Ok(honba_engine::EngineOutput::None)
    }

    fn on_trading_state(&mut self, state: TradingState) {
        self.trading_state = state;
    }

    fn holds_risk_stage(&self) -> bool {
        self.risk.is_some()
    }

    fn on_stop(&mut self) -> Result<()> {
        self.adapter.on_stop()?;
        // Never executed: the run is over (ADR 008).
        self.adapter.drain_intents();
        Ok(())
    }
}

/// The FSM fill event for `trade` against `state`.
fn event_fill(trade: &Trade, state: &OrderState) -> OrderEvent {
    let complete = state.filled_qty + trade.quantity() + 1e-9 >= state.quantity;
    OrderEvent::Fill {
        last_qty: trade.quantity(),
        complete,
    }
}
