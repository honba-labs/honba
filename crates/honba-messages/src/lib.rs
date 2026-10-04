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
//! - **Timestamped.** Every event carries `ts_event` (when the exchange observed
//!   it) and `ts_init` (when Honba created the value), both as [`UnixNanos`].
//!
//! # Example
//!
//! ```
//! use honba_messages::{
//!     Bar, BarAggregation, BarSpecification, BarType, InstrumentId, PriceType,
//!     UnixNanos, Exchange,
//! };
//!
//! let instrument = InstrumentId::new("NIFTY50", Exchange::new("NSE"));
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

/// Defines a fieldless enum together with an `ALL` constant listing every
/// variant in declaration order.
///
/// Because the list is generated from the definition it cannot fall out of
/// date; the Python bindings use it to check that every Rust wire enum
/// variant has a Python counterpart (ADR 006).
#[doc(hidden)]
#[macro_export]
macro_rules! enum_with_all {
    (
        $(#[$meta:meta])*
        $vis:vis enum $name:ident {
            $( $(#[$vmeta:meta])* $variant:ident ),+ $(,)?
        }
    ) => {
        $(#[$meta])*
        $vis enum $name {
            $( $(#[$vmeta])* $variant ),+
        }

        impl $name {
            /// Every variant, in declaration order.
            pub const ALL: &'static [$name] = &[$($name::$variant),+];
        }
    };
}

pub mod events;
pub mod identifiers;
pub mod market_data;
pub mod orders;
pub mod validation;

pub use events::{timestamp::UnixNanos, Event, Message, SCHEMA_VERSION};
pub use identifiers::{Exchange, InstrumentId, OrderId, TradeId};
pub use market_data::{
    bar::{Bar, BarAggregation, BarSpecification, BarType, PriceType},
    tick::{AggressorSide, QuoteTick, Tick, TradeTick},
};
pub use orders::order::{Order, OrderSide, OrderStatus, OrderType, TimeInForce};
pub use validation::InvariantError;

#[cfg(test)]
mod tests;
