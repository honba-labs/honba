//! Error types for the analytics crate.

use thiserror::Error;

/// Convenience alias for results produced by this crate.
pub type Result<T> = std::result::Result<T, AnalyticsError>;

/// Errors that analytics computations can produce.
#[derive(Debug, Error, PartialEq)]
#[non_exhaustive]
pub enum AnalyticsError {
    /// An empty input was supplied where at least one element is required.
    #[error("empty input")]
    EmptyInput,

    /// Not enough data to compute the requested metric.
    #[error("insufficient data: needed {needed}, got {got}")]
    InsufficientData {
        /// Minimum number of elements required.
        needed: usize,
        /// Number of elements supplied.
        got: usize,
    },

    /// Variance was zero, so a ratio denominator was undefined.
    #[error("zero variance in input")]
    ZeroVariance,

    /// Two trades had incompatible instruments or sides.
    #[error("trade mismatch: {0}")]
    TradeMismatch(String),
}
