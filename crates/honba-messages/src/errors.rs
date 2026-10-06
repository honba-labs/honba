//! The stable error taxonomy (plan.md E0-S5, §4.2).
//!
//! No error crosses a surface — Python, REST, WASM, MCP — without one of these
//! codes. The taxonomy lives in `honba-messages` (L0) rather than in the API
//! crate because the envelope that carries an error is itself a wire type, and
//! because the code generator must be able to reach the codes without depending
//! on a higher layer.
//!
//! Two properties make the taxonomy usable across languages:
//!
//! - [`ErrorCode::ALL`] is generated from the definition, so a new variant
//!   cannot be added without it appearing in the published list.
//! - Each code declares its [`ErrorCategory`] and its retryability, so callers
//!   branch on a closed set instead of parsing message text.

use std::fmt;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// The broad area a failure came from.
///
/// Clients switch on this to decide *where* to react; the finer-grained
/// [`ErrorCode`] says *what* happened. Splitting the two keeps client logic
/// from depending on the full code list.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum ErrorCategory {
    /// The request was malformed or failed validation.
    Validation,
    /// A resource does not exist.
    NotFound,
    /// Authentication or authorization failed.
    Auth,
    /// The caller exceeded a rate limit.
    RateLimit,
    /// A pre-trade risk rule refused the operation.
    Risk,
    /// The order lifecycle itself failed (rejection, unknown order).
    Order,
    /// Market data is missing, stale, or unavailable.
    MarketData,
    /// A timeout or transport-level failure; safe to retry.
    Transport,
    /// The server failed for a reason it did not anticipate.
    Internal,
    /// The operation is not supported on this surface or build.
    Unsupported,
}

impl ErrorCategory {
    /// Returns the stable wire string for this category.
    pub const fn as_str(&self) -> &'static str {
        match self {
            Self::Validation => "validation",
            Self::NotFound => "not_found",
            Self::Auth => "auth",
            Self::RateLimit => "rate_limit",
            Self::Risk => "risk",
            Self::Order => "order",
            Self::MarketData => "market_data",
            Self::Transport => "transport",
            Self::Internal => "internal",
            Self::Unsupported => "unsupported",
        }
    }
}

impl fmt::Display for ErrorCategory {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

crate::enum_with_all! {
    /// One code per failure mode, stable across releases (plan.md §4.2).
    ///
    /// Deserialization is deliberately strict: an unrecognized code is an
    /// error rather than a default, because silently mapping a newer server's
    /// code onto a known one would report the wrong thing happened.
    ///
    /// ```
    /// use honba_messages::ErrorCode;
    ///
    /// let code = ErrorCode::RiskMaxNotionalExceeded;
    /// assert_eq!(code.category().as_str(), "risk");
    /// assert!(!code.is_retryable(), "a risk refusal is not worth repeating");
    /// assert_eq!(
    ///     serde_json::to_string(&code).unwrap(),
    ///     "\"risk_max_notional_exceeded\""
    /// );
    /// ```
    #[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
    #[serde(rename_all = "snake_case")]
    pub enum ErrorCode {
        /// A request was malformed or failed validation.
        ValidationInvalidRequest,
        /// A named resource does not exist.
        NotFound,
        /// The caller is not authenticated.
        Unauthorized,
        /// The caller is authenticated but not permitted to do this.
        Forbidden,
        /// The caller exceeded a rate limit.
        RateLimited,
        /// Order notional exceeded the configured maximum.
        RiskMaxNotionalExceeded,
        /// Position size exceeded the configured maximum.
        RiskMaxPositionExceeded,
        /// Account drawdown exceeded the configured maximum.
        RiskMaxDrawdownExceeded,
        /// The exchange or gateway rejected the order.
        OrderRejected,
        /// The referenced order does not exist.
        OrderNotFound,
        /// The referenced instrument does not exist.
        InstrumentNotFound,
        /// Market data is unavailable for the request.
        MarketDataUnavailable,
        /// The operation timed out.
        Timeout,
        /// A transport or network failure.
        TransportError,
        /// An unanticipated server-side failure.
        InternalError,
        /// The operation is not supported on this surface or build.
        Unsupported,
    }
}

impl ErrorCode {
    /// Returns the category this code belongs to.
    ///
    /// The mapping is fixed here rather than supplied by the caller so that a
    /// code and its category cannot drift apart across surfaces.
    pub const fn category(&self) -> ErrorCategory {
        match self {
            Self::ValidationInvalidRequest => ErrorCategory::Validation,
            Self::NotFound | Self::OrderNotFound | Self::InstrumentNotFound => {
                ErrorCategory::NotFound
            }
            Self::Unauthorized | Self::Forbidden => ErrorCategory::Auth,
            Self::RateLimited => ErrorCategory::RateLimit,
            Self::RiskMaxNotionalExceeded
            | Self::RiskMaxPositionExceeded
            | Self::RiskMaxDrawdownExceeded => ErrorCategory::Risk,
            Self::OrderRejected => ErrorCategory::Order,
            Self::MarketDataUnavailable => ErrorCategory::MarketData,
            Self::Timeout | Self::TransportError => ErrorCategory::Transport,
            Self::InternalError => ErrorCategory::Internal,
            Self::Unsupported => ErrorCategory::Unsupported,
        }
    }

    /// Whether repeating the identical operation is safe and might succeed.
    ///
    /// Only transport-level codes are retryable. A validation, risk, or order
    /// refusal will fail identically every time, so marking one retryable
    /// would invite a caller to spin.
    pub const fn is_retryable(&self) -> bool {
        matches!(
            self,
            Self::Timeout | Self::TransportError | Self::RateLimited
        )
    }

    /// Returns the stable wire string for this code.
    pub fn as_str(&self) -> &'static str {
        // Serializing is the single source of truth for the spelling; this
        // exists so callers can read the string without allocating a Value.
        match self {
            Self::ValidationInvalidRequest => "validation_invalid_request",
            Self::NotFound => "not_found",
            Self::Unauthorized => "unauthorized",
            Self::Forbidden => "forbidden",
            Self::RateLimited => "rate_limited",
            Self::RiskMaxNotionalExceeded => "risk_max_notional_exceeded",
            Self::RiskMaxPositionExceeded => "risk_max_position_exceeded",
            Self::RiskMaxDrawdownExceeded => "risk_max_drawdown_exceeded",
            Self::OrderRejected => "order_rejected",
            Self::OrderNotFound => "order_not_found",
            Self::InstrumentNotFound => "instrument_not_found",
            Self::MarketDataUnavailable => "market_data_unavailable",
            Self::Timeout => "timeout",
            Self::TransportError => "transport_error",
            Self::InternalError => "internal_error",
            Self::Unsupported => "unsupported",
        }
    }
}

impl fmt::Display for ErrorCode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// A single error as it crosses a surface.
///
/// Carries the stable [`ErrorCode`] plus human-readable detail. `context` holds
/// structured specifics (the limit that was exceeded, the id that was not
/// found) so a client can react programmatically without parsing `message`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct ErrorDetail {
    /// The stable machine-readable code.
    pub code: ErrorCode,
    /// Human-readable description. Never parsed by clients.
    pub message: String,
    /// Whether repeating the identical operation is safe.
    pub retryable: bool,
    /// Structured detail specific to this failure.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub context: Option<serde_json::Value>,
}

impl ErrorDetail {
    /// Builds a detail, deriving `retryable` from the code.
    ///
    /// ```
    /// use honba_messages::{ErrorCode, ErrorDetail};
    ///
    /// let d = ErrorDetail::new(ErrorCode::Timeout, "upstream did not answer");
    /// assert!(d.retryable);
    /// ```
    pub fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            retryable: code.is_retryable(),
            context: None,
        }
    }

    /// Attaches structured context, consuming and returning `self`.
    #[must_use]
    pub fn with_context(mut self, context: serde_json::Value) -> Self {
        self.context = Some(context);
        self
    }
}

impl fmt::Display for ErrorDetail {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}
