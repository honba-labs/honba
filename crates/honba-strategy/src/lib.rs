#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Strategy trait and reference strategies for Honba.
//!
//! A [`Strategy`] reacts to typed events and acts only through its
//! [`StrategyContext`]: it reads the clock, positions, cash and instrument
//! metadata, and submits [`OrderIntent`]s. It does not execute; that is the
//! execution layer's job. [`StrategyRunner`] pairs a strategy and its
//! [`LedgerContext`] with an
//! [`ExecutionEngine`](honba_engine::ExecutionEngine) and closes the loop
//! inside the event kernel. The contract matches the Python
//! `honba.strategies` package (ADR 008).

pub mod buy_and_hold;
pub mod context;
pub mod contract_probe;
pub mod dyn_strategy;
pub mod intent;
pub mod ir;
pub mod manifest;
pub mod rsi_reversal;
pub mod runner;
pub mod sma_crossover;
pub mod strategy;

pub use buy_and_hold::BuyAndHold;
pub use context::{CacheContext, LedgerContext, StrategyContext};
pub use contract_probe::{ContractProbe, Observation};
pub use dyn_strategy::DynStrategy;
pub use intent::{IntentError, OrderIntent};
pub use ir::{IrError, IrSubscriptions, IrUniverse, StrategyIr};
pub use manifest::{
    ManifestError, StrategyManifest, Subscriptions, TimeframeSpec, Universe, STRATEGY_API_VERSION,
};
pub use rsi_reversal::RsiReversal;
pub use runner::{IntentRejection, StrategyRunner, SubmittedIntent, SuppressedIntent};
pub use sma_crossover::{SmaCrossover, MAX_SMA_PERIOD};
pub use strategy::{Strategy, StrategyAdapter};

#[cfg(test)]
mod tests;
