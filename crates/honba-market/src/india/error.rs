//! Error types for the India crate.

use thiserror::Error;

/// Convenience alias for results produced by this crate.
pub type Result<T> = std::result::Result<T, IndiaError>;

/// Errors that India-specific operations can produce.
#[derive(Debug, Error, PartialEq)]
#[non_exhaustive]
pub enum IndiaError {
    /// A date or timestamp could not be constructed.
    #[error("invalid date: {0}")]
    InvalidDate(String),

    /// An operation referenced a segment that isn't recognised.
    #[error("unknown segment: {0}")]
    UnknownSegment(String),

    /// An operation referenced a symbol that isn't in the universe.
    #[error("symbol not found: {0}")]
    SymbolNotFound(String),
}
