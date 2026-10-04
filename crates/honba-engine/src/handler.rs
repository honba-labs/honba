//! Event handler trait.

use honba_messages::{Event, UnixNanos};

use crate::error::Result;

/// Anything that reacts to events.
///
/// Handlers are called by the engine in `ts_event` order. Implementations
/// typically branch on [`Event`] variants they care about and ignore the rest.
pub trait Handler: Send {
    /// Called once at the start of a run.
    fn on_start(&mut self) -> Result<()> {
        Ok(())
    }

    /// Called for every event in the queue, in time order.
    fn on_event(&mut self, event: &Event, ts_init: UnixNanos) -> Result<EngineOutput>;

    /// Called once at the end of a run.
    fn on_stop(&mut self) -> Result<()> {
        Ok(())
    }
}

/// A `Handler` that does nothing. Useful as a default.
pub struct NoopHandler;

impl Handler for NoopHandler {
    fn on_event(&mut self, _event: &Event, _ts_init: UnixNanos) -> Result<EngineOutput> {
        Ok(EngineOutput::None)
    }
}

/// Output from a handler indicating what actions the engine should take.
#[derive(Debug, Clone, PartialEq)]
pub enum EngineOutput {
    /// No action to take.
    None,
    /// Submit new orders.
    Orders(Vec<honba_messages::Order>),
    /// Cancel orders.
    Cancels(Vec<honba_messages::OrderId>),
    /// Change trading state.
    StateChange(TradingState),
}

/// Trading state for the engine.
#[derive(Debug, Clone, PartialEq)]
pub enum TradingState {
    /// Normal trading.
    Active,
    /// Reducing positions.
    Reducing,
    /// Trading halted.
    Halted,
}
