//! The one list of types the wire contract publishes.
//!
//! Every artifact — JSON Schema, OpenAPI, TypeScript, `.pyi`, MCP tool schemas
//! — is rendered from this list. Adding a type here is the only step needed to
//! expose it everywhere; that is the whole point of inverting the direction
//! from Python-generated to Rust-generated (plan.md §4.1).

use schemars::JsonSchema;
use serde_json::{json, Value};

use crate::schemas::SchemaSet;

/// The data-carrying types that appear on the wire.
pub const WIRE_TYPES: &[&str] = &[
    "InstrumentId",
    "Bar",
    "Order",
    "OrderIntent",
    "Trade",
    "Position",
    "Event",
    "Message",
    "ScreenerFilterPredicate",
];

/// The screener contract: metric keys and references, predicates, scan
/// request and response.
pub const SCREENER_TYPES: &[&str] = &[
    "MetricKeySpec",
    "MetricRef",
    "MetricDefinition",
    "ScreenerFilterGroup",
    "SortSpec",
    "ScreenerScanRequest",
    "ScreenerRow",
    "ScreenerScanResponse",
];

/// The enums whose variants are published as stable string constants.
pub const WIRE_ENUMS: &[&str] = &[
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "TimeInForce",
    "BarAggregation",
    "PriceType",
    "AggressorSide",
    "PositionSide",
    "Currency",
];

/// The API surface types: envelope, error taxonomy, capabilities.
pub const API_TYPES: &[&str] = &[
    "ApiVersion",
    "ResponseEnvelope",
    "ErrorDetail",
    "ErrorCode",
    "ErrorCategory",
    "Capabilities",
    "CapabilityManifest",
    "HttpMethod",
    "Access",
    "ParamLocation",
];

/// The run-configuration types (plan.md E0-S4).
pub const CONFIG_TYPES: &[&str] = &["BacktestRunConfig"];

/// The strategy manifest types (plan.md E0-S8).
pub const MANIFEST_TYPES: &[&str] = &[
    "StrategyManifest",
    "Universe",
    "Subscriptions",
    "TimeframeSpec",
];

/// Every type name the contract publishes, in a stable order.
pub fn published_names() -> Vec<&'static str> {
    let mut names: Vec<&'static str> = Vec::new();
    names.extend_from_slice(WIRE_TYPES);
    names.extend_from_slice(SCREENER_TYPES);
    names.extend_from_slice(WIRE_ENUMS);
    names.extend_from_slice(API_TYPES);
    names.extend_from_slice(MANIFEST_TYPES);
    names.extend_from_slice(CONFIG_TYPES);
    names
}

/// Adds every published type to `set`.
///
/// Kept in one function so a new type cannot be added to the JSON Schema
/// artifact but forgotten by the TypeScript one: all five artifacts read the
/// same set.
pub fn register_all(set: &mut SchemaSet) {
    set.add_type::<honba_messages::InstrumentId>("InstrumentId");
    set.add_type::<honba_messages::Bar>("Bar");
    set.add_type::<honba_messages::Order>("Order");
    set.add_type::<honba_strategy::OrderIntent>("OrderIntent");
    set.add_type::<honba_entities::Trade>("Trade");
    set.add_type::<honba_entities::Position>("Position");
    set.add_type::<honba_messages::Event>("Event");
    set.add_type::<honba_messages::Message>("Message");
    set.add_type::<honba_entities::ScreenerFilterPredicate>("ScreenerFilterPredicate");

    set.add_type::<honba_entities::MetricKeySpec>("MetricKeySpec");
    set.add_type::<honba_entities::MetricRef>("MetricRef");
    set.add_type::<honba_entities::MetricDefinition>("MetricDefinition");
    set.add_type::<honba_entities::ScreenerFilterGroup>("ScreenerFilterGroup");
    set.add_type::<honba_entities::SortSpec>("SortSpec");
    set.add_type::<honba_entities::ScreenerScanRequest>("ScreenerScanRequest");
    set.add_type::<honba_entities::ScreenerRow>("ScreenerRow");
    set.add_type::<honba_entities::ScreenerScanResponse>("ScreenerScanResponse");

    set.add_type::<honba_messages::OrderSide>("OrderSide");
    set.add_type::<honba_messages::OrderType>("OrderType");
    set.add_type::<honba_messages::OrderStatus>("OrderStatus");
    set.add_type::<honba_messages::TimeInForce>("TimeInForce");
    set.add_type::<honba_messages::BarAggregation>("BarAggregation");
    set.add_type::<honba_messages::PriceType>("PriceType");
    set.add_type::<honba_messages::AggressorSide>("AggressorSide");
    set.add_type::<honba_entities::PositionSide>("PositionSide");
    set.add_type::<honba_entities::Currency>("Currency");

    set.add_type::<honba_messages::ApiVersion>("ApiVersion");
    set.add_type::<honba_messages::ResponseEnvelope<()>>("ResponseEnvelope");
    set.add_type::<honba_messages::ErrorDetail>("ErrorDetail");
    set.add_type::<honba_messages::ErrorCode>("ErrorCode");
    set.add_type::<honba_messages::ErrorCategory>("ErrorCategory");

    set.add_type::<honba_api::Capabilities>("Capabilities");
    set.add_type::<honba_api::CapabilityManifest>("CapabilityManifest");

    // Endpoint registry types from honba-messages (L0)
    set.add_type::<honba_messages::HttpMethod>("HttpMethod");
    set.add_type::<honba_messages::Access>("Access");
    set.add_type::<honba_messages::ParamLocation>("ParamLocation");

    set.add_type::<honba_strategy::StrategyManifest>("StrategyManifest");
    set.add_type::<honba_strategy::Universe>("Universe");
    set.add_type::<honba_strategy::Subscriptions>("Subscriptions");
    set.add_type::<honba_strategy::TimeframeSpec>("TimeframeSpec");

    set.add_type::<honba_config::BacktestRunConfig>("BacktestRunConfig");
}

/// The schema for a request body, registered under its DTO name.
///
/// `honba-api` request DTOs live in a crate codegen may not depend on at build
/// time, so the map is declared here and kept in step by a test.
pub fn request_type_names() -> &'static [&'static str] {
    &[
        "InstrumentsQuery",
        "QuotesQuery",
        "BarsQuery",
        "DepthQuery",
        "StrategiesRequest",
        "BacktestRequest",
        "SweepRequest",
        "OrdersRequest",
    ]
}

/// Adds the request DTOs to `set`.
pub fn register_requests(set: &mut SchemaSet) {
    set.add_type::<honba_api::InstrumentsQuery>("InstrumentsQuery");
    set.add_type::<honba_api::QuotesQuery>("QuotesQuery");
    set.add_type::<honba_api::BarsQuery>("BarsQuery");
    set.add_type::<honba_api::DepthQuery>("DepthQuery");
    set.add_type::<honba_api::StrategiesRequest>("StrategiesRequest");
    set.add_type::<honba_api::BacktestRequest>("BacktestRequest");
    set.add_type::<honba_api::SweepRequest>("SweepRequest");
    set.add_type::<honba_api::OrdersRequest>("OrdersRequest");
}

/// Adds the response DTOs to `set`.
pub fn register_responses(set: &mut SchemaSet) {
    set.add_type::<honba_api::InstrumentsResponse>("InstrumentsResponse");
    set.add_type::<honba_api::QuotesResponse>("QuotesResponse");
    set.add_type::<honba_api::BarsResponse>("BarsResponse");
    set.add_type::<honba_api::DepthResponse>("DepthResponse");
    set.add_type::<honba_api::StrategiesResponse>("StrategiesResponse");
    set.add_type::<honba_api::BacktestResponse>("BacktestResponse");
    set.add_type::<honba_api::BacktestMetrics>("BacktestMetrics");
    set.add_type::<honba_api::SweepResponse>("SweepResponse");
    set.add_type::<honba_api::SweepReportResponse>("SweepReportResponse");
    set.add_type::<honba_api::OrdersResponse>("OrdersResponse");
    set.add_type::<honba_api::PositionsResponse>("PositionsResponse");
    set.add_type::<honba_api::TradesResponse>("TradesResponse");
}

/// Builds the full registry used by every artifact.
pub fn full_registry() -> SchemaSet {
    let mut set = SchemaSet::new();
    register_all(&mut set);
    register_requests(&mut set);
    register_responses(&mut set);
    set
}

/// Returns the JSON Schema of `T`, normalized into the shared representation.
pub fn schema_of<T: JsonSchema>(preferred_name: &str) -> Value {
    let mut set = SchemaSet::new();
    set.add_type::<T>(preferred_name);
    let name = set.names().first().copied().unwrap_or(preferred_name);
    set.get(name).cloned().unwrap_or_else(|| json!({}))
}
