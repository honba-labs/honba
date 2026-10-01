//! Unit tests for this crate, one file per area.

mod clock;
mod queue;

use honba_messages::{InstrumentId, Venue};

/// A placeholder instrument for tests where the instrument does not matter.
fn any_instrument() -> InstrumentId {
    InstrumentId::new("X", Venue::new("NSE"))
}
