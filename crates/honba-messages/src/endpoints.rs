//! The endpoint registry (plan.md E11-S1).
//!
//! One table describes every endpoint: its method, path, and the request and
//! response types inside the envelope. Two consumers read it, which is the point:
//!
//! - `honba-codegen` walks it to emit OpenAPI `paths`, so the served spec can
//!   never describe a route the router does not have.
//! - The REST layer registers routes from it, so adding an endpoint is one row
//!   rather than a router edit plus a spec edit.
//!
//! Every response is `ResponseEnvelope<T>`, so the registry records only the
//! payload type `T`.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

/// The HTTP methods the API exposes.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "UPPERCASE")]
#[non_exhaustive]
pub enum HttpMethod {
    /// Read.
    Get,
    /// Create.
    Post,
    /// Delete.
    Delete,
}

impl HttpMethod {
    /// Returns the uppercase wire spelling.
    pub const fn as_str(&self) -> &'static str {
        match self {
            Self::Get => "GET",
            Self::Post => "POST",
            Self::Delete => "DELETE",
        }
    }
}

impl std::fmt::Display for HttpMethod {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Whether an endpoint is read-only or mutates state.
///
/// Write endpoints are the ones an approval queue and the risk stage must gate
/// (plan.md E11-S7), so the distinction has to be in the registry rather than
/// inferred from the HTTP method.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Access {
    /// Safe to call without approval.
    ReadOnly,
    /// Mutates state; requires the risk stage and approval queue.
    Write,
}

/// Which side of the transport a request body comes from.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ParamLocation {
    /// Path segment, e.g. `/instruments/{id}`.
    Path,
    /// Query string.
    Query,
    /// JSON request body.
    Body,
}

/// One declared endpoint.
///
/// Generic over the request and response payload types so the compiler checks
/// that a handler's types match its row.
#[derive(Debug)]
pub struct Endpoint<Req, Resp> {
    /// HTTP method.
    pub method: HttpMethod,
    /// Path relative to the version prefix, e.g. `/instruments/{id}`.
    pub path: &'static str,
    /// Whether this endpoint mutates state.
    pub access: Access,
    /// Where the request body or parameters are read from.
    pub location: ParamLocation,
    /// Whether a request body is required.
    pub body_required: bool,
    /// Human-readable summary used in the OpenAPI description.
    pub summary: &'static str,
    _req: std::marker::PhantomData<Req>,
    _resp: std::marker::PhantomData<Resp>,
}

impl<Req, Resp> Endpoint<Req, Resp> {
    /// Declares an endpoint row.
    pub const fn new(
        method: HttpMethod,
        path: &'static str,
        access: Access,
        location: ParamLocation,
        body_required: bool,
        summary: &'static str,
    ) -> Self {
        Self {
            method,
            path,
            access,
            location,
            body_required,
            summary,
            _req: std::marker::PhantomData,
            _resp: std::marker::PhantomData,
        }
    }

    /// Returns `METHOD /path` — the key this endpoint is addressed by.
    pub fn key(&self) -> String {
        format!("{} {}", self.method, self.path)
    }

    /// Returns the `{name}` placeholders in this path, in order.
    pub fn path_params(&self) -> Vec<&'static str> {
        let mut out = Vec::new();
        let mut rest = self.path;
        while let Some(start) = rest.find('{') {
            let after = &rest[start + 1..];
            match after.find('}') {
                Some(end) => {
                    out.push(&after[..end]);
                    rest = &after[end + 1..];
                }
                None => break,
            }
        }
        out
    }
}

/// The payload type of an endpoint, recovered for schema generation.
///
/// The registry stores heterogeneous rows behind `dyn`, so codegen needs a way
/// to reach each row's response schema at runtime.
pub trait HasSchema {
    /// Returns the JSON Schema for this payload type.
    fn schema() -> serde_json::Value;
}

/// One endpoint with its type identity erased.
pub type DynEndpoint = dyn HasSchema;

/// Every endpoint on the v1 surface, in declaration order.
pub const ENDPOINTS: &[(&str, &str)] = &[
    ("GET", "/capabilities"),
    ("GET", "/health"),
    ("GET", "/schema"),
    ("GET", "/instruments"),
    ("GET", "/instruments/{id}"),
    ("GET", "/quotes"),
    ("GET", "/bars/{id}"),
    ("GET", "/depth/{id}"),
    ("POST", "/strategies"),
    ("GET", "/strategies"),
    ("POST", "/strategies/verify"),
    ("POST", "/backtests"),
    ("GET", "/backtests/{id}"),
    ("GET", "/backtests/{id}/journal"),
    ("POST", "/sweeps"),
    ("GET", "/sweeps/{id}"),
    ("POST", "/orders"),
    ("GET", "/orders"),
    ("DELETE", "/orders/{id}"),
    ("POST", "/positions/close"),
    ("GET", "/screener/scan"),
    ("GET", "/journals/{id}"),
];

/// Returns every write endpoint in the registry.
///
/// The approval queue and risk stage are gated off this list, so it must stay
/// the single definition of "this endpoint can move money".
pub fn write_endpoints() -> Vec<String> {
    WRITE_PATHS
        .iter()
        .map(|(m, p)| format!("{m} {p}"))
        .collect()
}

/// Endpoints that mutate state and therefore require gating (plan.md §4.3).
///
/// `POST /strategies` and `POST /strategies/verify` compile rather than trade,
/// so they are not gated; the order and position routes are.
pub const WRITE_PATHS: &[(&str, &str)] = &[
    ("POST", "/orders"),
    ("DELETE", "/orders/{id}"),
    ("POST", "/positions/close"),
];
