#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Core message types for the Honba trading platform.
//!
//! This crate is the foundation for every other Honba crate. It defines the
//! value types that flow through the event kernel: market data ticks and bars,
//! order lifecycle events, and the envelope that carries them.
//!
//! # Design principles
//!
//! - **No logic, only data.** Messages are plain value types. Behaviour lives
//!   in `honba-algo` and downstream crates.
//! - **Immutable by construction.** Every type is `Copy` or cheaply cloneable,
//!   with private fields and `new` / accessor methods.
//! - **Timestamped.** Every event carries `ts_event` (when the venue observed
//!   it) and `ts_init` (when Honba created the value), both as [`UnixNanos`].
//!
//! # Example
//!
//! ```
//! use honba_messages::{
//!     Bar, BarAggregation, BarSpecification, BarType, InstrumentId, PriceType,
//!     UnixNanos, Venue,
//! };
//!
//! let instrument = InstrumentId::new("NIFTY50", Venue::new("NSE"));
//! let spec = BarSpecification::new(1, BarAggregation::Minute, PriceType::Last);
//! let bar_type = BarType::new(instrument, spec);
//!
//! let bar = Bar::new(
//!     bar_type,
//!     22_000.0, 22_050.0, 21_980.0, 22_020.0, 15_000.0,
//!     UnixNanos::from_u64(1_700_000_060_000_000_000),
//!     UnixNanos::from_u64(1_700_000_060_000_000_000),
//! );
//!
//! assert_eq!(bar.close(), 22_020.0);
//! ```

pub mod events;
pub mod identifiers;
pub mod market_data;
pub mod orders;

pub use events::{timestamp::UnixNanos, Event, Message};
pub use identifiers::{InstrumentId, OrderId, TradeId, Venue};
pub use market_data::{
    bar::{Bar, BarAggregation, BarSpecification, BarType, PriceType},
    tick::{AggressorSide, QuoteTick, Tick, TradeTick},
};
pub use orders::order::{Order, OrderSide, OrderStatus, OrderType, TimeInForce};
