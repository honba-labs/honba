#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Event kernel for the Honba trading platform.
//!
//! The kernel is deliberately small: a monotonic [`Clock`], a time-ordered
//! [`EventQueue`], a [`Handler`] trait, and an [`Engine`] that drives them.
//! Data sources and execution exchanges are injected via the [`DataFeed`] and
//! [`ExecutionEngine`] traits, so the same kernel runs backtests, paper
//! trading, and live sessions without code changes.

pub mod clock;
pub mod data;
pub mod engine;
pub mod error;
pub mod execution;
pub mod handler;
pub mod queue;

pub use clock::Clock;
pub use data::DataFeed;
pub use engine::Engine;
pub use error::{AlgoError, Result};
pub use execution::ExecutionEngine;
pub use handler::{EngineOutput, Handler, NoopHandler, TradingState};
pub use queue::EventQueue;

#[cfg(test)]
mod tests;
