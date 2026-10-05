//! Unit tests for this crate, one file per area.

mod bar;
mod error_taxonomy;
mod event;
mod identifiers;
mod order;
mod tick;
mod validation;

use crate::{Exchange, InstrumentId};

/// A placeholder instrument for tests where the instrument does not matter.
fn any_instrument() -> InstrumentId {
    InstrumentId::new("X", Exchange::new("NSE"))
}
