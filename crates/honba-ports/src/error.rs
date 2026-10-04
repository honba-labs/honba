//! The error taxonomy shared by every port.

use thiserror::Error;

/// The result type returned by every port method.
pub type PortResult<T> = std::result::Result<T, PortError>;

/// A failure raised at a port boundary.
///
/// Ports are the seam between the synchronous kernel and the asynchronous
/// edges, so this is a domain boundary: failures are enumerated and matched,
/// never string-matched, and never collapsed into `anyhow`.
///
/// [`PortError::is_retryable`] splits the taxonomy into the failures an edge
/// may retry ([`PortError::Unavailable`], [`PortError::Timeout`],
/// [`PortError::Transport`]) and the ones that need a decision from the caller.
#[derive(Debug, Error, PartialEq, Eq)]
#[non_exhaustive]
pub enum PortError {
    /// The port is not connected, not initialised, or temporarily out of service.
    #[error("port unavailable: {0}")]
    Unavailable(String),
    /// The operation did not complete within its deadline.
    #[error("operation timed out")]
    Timeout,
    /// The underlying connection or socket failed.
    #[error("transport error: {0}")]
    Transport(String),
    /// The venue accepted the request and refused it.
    #[error("broker rejected: {code}: {message}")]
    Rejected {
        /// The venue's machine-readable rejection code.
        code: String,
        /// The venue's human-readable rejection reason.
        message: String,
    },
    /// The request was malformed and will be rejected again unchanged.
    #[error("invalid request: {0}")]
    InvalidRequest(String),
    /// The port does not implement this capability.
    #[error("unsupported by this port: {0}")]
    Unsupported(String),
    /// The port broke its own invariants; a bug, not a market outcome.
    #[error("internal port error: {0}")]
    Internal(String),
}

impl PortError {
    /// Returns `true` when retrying the same call may succeed unchanged.
    ///
    /// `Unavailable`, `Timeout` and `Transport` are transient: the edge should
    /// back off and retry. Everything else is a decision for the caller —
    /// fixing the request, falling back to another capability, or giving up.
    pub fn is_retryable(&self) -> bool {
        matches!(
            self,
            PortError::Unavailable(_) | PortError::Timeout | PortError::Transport(_)
        )
    }
}
