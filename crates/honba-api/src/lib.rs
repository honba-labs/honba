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
pub mod market;
pub mod requests;
pub mod responses;
pub mod screener;
pub mod strategies;
pub mod verify;

pub use capabilities::{Capabilities, CapabilityManifest};
pub use honba_messages::{
    write_endpoints, Access, ApiResponse, ApiVersion, Endpoint, ErrorCategory, ErrorCode,
    ErrorDetail, HttpMethod, ParamLocation, ResponseEnvelope, API_VERSION, ENDPOINTS, WRITE_PATHS,
};
pub use market::{
    instrument_json, parse_instrument_id, parse_timeframe, ResolvedBarsQuery, ResolvedQuotesQuery,
    DEFAULT_DEPTH_LEVELS, DEFAULT_TIMEFRAME, MAX_DEPTH_LEVELS,
};
pub use requests::{
    BacktestRequest, BarsQuery, DepthQuery, InstrumentsQuery, OrdersRequest, QuotesQuery,
    ScreenerQuery, StrategiesRequest, SweepRequest,
};
pub use responses::{
    BacktestMetrics, BacktestResponse, BacktestStatus, BarsResponse, CapabilitiesResponse,
    CompiledStrategy, DepthLevel, DepthResponse, InstrumentsResponse, OrdersResponse,
    PositionsResponse, QuotesResponse, RunStatus, ScreenerResponse, ScreenerResultRow,
    StrategiesResponse, SweepReportResponse, SweepResponse, SweepStatus, TradesResponse,
};
pub use screener::{
    check_scan_budget, check_screener_rows, ResolvedScreenerQuery, DEFAULT_SCREENER_TIMEFRAME,
    MAX_SCREENER_BARS, MAX_SCREENER_ROWS, MAX_SCREENER_UNIVERSE,
};
pub use strategies::{
    compile_strategy, list_strategies, parse_compile_request, StrategyCatalog,
    MAX_COMPILED_STRATEGIES,
};
pub use verify::{verify_strategy, VerifyStrategyRequest, VerifyStrategyResponse};

#[cfg(test)]
mod tests;
