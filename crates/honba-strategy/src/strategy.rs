//! The `Strategy` trait and its adapter into the event kernel.

use honba_engine::{Handler, Result};
use honba_messages::{Bar, Event, QuoteTick, TradeTick, UnixNanos};

use crate::intent::{IntentError, OrderIntent};

/// A trading strategy.
///
/// Strategies receive typed callbacks for the events they care about and
/// accumulate [`OrderIntent`]s via [`Strategy::drain_intents`]. They do not
/// touch the execution engine directly — that keeps them testable in
/// isolation and lets the same strategy run against paper, backtest, or
/// live execution.
///
/// All hooks default to no-ops. A strategy only overrides the ones it uses.
pub trait Strategy: Send + 'static {
    /// A stable, human-readable name.
    fn name(&self) -> &str;

    /// Called once before the first event.
    fn on_start(&mut self) -> Result<()> {
        Ok(())
    }

    /// Called for every [`Bar`] event.
    fn on_bar(&mut self, _bar: &Bar, _ts_init: UnixNanos) -> Result<()> {
        Ok(())
    }

    /// Called for every [`QuoteTick`] event.
    fn on_quote(&mut self, _quote: &QuoteTick, _ts_init: UnixNanos) -> Result<()> {
        Ok(())
    }

    /// Called for every [`TradeTick`] event.
    fn on_trade(&mut self, _trade: &TradeTick, _ts_init: UnixNanos) -> Result<()> {
        Ok(())
    }

    /// Called when the runner receives a fill for an order the strategy
    /// emitted. Default is a no-op.
    fn on_fill(&mut self, _fill: &honba_entities::Trade) -> Result<()> {
        Ok(())
    }

    /// Called when the runner rejects an intent the strategy emitted because
    /// it violates the [`OrderIntent`] invariants. No order was submitted for
    /// it. Default is a no-op.
    fn on_intent_rejected(&mut self, _intent: &OrderIntent, _error: &IntentError) -> Result<()> {
        Ok(())
    }

    /// Called once after the last event.
    fn on_stop(&mut self) -> Result<()> {
        Ok(())
    }

    /// Returns and clears any pending intents.
    ///
    /// The runner calls this after every event.
    fn drain_intents(&mut self) -> Vec<OrderIntent>;
}

/// Wraps a [`Strategy`] so it can be added to an [`honba_engine::Engine`].
///
/// The adapter translates [`Event`] variants into the strategy's typed
/// hooks. It also exposes access to the wrapped strategy so the caller can
/// drain intents after each step.
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
}

impl<S: Strategy> StrategyAdapter<S> {
    /// Wraps a strategy.
    pub fn new(inner: S) -> Self {
        Self { inner }
    }

    /// Returns a shared reference to the wrapped strategy.
    pub fn inner(&self) -> &S {
        &self.inner
    }

    /// Returns a mutable reference to the wrapped strategy.
    pub fn inner_mut(&mut self) -> &mut S {
        &mut self.inner
    }

    /// Drains intents from the wrapped strategy.
    pub fn drain_intents(&mut self) -> Vec<OrderIntent> {
        self.inner.drain_intents()
    }

    /// Consumes the adapter, returning the wrapped strategy.
    pub fn into_inner(self) -> S {
        self.inner
    }
}

impl<S: Strategy> Handler for StrategyAdapter<S> {
    fn on_start(&mut self) -> Result<()> {
        self.inner.on_start()
    }

    fn on_event(&mut self, event: &Event, ts_init: UnixNanos) -> Result<()> {
        match event {
            Event::Bar(b) => self.inner.on_bar(b, ts_init),
            Event::Quote(q) => self.inner.on_quote(q, ts_init),
            Event::Trade(t) => self.inner.on_trade(t, ts_init),
            _ => Ok(()),
        }
    }

    fn on_stop(&mut self) -> Result<()> {
        self.inner.on_stop()
    }
}
