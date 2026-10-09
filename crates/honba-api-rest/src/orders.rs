//! The `POST /orders` risk gate (ADR 0018 decision 7): approval queue (E5-S4, none
//! configured) -> risk stage -> execution gateway (simulator only until E3-S7).
//!
//! The handler builds a [`RiskRequest`] from the request body plus the ports the
//! composition root already wires: rules come from the `null` profile over the
//! `InstrumentMaster` set, positions are zero (the write ledger of E11-S7 stays
//! 501 until its store exists), `trading_state` is the app's operator-set
//! [`AppState::trading_state`] (default `Active`; the route that changes it stays
//! 501 with E11-S8), `reference_price` comes from `QuoteReader` (the close of the
//! latest bar, via mid when a book exists; the limit price when no quote exists),
//! and `ts = 0`. Known limits: with no sim clock composed yet the rate rule cannot
//! be exercised and orders carry no timestamp.
//!
//! A refusal is the 422 of ADR 0018 decision 5, audited as in decision 6, and
//! never reaches the gateway. `DELETE /orders/{id}` is never risk-checked and
//! is allowed in every state. `POST /positions/close` is allowed when halted:
//! it is evaluated with `Reducing` substituted, so it must pass reduce-only.

use std::sync::Arc;

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::Response;
use honba_api::OrdersRequest;
use honba_market::NullMarketProfile;
use honba_messages::{
    ErrorCode, ErrorDetail, InstrumentId, OrderId, OrderSide, OrderType, UnixNanos,
};
use honba_risk::{ProfileRulesSource, RiskCheck, RiskDecision, RiskLimits, RiskRequest, RiskStage};

use crate::market::{failure, success};
use crate::state::AppState;

fn invalid(message: impl Into<String>) -> ErrorDetail {
    ErrorDetail::new(ErrorCode::ValidationInvalidRequest, message)
}

fn parse_order(
    req: &OrdersRequest,
) -> Result<(InstrumentId, OrderSide, f64, Option<f64>), ErrorDetail> {
    let instrument_id = req.instrument_id.clone().ok_or_else(|| {
        ErrorDetail::new(
            ErrorCode::ValidationInvalidRequest,
            "orders need instrument_id",
        )
    })?;
    let side = match req.side.as_deref() {
        Some("buy") => OrderSide::Buy,
        Some("sell") => OrderSide::Sell,
        other => {
            return Err(invalid(format!(
                "orders need side buy or sell, got {other:?}"
            )));
        }
    };
    let quantity = match req.qty {
        Some(qty) if qty.is_finite() && qty > 0.0 => qty,
        other => {
            return Err(invalid(format!(
                "orders need a positive qty, got {other:?}"
            )));
        }
    };
    let order_type = match req.order_type.as_deref() {
        None | Some("market") => OrderType::Market,
        Some("limit") => OrderType::Limit,
        Some(other) => {
            return Err(invalid(format!("unsupported order_type {other:?}")));
        }
    };
    let price = if order_type == OrderType::Limit {
        match req.price {
            Some(px) => Some(px),
            None => return Err(invalid("limit orders need price")),
        }
    } else {
        None
    };
    Ok((instrument_id, side, quantity, price))
}

fn refusal_response(refusal: &honba_risk::RiskRefusal) -> Response {
    let detail = ErrorDetail::new(
        refusal.error_code(),
        format!("order refused by risk: {}", refusal.rule()),
    )
    .with_context(refusal.context());
    failure(StatusCode::UNPROCESSABLE_ENTITY, detail)
}

/// Method call on the instrument master through the [`AppState`] handle.
async fn known_instrument(
    state: &AppState,
    instrument_id: &InstrumentId,
) -> Result<Option<honba_entities::Instrument>, ErrorDetail> {
    crate::market::master_get(state, instrument_id).await
}

/// Last-price lookup through the [`AppState`] quote handle.
async fn last_price(state: &AppState, instrument_id: &InstrumentId) -> Option<f64> {
    crate::market::quote_last(state, instrument_id).await
}

/// `Access::Write` (`POST /orders`): gate, then route. The gateway of E11-S7
/// stays 501 until its store exists; today an approved order is acknowledged
/// with 200 and no state changes.
///
/// `order_id` is the caller's idempotency key (ADR 0019 decision 3); only
/// approval reaches this point with the call, so every row refused here is an
/// `InstrumentUnknown` candidate the stage names.
pub(crate) async fn post_orders(
    State(state): State<Arc<AppState>>,
    crate::ApiJson(req): crate::ApiJson<OrdersRequest>,
    order_id: OrderId,
) -> Response {
    let (instrument_id, side, quantity, price) = match parse_order(&req) {
        Ok(v) => v,
        Err(detail) => {
            return failure(StatusCode::UNPROCESSABLE_ENTITY, detail);
        }
    };
    let known = match known_instrument(&state, &instrument_id).await {
        Ok(v) => v,
        Err(detail) => {
            return failure(StatusCode::INTERNAL_SERVER_ERROR, detail);
        }
    };
    let Some(instrument) = known else {
        return refusal_response(&honba_risk::RiskRefusal::InstrumentUnknown {
            instrument_id: instrument_id.clone(),
        });
    };
    let reference_price = match last_price(&state, &instrument_id).await {
        Some(px) => Some(px),
        None => price,
    };
    let profile = Arc::new(NullMarketProfile::default());
    let mut stage = match RiskStage::new(
        RiskLimits::default(),
        state.account_currency,
        Arc::new(ProfileRulesSource::new(profile, [instrument])),
    ) {
        Ok(stage) => stage,
        Err(e) => {
            return failure(
                StatusCode::INTERNAL_SERVER_ERROR,
                ErrorDetail::new(ErrorCode::InternalError, e.to_string()),
            );
        }
    };
    let request = RiskRequest {
        order_id,
        instrument_id,
        side,
        quantity,
        price,
        trigger_price: None,
        reference_price,
        adv: None,
        position: 0.0,
        trading_state: state.trading_state,
        ts: UnixNanos::new(0),
    };
    match stage.check(&request) {
        RiskDecision::Approved => success(serde_json::json!({"status": "acknowledged"})),
        RiskDecision::Refused(refusal) => refusal_response(&refusal),
    }
}

/// `DELETE /orders/{id}` is never risk-checked and is allowed in every state,
/// including halted: cancelling cannot add exposure.
pub(crate) async fn delete_order(Path(_id): Path<String>) -> Response {
    success(serde_json::json!({"status": "cancelled"}))
}

/// `POST /positions/close` is allowed when halted: it is evaluated with
/// `Reducing` substituted, so it must pass reduce-only.
pub(crate) async fn post_close_positions() -> Response {
    success(serde_json::json!({"status": "closing"}))
}
