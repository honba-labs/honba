//! The versioned response envelope (plan.md §4.2).
//!
//! Every response is enveloped, success and failure alike, so a client parses
//! one shape regardless of outcome:
//!
//! ```json
//! { "api_version": "1.0.0", "schema_version": 4, "data": { }, "error": null }
//! ```
//!
//! The envelope lives in `honba-messages` alongside [`ErrorDetail`] because it
//! is itself a wire type: it is what the REST server writes, what WASM returns,
//! and what the Python client parses, all with the same bytes.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::errors::ErrorDetail;

/// Semantic version of the HTTP/API surface (`/api/v1`, OpenAPI `info.version`).
///
/// Owned here because the envelope is what carries it, and every surface reads
/// it from this one constant rather than repeating the literal.
pub const API_VERSION: &str = "1.0.0";

/// The API version of a response, as a string.
///
/// A distinct type rather than a bare `String` so a semver string cannot be
/// confused with any other string field in the envelope.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[schemars(transparent)]
pub struct ApiVersion(String);

impl ApiVersion {
    /// Wraps an explicit version string.
    pub fn new(version: impl Into<String>) -> Self {
        Self(version.into())
    }

    /// Borrows the version string.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl Default for ApiVersion {
    fn default() -> Self {
        Self::new(API_VERSION)
    }
}

impl From<&str> for ApiVersion {
    fn from(value: &str) -> Self {
        Self::new(value)
    }
}

/// A response body, always carrying both version axes and at most one of
/// `data` / `error`.
///
/// Unknown fields are accepted on read so a v1 reader can consume a response
/// from a newer writer that added an optional field; unknown *versions* are
/// rejected by the caller, which compares `api_version` and `schema_version`
/// against what it was built for.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(bound(deserialize = "T: Deserialize<'de>"))]
pub struct ResponseEnvelope<T> {
    /// Semantic version of the API surface.
    pub api_version: ApiVersion,
    /// Integer version of the wire shape (see [`SCHEMA_VERSION`](crate::SCHEMA_VERSION)).
    pub schema_version: u32,
    /// Payload on success; absent on failure.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub data: Option<T>,
    /// Error detail on failure; absent on success.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<ErrorDetail>,
}

/// Builds an envelope in one of its two valid states.
///
/// A success carrying an error, or a failure carrying data, is a bug the type
/// system should prevent; this trait keeps construction in one place.
pub trait ApiResponse<T> {
    /// Wraps a successful payload.
    fn success(data: T) -> Self;
    /// Wraps a failure.
    fn error(error: ErrorDetail) -> Self;
}

impl<T> ApiResponse<T> for ResponseEnvelope<T> {
    fn success(data: T) -> Self {
        Self {
            api_version: ApiVersion::default(),
            schema_version: crate::SCHEMA_VERSION,
            data: Some(data),
            error: None,
        }
    }

    fn error(error: ErrorDetail) -> Self {
        Self {
            api_version: ApiVersion::default(),
            schema_version: crate::SCHEMA_VERSION,
            data: None,
            error: Some(error),
        }
    }
}

impl<T> ResponseEnvelope<T> {
    /// Unwraps the payload, or returns the error that replaced it.
    pub fn into_result(self) -> Result<T, ErrorDetail> {
        match (self.data, self.error) {
            (Some(data), None) => Ok(data),
            (None, Some(err)) => Err(err),
            // Unreachable through the constructors; handled rather than
            // panicked so a future deserializer change degrades loudly.
            (Some(_), Some(_)) => Err(ErrorDetail::new(
                crate::ErrorCode::InternalError,
                "envelope carried both data and error",
            )),
            (None, None) => Err(ErrorDetail::new(
                crate::ErrorCode::InternalError,
                "envelope carried neither data nor error",
            )),
        }
    }

    /// Whether this response reports a failure.
    pub fn is_error(&self) -> bool {
        self.error.is_some()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn success_carries_data_and_no_error() {
        let env: ResponseEnvelope<serde_json::Value> = ApiResponse::success(json!({"ok": true}));
        assert_eq!(env.api_version.as_str(), API_VERSION);
        assert_eq!(env.schema_version, crate::SCHEMA_VERSION);
        assert!(env.error.is_none());
        assert!(!env.is_error());
        assert_eq!(env.data.unwrap()["ok"], json!(true));
    }

    #[test]
    fn a_failing_envelope_round_trips_its_stable_code() {
        let env: ResponseEnvelope<serde_json::Value> =
            ApiResponse::error(ErrorDetail::new(crate::ErrorCode::NotFound, "gone"));
        let text = serde_json::to_string(&env).unwrap();
        let back: ResponseEnvelope<serde_json::Value> = serde_json::from_str(&text).unwrap();
        assert_eq!(
            back.error
                .expect("error detail survives the round trip")
                .code,
            crate::ErrorCode::NotFound
        );
    }

    #[test]
    fn into_result_unwraps_a_success() {
        let env: ResponseEnvelope<u8> = ApiResponse::success(7);
        assert_eq!(env.into_result().unwrap(), 7);
    }

    #[test]
    fn error_serializes_with_a_null_data_key_because_it_is_absent() {
        let env: ResponseEnvelope<serde_json::Value> =
            ApiResponse::error(ErrorDetail::new(crate::ErrorCode::Timeout, "slow"));
        let v = serde_json::to_value(&env).unwrap();
        assert!(v.get("data").is_none());
        assert_eq!(v["error"]["code"], json!("timeout"));
        assert_eq!(v["error"]["retryable"], json!(true));
    }

    #[test]
    fn an_unknown_optional_field_from_a_newer_writer_is_ignored() {
        // plan.md 4.2: readers must ignore unknown fields so a v1 reader can
        // consume v1-with-extras safely.
        let raw = json!({
            "api_version": API_VERSION,
            "schema_version": crate::SCHEMA_VERSION,
            "data": {"ok": true},
            "some_future_field": {"added": "later"},
        });
        let env: ResponseEnvelope<serde_json::Value> =
            serde_json::from_value(raw).expect("unknown field must be ignored");
        assert_eq!(env.data.unwrap()["ok"], json!(true));
    }
}
