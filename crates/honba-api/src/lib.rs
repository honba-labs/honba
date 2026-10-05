//! Transport-agnostic versioned API surface for the Honba platform.
//!
//! The envelope, error taxonomy, endpoint registry, and version constants live in
//! `honba-messages` (L0) because they are wire types that every surface —
//! Python, REST, WASM, MCP — reads and writes. This crate holds the request
//! and response DTOs that sit *inside* that envelope, plus the endpoint
//! registry the code generator walks to emit OpenAPI `paths`.
//!
//! It stays transport-free on purpose: no axum, no wasm-bindgen, no I/O. The
//! REST and WASM crates depend on this one, not on each other.

#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

pub mod capabilities;
pub mod requests;
pub mod responses;

pub use capabilities::{Capabilities, CapabilityManifest};
pub use honba_messages::{
    Access, ApiResponse, ApiVersion, Endpoint, ErrorCategory, ErrorCode, ErrorDetail, HttpMethod,
    ParamLocation, ResponseEnvelope, WRITE_PATHS, ENDPOINTS, write_endpoints, API_VERSION,
};
pub use requests::{
    BacktestRequest, BarsQuery, DepthQuery, InstrumentsQuery, OrdersRequest, QuotesQuery,
    StrategiesRequest, SweepRequest,
};
pub use responses::{
    BacktestMetrics, BacktestResponse, BacktestStatus, BarsResponse, CapabilitiesResponse,
    DepthLevel, DepthResponse, InstrumentsResponse, OrdersResponse, PositionsResponse,
    QuotesResponse, RunStatus, StrategiesResponse, SweepReportResponse, SweepResponse, SweepStatus,
    TradesResponse,
};

#[cfg(test)]
mod tests;
