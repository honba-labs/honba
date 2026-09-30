#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Strategy trait and reference strategies for Honba.
//!
//! A [`Strategy`] reacts to typed events and emits [`OrderIntent`]s. It does
//! not execute — that's the execution layer's job. [`StrategyRunner`] pairs a
//! strategy with an [`ExecutionEngine`](honba_engine::ExecutionEngine) and
//! closes the loop inside the event kernel.

pub mod buy_and_hold;
pub mod intent;
pub mod rsi_reversal;
pub mod runner;
pub mod sma_crossover;
pub mod strategy;

pub use buy_and_hold::BuyAndHold;
pub use intent::OrderIntent;
pub use rsi_reversal::RsiReversal;
pub use runner::StrategyRunner;
pub use sma_crossover::SmaCrossover;
pub use strategy::{Strategy, StrategyAdapter};
