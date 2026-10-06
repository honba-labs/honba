#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! REST API server for Honba platform using axum.
//!
//! Provides read-only endpoints as specified in Phase 2 (E11-S3, E11-S4).
//! All responses are wrapped in the versioned envelope from `honba-api`.

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::Json,
    routing::{delete, get, post},
    Router,
};
use honba_api::{
    ApiResponse, BacktestRequest, BacktestResponse, BarsQuery, BarsResponse, Capabilities,
    CapabilitiesResponse, ErrorCode, ErrorDetail, InstrumentsQuery, InstrumentsResponse,
    OrdersRequest, OrdersResponse, QuotesQuery, QuotesResponse, ResponseEnvelope, RunStatus,
    StrategiesRequest, StrategiesResponse, SweepRequest, SweepResponse, VerifyStrategyRequest,
    VerifyStrategyResponse,
};
use std::sync::Arc;
use tower_http::{compression::CompressionLayer, cors::CorsLayer, trace::TraceLayer};

/// App state for REST API.
#[derive(Clone, Debug, Default)]
pub struct AppState {
    // Placeholder state - will be wired to actual engine/data in later phases
}

/// Create the API router.
pub fn api_router() -> Router {
    let state = Arc::new(AppState::default());
    Router::new()
        .route("/capabilities", get(get_capabilities))
        .route("/health", get(get_health))
        .route("/schema", get(get_schema))
        .route("/instruments", get(get_instruments))
        .route("/instruments/:id", get(get_instrument_by_id))
        .route("/quotes", get(get_quotes))
        .route("/bars/:id", get(get_bars))
        .route("/depth/:id", get(get_depth))
        .route("/strategies", post(post_strategies).get(get_strategies))
        .route("/strategies/verify", post(post_verify_strategy))
        .route("/backtests", post(post_backtests))
        .route("/backtests/:id", get(get_backtest_by_id))
        .route("/backtests/:id/journal", get(get_backtest_journal))
        .route("/sweeps", post(post_sweeps))
        .route("/sweeps/:id", get(get_sweep_by_id))
        .route("/orders", post(post_orders).get(get_orders))
        .route("/orders/:id", delete(delete_order))
        .route("/positions/close", post(post_close_positions))
        .route("/screener/scan", get(get_screener_scan))
        .route("/journals/:id", get(get_journal_by_id))
        .with_state(state)
        .layer(CompressionLayer::new())
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http())
}

/// Get API capabilities.
async fn get_capabilities(
    State(_state): State<Arc<AppState>>,
) -> Json<ResponseEnvelope<CapabilitiesResponse>> {
    let manifest = honba_api::CapabilityManifest {
        crates: vec![
            "honba-api".to_string(),
            "honba-api-rest".to_string(),
            "honba-messages".to_string(),
            "honba-entities".to_string(),
        ],
        market_packs: vec!["india".to_string(), "null".to_string()],
        endpoints: vec![
            "GET /capabilities".to_string(),
            "GET /health".to_string(),
            "GET /schema".to_string(),
            "GET /instruments".to_string(),
            "GET /quotes".to_string(),
            "GET /bars".to_string(),
            "GET /depth".to_string(),
        ],
        toolsets: vec!["strategies".to_string(), "indicators".to_string()],
        adapters: vec![],
        features: std::collections::BTreeMap::new(),
    };
    let caps = Capabilities {
        capabilities: manifest,
    };
    Json(ApiResponse::success(caps))
}

/// Get health status.
async fn get_health() -> Json<ResponseEnvelope<serde_json::Value>> {
    Json(ApiResponse::success(serde_json::json!({"status": "ok"})))
}

/// Get schema bundle.
async fn get_schema() -> Json<ResponseEnvelope<serde_json::Value>> {
    // Return basic schema info - codegen will populate full schema
    Json(ApiResponse::success(serde_json::json!({
        "openapi": "3.1.0",
        "version": honba_api::API_VERSION
    })))
}

async fn get_instruments(
    State(_state): State<Arc<AppState>>,
    Query(_query): Query<InstrumentsQuery>,
) -> Json<ResponseEnvelope<InstrumentsResponse>> {
    Json(ApiResponse::success(InstrumentsResponse {
        instruments: vec![],
    }))
}

async fn get_instrument_by_id(
    Path(_id): Path<String>,
) -> Json<ResponseEnvelope<serde_json::Value>> {
    Json(ApiResponse::error(ErrorDetail::new(
        ErrorCode::InstrumentNotFound,
        "instrument not found",
    )))
}

async fn get_quotes(
    State(_state): State<Arc<AppState>>,
    Query(_query): Query<QuotesQuery>,
) -> Json<ResponseEnvelope<QuotesResponse>> {
    Json(ApiResponse::success(QuotesResponse { quotes: vec![] }))
}

async fn get_bars(
    Path(_id): Path<String>,
    Query(_query): Query<BarsQuery>,
) -> Json<ResponseEnvelope<BarsResponse>> {
    Json(ApiResponse::success(BarsResponse { bars: vec![] }))
}

async fn get_depth(Path(_id): Path<String>) -> Json<ResponseEnvelope<serde_json::Value>> {
    Json(ApiResponse::success(serde_json::json!({})))
}

async fn get_strategies() -> Json<ResponseEnvelope<StrategiesResponse>> {
    Json(ApiResponse::success(StrategiesResponse {
        strategies: vec![],
    }))
}

async fn post_strategies(
    Json(_req): Json<StrategiesRequest>,
) -> Json<ResponseEnvelope<StrategiesResponse>> {
    Json(ApiResponse::success(StrategiesResponse {
        strategies: vec![],
    }))
}

/// Verifies a manifest and returns its IR; a manifest that does not verify is
/// a 422 carrying `validation_invalid_request` and the reason code.
async fn post_verify_strategy(
    Json(manifest): Json<VerifyStrategyRequest>,
) -> (StatusCode, Json<ResponseEnvelope<VerifyStrategyResponse>>) {
    match honba_api::verify_strategy(manifest) {
        Ok(ir) => (StatusCode::OK, Json(ApiResponse::success(ir))),
        Err(detail) => (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(ApiResponse::error(detail)),
        ),
    }
}

async fn post_backtests(
    Json(_req): Json<BacktestRequest>,
) -> Json<ResponseEnvelope<BacktestResponse>> {
    Json(ApiResponse::success(BacktestResponse {
        run_id: "test-run-001".to_string(),
        status: RunStatus::Pending,
        metrics: None,
        assumptions: None,
    }))
}

async fn get_backtest_by_id(Path(_id): Path<String>) -> Json<ResponseEnvelope<BacktestResponse>> {
    Json(ApiResponse::success(BacktestResponse {
        run_id: _id,
        status: RunStatus::Completed,
        metrics: None,
        assumptions: None,
    }))
}

async fn get_backtest_journal(Path(_id): Path<String>) -> (StatusCode, String) {
    // SSE or plain text; for now return 501/placeholder
    (
        StatusCode::NOT_IMPLEMENTED,
        "Journal streaming via SSE not yet implemented".to_string(),
    )
}

async fn post_sweeps(Json(_req): Json<SweepRequest>) -> Json<ResponseEnvelope<SweepResponse>> {
    Json(ApiResponse::success(SweepResponse {
        job_id: "sweep-001".to_string(),
        status: RunStatus::Pending,
        report: None,
    }))
}

async fn get_sweep_by_id(Path(_id): Path<String>) -> Json<ResponseEnvelope<SweepResponse>> {
    Json(ApiResponse::success(SweepResponse {
        job_id: _id,
        status: RunStatus::Completed,
        report: None,
    }))
}

async fn get_orders() -> Json<ResponseEnvelope<OrdersResponse>> {
    Json(ApiResponse::success(OrdersResponse { orders: vec![] }))
}

async fn post_orders(Json(_req): Json<OrdersRequest>) -> Json<ResponseEnvelope<serde_json::Value>> {
    Json(ApiResponse::success(
        serde_json::json!({"order_id": "ord-001"}),
    ))
}

async fn delete_order(Path(_id): Path<String>) -> Json<ResponseEnvelope<serde_json::Value>> {
    Json(ApiResponse::success(
        serde_json::json!({"cancelled": true, "order_id": _id}),
    ))
}

async fn post_close_positions() -> Json<ResponseEnvelope<serde_json::Value>> {
    Json(ApiResponse::success(serde_json::json!({"closed": true})))
}

async fn get_screener_scan() -> Json<ResponseEnvelope<serde_json::Value>> {
    Json(ApiResponse::success(serde_json::json!({})))
}

async fn get_journal_by_id(Path(_id): Path<String>) -> (StatusCode, String) {
    (
        StatusCode::NOT_IMPLEMENTED,
        format!("Journal {} not implemented", _id),
    )
}

#[cfg(test)]
mod tests;
