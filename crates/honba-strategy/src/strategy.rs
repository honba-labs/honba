//! The `Strategy` trait and its adapter into the event kernel.

use honba_engine::{Handler, Result};
use honba_entities::Trade;
use honba_messages::{Bar, Event, QuoteTick, TradeTick, UnixNanos};

use crate::context::{LedgerContext, StrategyContext};
use crate::intent::{IntentError, OrderIntent};

/// A trading strategy (ADR 008).
///
/// Strategies receive typed callbacks for the events they care about and act
/// only through the [`StrategyContext`] passed to every hook: read the clock
/// ([`StrategyContext::now`]), positions, cash and instrument metadata, and
/// submit [`OrderIntent`]s. They never touch the execution engine, which keeps
/// them testable in isolation and lets the same strategy run against paper,
/// backtest or live execution.
///
/// All hooks default to no-ops. A strategy only overrides the ones it uses.
/// The hook set matches the Python `honba.strategies.base.Strategy`.
pub trait Strategy: Send + 'static {
    /// A stable, human-readable name. Order ids are `"{name}-{n}"`.
    fn name(&self) -> &str;

    /// Called once before the first event. Intents submitted here are
    /// processed with the first event.
    fn on_start(&mut self, _ctx: &mut dyn StrategyContext) -> Result<()> {
        Ok(())
    }

    /// Called for every [`Bar`] event.
    fn on_bar(&mut self, _ctx: &mut dyn StrategyContext, _bar: &Bar) -> Result<()> {
        Ok(())
    }

    /// Called for every [`QuoteTick`] event.
    fn on_quote(&mut self, _ctx: &mut dyn StrategyContext, _quote: &QuoteTick) -> Result<()> {
        Ok(())
    }

    /// Called for every [`TradeTick`] event (a market print; the strategy's
    /// own executions arrive in [`Strategy::on_fill`]).
    fn on_trade(&mut self, _ctx: &mut dyn StrategyContext, _trade: &TradeTick) -> Result<()> {
        Ok(())
    }

    /// Called for each fill of an order the strategy submitted, after the
    /// fill is booked in the context. Intents submitted here are processed
    /// with the next event.
    fn on_fill(&mut self, _ctx: &mut dyn StrategyContext, _fill: &Trade) -> Result<()> {
        Ok(())
    }

    /// Called when the runner rejects an intent the strategy submitted because
    /// it violates the [`OrderIntent`] invariants. No order was submitted for
    /// it and it no longer counts as busy. Default is a no-op.
    fn on_intent_rejected(
        &mut self,
        _ctx: &mut dyn StrategyContext,
        _intent: &OrderIntent,
        _error: &IntentError,
    ) -> Result<()> {
        Ok(())
    }

    /// Called once after the last event. Intents submitted here are never
    /// executed: square off on an event instead.
    fn on_stop(&mut self, _ctx: &mut dyn StrategyContext) -> Result<()> {
        Ok(())
    }
}

/// Wraps a [`Strategy`] and its [`LedgerContext`] so it can be added to an
/// [`honba_engine::Engine`].
///
/// The adapter sets the context clock to each event's `ts_init` and
/// translates [`Event`] variants into the strategy's typed hooks. Submitted
/// intents stay in the context until drained ([`Self::drain_intents`]);
/// [`StrategyRunner`](crate::StrategyRunner) does that and executes them.
///
/// ```
/// use honba_engine::Engine;
/// use honba_strategy::{BuyAndHold, StrategyAdapter};
/// use honba_messages::{InstrumentId, Venue};
///
/// let strategy = BuyAndHold::new(
///     InstrumentId::new("NIFTY50", Venue::new("NSE")),
///     75.0,
/// );
/// let adapter = StrategyAdapter::new(strategy);
/// let mut engine = Engine::new();
/// engine.add_handler(adapter);
/// ```
pub struct StrategyAdapter<S: Strategy> {
    inner: S,
    ctx: LedgerContext,
}

impl<S: Strategy> StrategyAdapter<S> {
    /// Wraps a strategy with an empty [`LedgerContext`].
    pub fn new(inner: S) -> Self {
        Self::with_context(inner, LedgerContext::new())
    }

    /// Wraps a strategy with the given context (initial cash, instruments).
    pub fn with_context(inner: S, ctx: LedgerContext) -> Self {
        Self { inner, ctx }
    }

    /// Returns a shared reference to the wrapped strategy.
    pub fn inner(&self) -> &S {
        &self.inner
    }

    /// Returns a mutable reference to the wrapped strategy.
    pub fn inner_mut(&mut self) -> &mut S {
        &mut self.inner
    }

    /// Returns the strategy's context.
    pub fn context(&self) -> &LedgerContext {
        &self.ctx
    }

    /// Returns the strategy's context mutably (for runners: fills, rejections).
    pub fn context_mut(&mut self) -> &mut LedgerContext {
        &mut self.ctx
    }

    /// Borrows the strategy and its context at the same time.
    pub fn parts_mut(&mut self) -> (&mut S, &mut LedgerContext) {
        (&mut self.inner, &mut self.ctx)
    }

    /// Drains the intents the strategy submitted.
    pub fn drain_intents(&mut self) -> Vec<OrderIntent> {
        self.ctx.drain_intents()
    }

    /// Consumes the adapter, returning the wrapped strategy.
    pub fn into_inner(self) -> S {
        self.inner
    }
}

impl<S: Strategy> Handler for StrategyAdapter<S> {
    fn on_start(&mut self) -> Result<()> {
        self.inner.on_start(&mut self.ctx)
    }

    fn on_event(&mut self, event: &Event, ts_init: UnixNanos) -> Result<()> {
        self.ctx.set_now(ts_init);
        match event {
            Event::Bar(b) => self.inner.on_bar(&mut self.ctx, b),
            Event::Quote(q) => self.inner.on_quote(&mut self.ctx, q),
            Event::Trade(t) => self.inner.on_trade(&mut self.ctx, t),
            _ => Ok(()),
        }
    }

    fn on_stop(&mut self) -> Result<()> {
        self.inner.on_stop(&mut self.ctx)
    }
}
