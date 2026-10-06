#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! REST API server for Honba platform using axum.
//!
//! Provides read-only endpoints as specified in Phase 2 (E11-S3, E11-S4).
//! All responses are wrapped in the versioned envelope from `honba-api`.

use axum::{
    async_trait,
    extract::{rejection::JsonRejection, FromRequest, Path, Request, State},
    http::StatusCode,
    response::{IntoResponse, Json, Response},
    routing::{delete, get, post},
    Router,
};
use honba_api::{
    ApiResponse, BacktestRequest, Capabilities, CapabilitiesResponse, ErrorCode, ErrorDetail,
    OrdersRequest, ResponseEnvelope, StrategiesRequest, SweepRequest, VerifyStrategyRequest,
    VerifyStrategyResponse,
};
use std::sync::Arc;
use tower_http::{compression::CompressionLayer, cors::CorsLayer, trace::TraceLayer};

mod market;
mod state;

pub use market::{ApiQuery, ApiQueryRejection};
pub use state::AppState;

/// JSON body extractor whose rejections are the standard error envelope
/// (`validation_invalid_request`) rather than axum's plain-text message.
#[derive(Debug)]
pub struct ApiJson<T>(pub T);

/// Rejection of [`ApiJson`]: keeps axum's status, wraps its message in an envelope.
#[derive(Debug)]
pub struct ApiJsonRejection(JsonRejection);

impl IntoResponse for ApiJsonRejection {
    fn into_response(self) -> Response {
        let status = self.0.status();
        let detail = ErrorDetail::new(ErrorCode::ValidationInvalidRequest, self.0.body_text());
        (
            status,
            Json(ResponseEnvelope::<serde_json::Value>::error(detail)),
        )
            .into_response()
    }
}

#[async_trait]
impl<S, T> FromRequest<S> for ApiJson<T>
where
    Json<T>: FromRequest<S, Rejection = JsonRejection>,
    S: Send + Sync,
{
    type Rejection = ApiJsonRejection;

    async fn from_request(req: Request, state: &S) -> Result<Self, Self::Rejection> {
        Json::<T>::from_request(req, state)
            .await
            .map(|Json(v)| Self(v))
            .map_err(ApiJsonRejection)
    }
}

/// Create the API router over an empty catalogue.
pub fn api_router() -> Router {
    api_router_with(AppState::default())
}

/// Create the API router serving `state`.
pub fn api_router_with(state: AppState) -> Router {
    let state = Arc::new(state);
    Router::new()
        .route("/capabilities", get(get_capabilities))
        .route("/health", get(get_health))
        .route("/schema", get(get_schema))
        .route("/instruments", get(market::get_instruments))
        .route("/instruments/:id", get(market::get_instrument_by_id))
        .route("/quotes", get(market::get_quotes))
        .route("/bars/:id", get(market::get_bars))
        .route("/depth/:id", get(market::get_depth))
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

/// The 501 answer of a route that is in the contract but not built yet.
///
/// A placeholder must never look like a success: an empty list or a made-up id
/// would be read as real state by a client or an agent.
fn not_implemented(what: &str) -> (StatusCode, Json<ResponseEnvelope<serde_json::Value>>) {
    let detail = ErrorDetail::new(
        ErrorCode::NotImplemented,
        format!("{what} is not implemented yet"),
    );
    (
        StatusCode::NOT_IMPLEMENTED,
        Json(ApiResponse::error(detail)),
    )
}

type NotImplemented = (StatusCode, Json<ResponseEnvelope<serde_json::Value>>);

async fn get_strategies() -> NotImplemented {
    not_implemented("listing strategies")
}

async fn post_strategies(ApiJson(_req): ApiJson<StrategiesRequest>) -> NotImplemented {
    not_implemented("compiling a strategy from source")
}

/// Verifies a manifest and returns its IR; a manifest that does not verify is
/// a 422 carrying `validation_invalid_request` and the reason code.
async fn post_verify_strategy(
    ApiJson(manifest): ApiJson<VerifyStrategyRequest>,
) -> (StatusCode, Json<ResponseEnvelope<VerifyStrategyResponse>>) {
    match honba_api::verify_strategy(manifest) {
        Ok(ir) => (StatusCode::OK, Json(ApiResponse::success(ir))),
        Err(detail) => (
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(ApiResponse::error(detail)),
        ),
    }
}

async fn post_backtests(ApiJson(_req): ApiJson<BacktestRequest>) -> NotImplemented {
    not_implemented("running a backtest")
}

async fn get_backtest_by_id(Path(_id): Path<String>) -> NotImplemented {
    not_implemented("reading a backtest")
}

async fn get_backtest_journal(Path(_id): Path<String>) -> NotImplemented {
    not_implemented("streaming a backtest journal")
}

async fn post_sweeps(ApiJson(_req): ApiJson<SweepRequest>) -> NotImplemented {
    not_implemented("running a sweep")
}

async fn get_sweep_by_id(Path(_id): Path<String>) -> NotImplemented {
    not_implemented("reading a sweep")
}

async fn get_orders() -> NotImplemented {
    not_implemented("listing orders")
}

async fn post_orders(ApiJson(_req): ApiJson<OrdersRequest>) -> NotImplemented {
    not_implemented("placing an order")
}

async fn delete_order(Path(_id): Path<String>) -> NotImplemented {
    not_implemented("cancelling an order")
}

async fn post_close_positions() -> NotImplemented {
    not_implemented("closing positions")
}

async fn get_screener_scan() -> NotImplemented {
    not_implemented("the screener")
}

async fn get_journal_by_id(Path(_id): Path<String>) -> NotImplemented {
    not_implemented("reading a journal")
}

#[cfg(test)]
mod tests;
