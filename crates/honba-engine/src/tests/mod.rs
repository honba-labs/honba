//! Unit tests for this crate, one file per area.

mod audit;
mod clock;
mod engine;
mod execution;
mod queue;
mod state;

use honba_messages::{Exchange, InstrumentId};

/// A placeholder instrument for tests where the instrument does not matter.
fn any_instrument() -> InstrumentId {
    InstrumentId::new("X", Exchange::new("NSE"))
}
