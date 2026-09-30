#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Strategy trait and reference strategies for Honba.
//!
//! A [`Strategy`] reacts to typed events and emits [`OrderIntent`]s. It does
//! not execute — that's the execution layer's job. The [`StrategyAdapter`]
//! bridges a strategy into the event kernel's [`Handler`](honba_algo::Handler)
//! interface.
//!
//! # Example
//!
//! ```
//! use honba_algo::Engine;
//! use honba_algo_strategies::{SmaCrossover, StrategyAdapter};
//! use honba_messages::{InstrumentId, Venue};
//!
//! let strategy = SmaCrossover::new(
//!     InstrumentId::new("NIFTY50", Venue::new("NSE")),
//!     5, 20, 75.0,
//! );
//! let mut engine = Engine::new();
//! engine.add_handler(StrategyAdapter::new(strategy));
//! ```

pub mod buy_and_hold;
pub mod intent;
pub mod rsi_reversal;
pub mod sma_crossover;
pub mod strategy;

pub use buy_and_hold::BuyAndHold;
pub use intent::OrderIntent;
pub use rsi_reversal::RsiReversal;
pub use sma_crossover::SmaCrossover;
pub use strategy::{Strategy, StrategyAdapter};
