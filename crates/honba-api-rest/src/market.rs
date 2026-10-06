//! Handlers for `GET /instruments`, `GET /instruments/{id}` and `GET /bars/{id}`.
//!
//! Each handler resolves its request with the pure functions in `honba-api`,
//! calls a read port, and answers inside the standard envelope. Failures use
//! the same envelope: 404 for an unknown instrument or an unavailable
//! timeframe, 422 for a bad query, and a port failure maps by kind.

use std::sync::Arc;

use axum::{
    async_trait,
    extract::{rejection::QueryRejection, FromRequestParts, Path, Query, State},
    http::{request::Parts, StatusCode},
    response::{IntoResponse, Json, Response},
};
use honba_api::{
    instrument_json, parse_instrument_id, ApiResponse, BarsQuery, BarsResponse, ErrorCode,
    ErrorDetail, InstrumentsQuery, InstrumentsResponse, ResponseEnvelope,
};
use honba_ports::{BarRequest, PortError};
use serde::de::DeserializeOwned;

use crate::AppState;

/// Query-string extractor whose rejections are the standard error envelope
/// (`validation_invalid_request`, status 422) rather than axum's plain text.
#[derive(Debug)]
pub struct ApiQuery<T>(pub T);

/// Rejection of [`ApiQuery`].
#[derive(Debug)]
pub struct ApiQueryRejection(QueryRejection);

impl IntoResponse for ApiQueryRejection {
    fn into_response(self) -> Response {
        failure(
            StatusCode::UNPROCESSABLE_ENTITY,
            ErrorDetail::new(ErrorCode::ValidationInvalidRequest, self.0.body_text()),
        )
    }
}

#[async_trait]
impl<S, T> FromRequestParts<S> for ApiQuery<T>
where
    T: DeserializeOwned,
    S: Send + Sync,
{
    type Rejection = ApiQueryRejection;

    async fn from_request_parts(parts: &mut Parts, state: &S) -> Result<Self, Self::Rejection> {
        Query::<T>::from_request_parts(parts, state)
            .await
            .map(|Query(value)| Self(value))
            .map_err(ApiQueryRejection)
    }
}

fn failure(status: StatusCode, detail: ErrorDetail) -> Response {
    let body: ResponseEnvelope<serde_json::Value> = ApiResponse::error(detail);
    (status, Json(body)).into_response()
}

fn success<T: serde::Serialize>(data: T) -> Response {
    let body: ResponseEnvelope<T> = ApiResponse::success(data);
    (StatusCode::OK, Json(body)).into_response()
}

fn unprocessable(detail: ErrorDetail) -> Response {
    failure(StatusCode::UNPROCESSABLE_ENTITY, detail)
}

fn instrument_not_found(id: &honba_messages::InstrumentId) -> Response {
    failure(
        StatusCode::NOT_FOUND,
        ErrorDetail::new(
            ErrorCode::InstrumentNotFound,
            format!("instrument {id} not found"),
        ),
    )
}

/// Maps a port failure to a status and an envelope code.
fn port_failure(error: PortError) -> Response {
    let (status, code) = match &error {
        PortError::Unsupported(_) => (StatusCode::NOT_FOUND, ErrorCode::MarketDataUnavailable),
        PortError::InvalidRequest(_) => (
            StatusCode::UNPROCESSABLE_ENTITY,
            ErrorCode::ValidationInvalidRequest,
        ),
        PortError::Unavailable(_) => (
            StatusCode::SERVICE_UNAVAILABLE,
            ErrorCode::MarketDataUnavailable,
        ),
        PortError::Timeout => (StatusCode::GATEWAY_TIMEOUT, ErrorCode::Timeout),
        PortError::Transport(_) => (StatusCode::BAD_GATEWAY, ErrorCode::TransportError),
        _ => (StatusCode::INTERNAL_SERVER_ERROR, ErrorCode::InternalError),
    };
    failure(status, ErrorDetail::new(code, error.to_string()))
}

/// `GET /instruments`: every instrument passing the query filters, in id order.
pub(crate) async fn get_instruments(
    State(state): State<Arc<AppState>>,
    ApiQuery(query): ApiQuery<InstrumentsQuery>,
) -> Response {
    match state.instruments.list_instruments().await {
        Ok(mut all) => {
            all.sort_by(|a, b| a.id().cmp(b.id()));
            let instruments = all
                .iter()
                .filter(|instrument| query.matches(instrument.id()))
                .map(instrument_json)
                .collect();
            success(InstrumentsResponse { instruments })
        }
        Err(error) => port_failure(error),
    }
}

/// `GET /instruments/{id}`: one instrument, or 404 `instrument_not_found`.
pub(crate) async fn get_instrument_by_id(
    State(state): State<Arc<AppState>>,
    Path(raw): Path<String>,
) -> Response {
    let id = match parse_instrument_id(&raw) {
        Ok(id) => id,
        Err(detail) => return unprocessable(detail),
    };
    match state.instruments.get_instrument(&id).await {
        Ok(Some(instrument)) => success(instrument_json(&instrument)),
        Ok(None) => instrument_not_found(&id),
        Err(error) => port_failure(error),
    }
}

/// `GET /bars/{id}`: bars in ascending `ts_event` over `[from, to)`.
pub(crate) async fn get_bars(
    State(state): State<Arc<AppState>>,
    Path(raw): Path<String>,
    ApiQuery(query): ApiQuery<BarsQuery>,
) -> Response {
    let id = match parse_instrument_id(&raw) {
        Ok(id) => id,
        Err(detail) => return unprocessable(detail),
    };
    let resolved = match query.resolve() {
        Ok(resolved) => resolved,
        Err(detail) => return unprocessable(detail),
    };
    match state.instruments.get_instrument(&id).await {
        Ok(Some(_)) => {}
        Ok(None) => return instrument_not_found(&id),
        Err(error) => return port_failure(error),
    }
    let request = match BarRequest::new(id, resolved.spec, resolved.from, resolved.to) {
        Ok(request) => request,
        Err(error) => return port_failure(error),
    };
    match state.bars.read_bars(&request).await {
        Ok(bars) => success(BarsResponse { bars }),
        Err(error) => port_failure(error),
    }
}
