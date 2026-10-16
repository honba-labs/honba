//! Errors from the portfolio valuation engine.

use honba_messages::InstrumentId;
use thiserror::Error;

/// Why a portfolio could not be valued.
#[derive(Debug, Error, PartialEq)]
#[non_exhaustive]
pub enum PortfolioError {
    /// A non-flat position had no mark, so it cannot be valued.
    ///
    /// The engine refuses rather than silently valuing at cost: a snapshot that mixed marks
    /// and costs would misstate equity without saying so.
    #[error("no mark for instrument {instrument_id}")]
    MissingMark {
        /// The instrument that lacked a mark.
        instrument_id: InstrumentId,
    },

    /// A monetary operation failed: overflow, a non-finite input, or a currency mismatch
    /// between the account and one of its positions (ADR 0011).
    #[error(transparent)]
    Money(#[from] honba_entities::EntitiesError),
}
