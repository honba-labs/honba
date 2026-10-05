//! Error types for the entities crate.

use thiserror::Error;

/// Convenience alias for results produced by this crate.
pub type Result<T> = std::result::Result<T, EntitiesError>;

/// Errors that entity operations can produce.
#[derive(Debug, Error, PartialEq)]
#[non_exhaustive]
pub enum EntitiesError {
    /// An operation referenced a position that does not exist.
    #[error("position not found for instrument {0}")]
    PositionNotFound(String),

    /// An operation referenced an account that does not exist.
    #[error("account not found: {0}")]
    AccountNotFound(String),

    /// Arithmetic overflowed or produced an invalid value.
    #[error("arithmetic error: {0}")]
    Arithmetic(String),

    /// Two operands had incompatible currencies.
    #[error("currency mismatch: {left} vs {right}")]
    CurrencyMismatch {
        /// The left operand's currency.
        left: String,
        /// The right operand's currency.
        right: String,
    },

    /// A screener predicate's value does not fit its operator.
    #[error("invalid screener predicate: {0}")]
    InvalidPredicate(String),

    /// A money value or operation was invalid (non-finite, overflow).
    #[error("invalid money: {0}")]
    InvalidMoney(String),
}
