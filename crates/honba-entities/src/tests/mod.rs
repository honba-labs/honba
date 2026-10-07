//! Unit tests for this crate, one file per area.

mod currency;
mod execution;
mod instrument;
mod money;
mod position;
mod screener;
mod trade;

use honba_messages::{Exchange, InstrumentId};

/// A placeholder instrument for tests where the instrument does not matter.
fn any_instrument() -> InstrumentId {
    InstrumentId::new("X", Exchange::new("NSE"))
}
