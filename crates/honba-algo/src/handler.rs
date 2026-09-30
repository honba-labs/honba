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
    fn on_event(&mut self, event: &Event, ts_init: UnixNanos) -> Result<()>;

    /// Called once at the end of a run.
    fn on_stop(&mut self) -> Result<()> {
        Ok(())
    }
}

/// A `Handler` that does nothing. Useful as a default.
pub struct NoopHandler;

impl Handler for NoopHandler {
    fn on_event(&mut self, _event: &Event, _ts_init: UnixNanos) -> Result<()> {
        Ok(())
    }
}
