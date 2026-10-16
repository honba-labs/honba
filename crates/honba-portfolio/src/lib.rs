#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Pure portfolio valuation (ADR 0032, ROADMAP E13).
//!
//! Given what a user holds ([`PortfolioState`]) and the last price of each instrument
//! ([`Marks`]), [`value`] produces a [`honba_entities::PortfolioSnapshot`]: accounts with
//! mark-to-market positions and delivery holdings, cash, equity, and realized/unrealized PnL.
//!
//! There is no clock and no I/O — the caller stamps `as_of` — so the same positions and marks
//! always give a byte-identical snapshot, and the server, a backtest and the browser agree. The
//! engine never places an order: a portfolio is research and presentation, fed by fills and
//! marks (ADR 0032 §7).
//!
//! ```
//! use honba_entities::{Currency, Money, Position, PositionSide};
//! use honba_messages::{Exchange, InstrumentId, Product, UnixNanos};
//! use honba_portfolio::{value, AccountState, Marks, PortfolioState};
//!
//! let id = InstrumentId::new("ACME", Exchange::new("NSE"));
//! let mut position = Position::flat(id.clone(), Currency::Inr);
//! position.apply_fill(PositionSide::Long, 10.0, 100.0);
//!
//! let state = PortfolioState::new(Currency::Inr).with_account(
//!     AccountState::new("MAIN", "Main", Currency::Inr, Money::new(0, Currency::Inr))
//!         .with_position(position, Product::Intraday),
//! );
//! let marks = Marks::new().with(id, 110.0);
//!
//! let snapshot = value(&state, &marks, "default", 0, UnixNanos::from_u64(1)).unwrap();
//! assert_eq!(snapshot.totals.market_value.minor(), 110_000); // 10 * 110 INR, in minor units
//! assert_eq!(snapshot.totals.equity.minor(), 110_000);      // cash 0 + market value
//! assert_eq!(snapshot.totals.unrealized_pnl.minor(), 10_000); // 10 * (110 - 100) INR
//! ```

mod engine;
mod error;
mod marks;
mod state;

pub use engine::value;
pub use error::PortfolioError;
pub use marks::Marks;
pub use state::{AccountState, HeldPosition, PortfolioState};

#[cfg(test)]
mod tests;
