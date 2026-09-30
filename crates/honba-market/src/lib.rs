#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Generic market contracts and pluggable market packs for the Honba platform.
//!
//! Provides the generic traits for trading calendars, transaction costs, and universes,
//! along with concrete market pack implementations such as `india` and `null`.

#[cfg(feature = "india")]
pub mod india;
pub mod null;

// Expose generic contracts at root level
pub use india::calendar::source::{HolidaySource, TradingCalendar};
pub use india::costs::source::CostModelSource;
pub use india::error::{IndiaError, Result};
pub use india::universes::source::{Universe, UniverseSource};

// Aliases for modules to support backward compatibility
/// Calendar contracts and models
pub mod calendar {
    pub use crate::india::calendar::*;
}

/// Cost contracts and models
pub mod costs {
    pub use crate::india::costs::*;
}

/// Universe contracts and models
pub mod universes {
    pub use crate::india::universes::*;
}
