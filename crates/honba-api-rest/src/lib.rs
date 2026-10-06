#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! REST API server for Honba platform using axum.
//!
//! Provides read-only endpoints as specified in Phase 2 (E11-S3, E11-S4).
//! All responses are wrapped in the versioned envelope from `honba-api`.

use axum::http::{HeaderValue, Method};
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
use tower_http::{
    compression::CompressionLayer,
    cors::{AllowOrigin, CorsLayer},
    trace::TraceLayer,
};

mod dispatch;
mod market;
mod state;

pub use dispatch::{build_target, dispatch, DispatchError};
pub use market::{ApiQuery, ApiQueryRejection, MAX_BAR_ROWS};
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

/// Server configuration beyond the data: today only the CORS allow-list.
///
/// The default sends no CORS headers at all, so browsers refuse cross-origin reads; origins
/// are opt-in, one explicit entry each (never a wildcard).
#[derive(Clone, Debug, Default)]
pub struct ApiConfig {
    cors_origins: Vec<HeaderValue>,
}

impl ApiConfig {
    /// The allowed CORS origins; empty means CORS is off.
    pub fn cors_origins(&self) -> &[HeaderValue] {
        &self.cors_origins
    }

    /// Allows exactly these origins (e.g. `https://app.example`); an entry that cannot be a
    /// header value, or a bare `*`, is an error.
    pub fn with_cors_origins<I, S>(mut self, origins: I) -> Result<Self, String>
    where
        I: IntoIterator<Item = S>,
        S: AsRef<str>,
    {
        for origin in origins {
            let origin = origin.as_ref();
            if origin == "*" {
                return Err("cors origin '*' is not allowed; list explicit origins".to_owned());
            }
            let value = HeaderValue::from_str(origin)
                .map_err(|_| format!("cors origin {origin:?} is not a valid header value"))?;
            self.cors_origins.push(value);
        }
        Ok(self)
    }
}

/// Create the API router over an empty catalogue.
pub fn api_router() -> Router {
    api_router_with(AppState::default())
}

/// Create the API router serving `state`.
pub fn api_router_with(state: AppState) -> Router {
    api_router_with_config(state, &ApiConfig::default())
}

/// Create the API router serving `state` under `config`.
pub fn api_router_with_config(state: AppState, config: &ApiConfig) -> Router {
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
        .layer(cors_layer(config))
        .layer(TraceLayer::new_for_http())
}

/// No CORS headers unless origins are listed; then only for those, read methods only.
fn cors_layer(config: &ApiConfig) -> CorsLayer {
    if config.cors_origins.is_empty() {
        return CorsLayer::new();
    }
    CorsLayer::new()
        .allow_origin(AllowOrigin::list(config.cors_origins.clone()))
        .allow_methods([Method::GET, Method::POST, Method::DELETE, Method::OPTIONS])
        .allow_headers([axum::http::header::CONTENT_TYPE])
}

/// Serves [`api_router_with`]`(state)` on `listener` until `shutdown` completes.
///
/// In-flight requests finish before this returns. The caller owns the listener, so a test can
/// bind `127.0.0.1:0` and read the chosen port; the future owns the stop signal (ctrl-c in the
/// CLI, a channel in tests). Handlers read no wall clock.
pub async fn serve(
    listener: tokio::net::TcpListener,
    state: AppState,
    shutdown: impl std::future::Future<Output = ()> + Send + 'static,
) -> std::io::Result<()> {
    serve_with_config(listener, state, &ApiConfig::default(), shutdown).await
}

/// [`serve`] under an explicit [`ApiConfig`].
pub async fn serve_with_config(
    listener: tokio::net::TcpListener,
    state: AppState,
    config: &ApiConfig,
    shutdown: impl std::future::Future<Output = ()> + Send + 'static,
) -> std::io::Result<()> {
    axum::serve(listener, api_router_with_config(state, config))
        .with_graceful_shutdown(shutdown)
        .await
}

/// Registry endpoints whose handlers answer 501 `not_implemented`.
///
/// Remove a row when its handler is built; `tests/capabilities.rs` probes the router and fails
/// if this list and the real answers disagree.
pub const NOT_IMPLEMENTED_ENDPOINTS: &[(&str, &str)] = &[
    ("POST", "/strategies"),
    ("GET", "/strategies"),
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

fn endpoint_key((method, path): &(&str, &str)) -> String {
    format!("{method} {path}")
}

/// The capability manifest: endpoints come from the registry, never a hand-kept list.
pub(crate) fn capability_manifest() -> honba_api::CapabilityManifest {
    honba_api::CapabilityManifest {
        crates: vec![
            "honba-api".to_string(),
            "honba-api-rest".to_string(),
            "honba-messages".to_string(),
            "honba-entities".to_string(),
        ],
        market_packs: vec!["india".to_string(), "null".to_string()],
        endpoints: honba_api::ENDPOINTS.iter().map(endpoint_key).collect(),
        not_implemented: NOT_IMPLEMENTED_ENDPOINTS.iter().map(endpoint_key).collect(),
        toolsets: vec!["strategies".to_string(), "indicators".to_string()],
        adapters: vec![],
        features: std::collections::BTreeMap::new(),
    }
}

/// Get API capabilities.
async fn get_capabilities(
    State(_state): State<Arc<AppState>>,
) -> Json<ResponseEnvelope<CapabilitiesResponse>> {
    Json(ApiResponse::success(Capabilities {
        capabilities: capability_manifest(),
    }))
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
