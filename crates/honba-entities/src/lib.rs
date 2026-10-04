#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Domain entities for the Honba trading platform.
//!
//! Where [`honba_messages`] holds immutable value types that flow through the
//! event kernel, this crate holds *stateful* domain objects: instruments,
//! positions, portfolios, and completed trades. These have identity, can be
//! mutated as fills arrive, and typically persist across a session.
//!
//! # Example
//!
//! ```
//! use honba_entities::{
//!     Currency, Instrument, InstrumentKind, Money, PositionSide, Position,
//! };
//! use honba_messages::{InstrumentId, Exchange};
//!
//! let id = InstrumentId::new("NIFTY50", Exchange::new("NSE"));
//! let instrument = Instrument::new(
//!     id.clone(),
//!     InstrumentKind::Index,
//!     Currency::Inr,
//!     1.0,       // lot_size
//!     0.05,      // tick_size
//! );
//! assert_eq!(instrument.id(), &id);
//! assert_eq!(instrument.lot_size(), 1.0);
//!
//! let mut position = Position::flat(id.clone(), Currency::Inr);
//! position.apply_fill(PositionSide::Long, 75.0, 22_000.0);
//! assert_eq!(position.quantity(), 75.0);
//! ```

pub mod error;
pub mod instrument;
pub mod portfolio;
pub mod position;
pub mod screener;
pub mod trade;

pub use error::{EntitiesError, Result};
pub use instrument::{Currency, Instrument, InstrumentKind, Money};
pub use portfolio::{Account, Portfolio};
pub use position::{Position, PositionSide};
pub use screener::{
    check_predicate_value, FilterOp, MetricDefinition, MetricKeySpec, MetricPeriod, MetricRef,
    MetricValue, ScreenerFilterGroup, ScreenerFilterPredicate, ScreenerRow, ScreenerScanRequest,
    ScreenerScanResponse, SortSpec, Timeframe, UnitType, ValueType,
};
pub use trade::Trade;

#[cfg(test)]
mod tests;
