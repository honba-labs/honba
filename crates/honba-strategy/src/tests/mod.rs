//! Unit tests for this crate, one file per area.

mod context;
mod dyn_strategy;
mod intent;
mod ledger;
mod manifest;

use honba_messages::{Exchange, InstrumentId};

/// A placeholder instrument for tests where the instrument does not matter.
fn any_instrument() -> InstrumentId {
    InstrumentId::new("X", Exchange::new("NSE"))
}
