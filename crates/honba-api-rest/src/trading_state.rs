//! `GET` / `PUT /trading-state`: read and change the operator trading state at runtime
//! (ADR 0018 decision 7). The state lives in the shared [`AppState`] cell, so the order gate and
//! `POST /positions/close` see a change on the next request. Every transition is allowed; a
//! `PUT` of the current state is a no-op that answers `previous == state`.
//!
//! The REST crate has no operator journal (the auth audit log records who called which route,
//! not bodies), so a change is logged through `tracing` with the optional `reason`.

use std::sync::Arc;

use axum::extract::State;
use axum::response::Response;
use honba_api::{TradingStateChange, TradingStateRequest, TradingStateResponse};
use tracing::info;

use crate::market::success;
use crate::{ApiJson, AppState};

/// `GET /trading-state`: the state in force.
pub(crate) async fn get_trading_state(State(state): State<Arc<AppState>>) -> Response {
    success(TradingStateResponse {
        state: state.trading_state(),
    })
}

/// `PUT /trading-state`: set the state and report the one it replaced.
pub(crate) async fn put_trading_state(
    State(state): State<Arc<AppState>>,
    ApiJson(request): ApiJson<TradingStateRequest>,
) -> Response {
    let previous = state.set_trading_state(request.state);
    let reason = request.reason.as_deref().unwrap_or("");
    if previous == request.state {
        info!(state = ?request.state, reason, "trading state unchanged");
    } else {
        info!(from = ?previous, to = ?request.state, reason, "trading state changed");
    }
    success(TradingStateChange {
        state: request.state,
        previous,
    })
}
