//! Error types for the event kernel.

use thiserror::Error;

/// Convenience alias for results produced by this crate.
pub type Result<T> = std::result::Result<T, AlgoError>;

/// Errors that kernel operations can produce.
#[derive(Debug, Error, PartialEq)]
#[non_exhaustive]
pub enum AlgoError {
    /// The clock was asked to move backwards.
    #[error("clock cannot go backwards: current={current}, requested={requested}")]
    ClockRegression {
        /// Current clock value.
        current: u64,
        /// Requested (earlier) value.
        requested: u64,
    },

    /// A data feed reported exhaustion mid-run.
    #[error("data feed exhausted")]
    DataFeedExhausted,

    /// A component received an event it doesn't handle.
    #[error("unhandled event: {0}")]
    UnhandledEvent(String),

    /// A component failed with a message.
    #[error("component error: {0}")]
    Component(String),
}
