//! The `StrategyRunner`: glues a strategy to an execution engine.

use honba_engine::{AlgoError, ExecutionEngine, Handler, Result};
use honba_entities::Trade;
use honba_messages::{Event, OrderId, UnixNanos};

use crate::context::LedgerContext;
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
/// 4. Drain fills from the execution engine, book each in the context, then
///    pass it to [`Strategy::on_fill`].
///
/// Intents submitted in [`Strategy::on_stop`] are discarded. Submitted
/// intents and fills are available via [`Self::submitted`] and [`Self::fills`].
///
/// ```
/// use honba_engine::Engine;
/// use honba_sim::BarFillEngine;
/// use honba_strategy::{BuyAndHold, StrategyRunner};
/// use honba_testing::VecFeed;
/// use honba_messages::{InstrumentId, Venue};
///
/// let strategy = BuyAndHold::new(InstrumentId::new("X", Venue::new("NSE")), 10.0);
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
        }
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

    fn next_order_id(&mut self) -> OrderId {
        let id = format!("{}-{}", self.adapter.inner().name(), self.order_seq);
        self.order_seq += 1;
        OrderId::new(id)
    }
}

impl<S: Strategy, E: ExecutionEngine> Handler for StrategyRunner<S, E> {
    fn on_start(&mut self) -> Result<()> {
        self.adapter.on_start()
    }

    fn on_event(&mut self, event: &Event, ts_init: UnixNanos) -> Result<()> {
        // 1. Set the clock and dispatch to the strategy.
        self.adapter.on_event(event, ts_init)?;

        // 2-3. Drain intents, validate and submit them.
        let intents = self.adapter.drain_intents();
        for intent in intents {
            if let Err(error) = intent.validate() {
                let (strategy, ctx) = self.adapter.parts_mut();
                ctx.release(&intent);
                strategy.on_intent_rejected(ctx, &intent, &error)?;
                self.rejections.push(IntentRejection {
                    intent,
                    error,
                    ts_init,
                });
                continue;
            }
            let id = self.next_order_id();
            self.submitted.push(SubmittedIntent {
                ts_init,
                intent: intent.clone(),
                order_id: id.clone(),
            });
            let order = intent
                .into_order(id, ts_init)
                .map_err(|e| AlgoError::Component(e.to_string()))?;
            self.execution.submit(order)?;
        }

        // 4. Drain fills, book them, and feed them back to the strategy.
        let new_fills = self.execution.drain_fills()?;
        let (strategy, ctx) = self.adapter.parts_mut();
        for fill in &new_fills {
            ctx.apply_fill(fill);
            strategy.on_fill(ctx, fill)?;
        }
        self.fills.extend(new_fills);

        Ok(())
    }

    fn on_stop(&mut self) -> Result<()> {
        self.adapter.on_stop()?;
        // Never executed: the run is over (ADR 008).
        self.adapter.drain_intents();
        Ok(())
    }
}
