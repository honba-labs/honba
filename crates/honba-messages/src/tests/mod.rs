//! Unit tests for this crate, one file per area.

mod bar;
mod endpoints;
mod error_taxonomy;
mod errors;
mod event;
mod identifiers;
mod order;
mod tick;
mod timestamp;
mod validation;

use crate::{Exchange, InstrumentId};

/// A placeholder instrument for tests where the instrument does not matter.
fn any_instrument() -> InstrumentId {
    InstrumentId::new("X", Exchange::new("NSE"))
}
