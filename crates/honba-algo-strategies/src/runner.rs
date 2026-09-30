//! The `StrategyRunner`: glues a strategy to an execution engine.

use honba_algo::{ExecutionEngine, Handler, Result};
use honba_entities::Trade;
use honba_messages::{Event, OrderId, UnixNanos};

use crate::strategy::{Strategy, StrategyAdapter};

/// Wraps a [`Strategy`] with an [`ExecutionEngine`], closing the loop:
///
/// 1. Dispatch the incoming event to the strategy.
/// 2. Drain any [`OrderIntent`](crate::OrderIntent)s the strategy produced.
/// 3. Convert them into orders with generated ids and submit them.
/// 4. Drain fills from the execution engine and feed them back to the
///    strategy via [`Strategy::on_fill`](crate::Strategy::on_fill).
///
/// Accumulated fills are available after the run via [`Self::fills`].
///
/// ```
/// use honba_algo::Engine;
/// use honba_algo_strategies::{BuyAndHold, StrategyRunner};
/// use honba_algo_testing::{BarFillEngine, VecFeed};
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
    fills: Vec<Trade>,
}

impl<S: Strategy, E: ExecutionEngine> StrategyRunner<S, E> {
    /// Creates a runner from a strategy and an execution engine.
    pub fn new(strategy: S, execution: E) -> Self {
        Self {
            adapter: StrategyAdapter::new(strategy),
            execution,
            order_seq: 0,
            fills: Vec::new(),
        }
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

    /// Consumes the runner, returning the strategy, execution engine, and fills.
    pub fn into_parts(self) -> (S, E, Vec<Trade>) {
        let Self { adapter, execution, fills, .. } = self;
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
        // 1. Dispatch to the strategy.
        self.adapter.on_event(event, ts_init)?;

        // 2. Drain intents and submit them.
        let intents = self.adapter.drain_intents();
        for intent in intents {
            let id = self.next_order_id();
            let order = intent.into_order(id, ts_init);
            self.execution.submit(order)?;
        }

        // 3. Drain fills and feed them back to the strategy.
        let new_fills = self.execution.drain_fills()?;
        for fill in &new_fills {
            self.adapter.inner_mut().on_fill(fill)?;
        }
        self.fills.extend(new_fills);

        Ok(())
    }

    fn on_stop(&mut self) -> Result<()> {
        self.adapter.on_stop()
    }
}
