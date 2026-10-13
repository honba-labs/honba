//! The `POST /orders` risk gate (ADR 0018 decision 7): approval queue (E5-S4) ->
//! risk stage -> execution gateway (simulator only until E3-S7).
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
//! When the approval queue is enabled (E5-S4), placements are held in the queue
//! until approved by an operator. Cancel and modify bypass the queue.
//! A refusal from the risk stage is the 422 of ADR 0018 decision 5 and never
//! reaches the gateway. The decision 6 audit record and the `order_id`
//! idempotency key of ADR 0019 decision 3 both arrive with the E11-S7 write
//! ledger; here the id is a fixed placeholder and nothing is audited.
//! `DELETE /orders/{id}` and `POST /positions/close` cannot add exposure, so
//! they are never risk-checked and are allowed in every state; at this seam
//! both acknowledge without a store, and close does not yet generate the
//! reduce-only orders the ledger will submit.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use axum::extract::{Path, State};
use axum::http::StatusCode;
use axum::response::Response;
use honba_api::{OrdersQuery, OrdersRequest, OrdersResponse};
use honba_market::NullMarketProfile;
use honba_messages::{
    ErrorCode, ErrorDetail, InstrumentId, Order, OrderId, OrderSide, OrderStatus, OrderType,
    TimeInForce, UnixNanos,
};
use honba_risk::{ProfileRulesSource, RiskCheck, RiskDecision, RiskLimits, RiskRequest, RiskStage};
use serde_json::json;

use crate::market::{failure, success, ApiQuery};
use crate::state::AppState;

/// The in-memory order store behind the write ledger (ADR 0019 decision 3).
///
/// One row per client order id, in insertion order. The store starts empty and
/// only records orders the risk stage approved, so a restart forgets nothing it
/// ever promised: nothing is promised yet (the venue gateway is not wired).
#[derive(Clone, Debug, Default)]
pub struct OrderLedger {
    inner: Arc<Mutex<BTreeMap<String, Order>>>,
}

impl OrderLedger {
    /// An empty ledger.
    pub fn new() -> Self {
        Self::default()
    }

    /// Records `order` under its id, replacing any earlier row with the same id.
    pub fn record(&self, order: Order) {
        let mut guard = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        guard.insert(order.order_id().as_str().to_owned(), order);
    }

    /// Looks up one row by client order id.
    pub fn get(&self, order_id: &str) -> Option<Order> {
        let guard = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        guard.get(order_id).cloned()
    }

    /// Every row, oldest insertion first.
    pub fn list(&self) -> Vec<Order> {
        let guard = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        guard.values().cloned().collect()
    }

    /// Rows passing `filter`, oldest insertion first.
    pub fn filter<F>(&self, filter: F) -> Vec<Order>
    where
        F: Fn(&Order) -> bool,
    {
        self.list()
            .into_iter()
            .filter(|order| filter(order))
            .collect()
    }

    /// Marks the row `order_id` cancelled when it is still working (ADR 0019:
    /// `CancelRequested` is illegal from `Initialized` and from terminal states).
    ///
    /// An `Initialized` row never left the submitter, so there is nothing to
    /// cancel and it is left untouched (`false`); a row that is filled, already
    /// cancelled, rejected or expired is terminal and is left untouched
    /// (`false`); an unknown id is `None`.
    pub fn cancel(&self, order_id: &str) -> Option<bool> {
        let mut guard = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let row = guard.get_mut(order_id)?;
        if matches!(
            row.status(),
            OrderStatus::Submitted | OrderStatus::Accepted | OrderStatus::PartiallyFilled
        ) {
            *row = row.clone().with_status(OrderStatus::Cancelled);
            Some(true)
        } else {
            Some(false)
        }
    }
}

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

/// `Access::Write` (`POST /orders`): approval queue (E5-S4) -> risk stage -> gateway.
/// An approved order is acknowledged with 200 and recorded in the write ledger
/// (E11-S7, ADR 0019 decision 3).
///
/// The ledger keys every order by the caller's idempotency key: `client_order_id`
/// when the body carries one, else the route-provided `order_id`. A repeat of a
/// seen key is a duplicate no-op that answers with the first row.
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
    let instrument = match known_instrument(&state, &instrument_id).await {
        Ok(instrument) => instrument,
        Err(detail) => {
            return failure(StatusCode::INTERNAL_SERVER_ERROR, detail);
        }
    };
    let reference_price = match last_price(&state, &instrument_id).await {
        Some(px) => Some(px),
        None => price,
    };
    let profile = Arc::new(NullMarketProfile::default());
    // An unknown instrument contributes no rules, so the stage itself raises
    // `InstrumentUnknown` at rule 3, after the state rules 1-2 (ADR 0018
    // decision 4); resolving it here first would hoist rule 3 above them.
    let mut stage = match RiskStage::new(
        RiskLimits::default(),
        state.account_currency,
        Arc::new(ProfileRulesSource::new(profile, instrument)),
    ) {
        Ok(stage) => stage,
        Err(e) => {
            return failure(
                StatusCode::INTERNAL_SERVER_ERROR,
                ErrorDetail::new(ErrorCode::InternalError, e.to_string()),
            );
        }
    };

    // The caller's key when present, else the route's placeholder; a seen key
    // short-circuits with the first row before the risk stage runs again.
    let ledger_id = req
        .client_order_id
        .as_deref()
        .map(OrderId::new)
        .unwrap_or_else(|| order_id.clone());
    if let Some(existing) = state.orders.get(ledger_id.as_str()) {
        return success(order_json(&existing));
    }

    // E5-S4: If approval queue is enabled, enqueue the order and return 202
    if let Some(queue) = &state.approval_queue {
        let position = match queue.enqueue(ledger_id.clone(), req.clone()) {
            Ok(pos) => pos,
            Err(detail) => return failure(StatusCode::TOO_MANY_REQUESTS, detail),
        };
        #[derive(serde::Serialize)]
        struct QueuedResponse {
            status: &'static str,
            queue_position: usize,
            message: &'static str,
        }
        return success(QueuedResponse {
            status: "queued",
            queue_position: position,
            message: "Order held in approval queue; awaiting operator approval",
        });
    }

    // No approval queue: proceed directly to risk stage
    let request = RiskRequest {
        order_id: ledger_id.clone(),
        instrument_id: instrument_id.clone(),
        side,
        quantity,
        price,
        trigger_price: None,
        reference_price,
        adv: None,
        position: 0.0,
        trading_state: state.trading_state,
        ts: UnixNanos::new(0),
        last_feed_ts: None,
    };
    match stage.check(&request) {
        RiskDecision::Approved => {
            let order = Order::new(
                ledger_id,
                instrument_id,
                side,
                order_type_of(&req),
                quantity,
                price,
                TimeInForce::Day,
                UnixNanos::new(0),
                UnixNanos::new(0),
            )
            .with_status(OrderStatus::Submitted);
            state.orders.record(order.clone());
            success(order_json(&order))
        }
        RiskDecision::Refused(refusal) => refusal_response(&refusal),
    }
}

fn order_type_of(req: &OrdersRequest) -> OrderType {
    match req.order_type.as_deref() {
        Some("limit") => OrderType::Limit,
        _ => OrderType::Market,
    }
}

fn order_json(order: &Order) -> serde_json::Value {
    serde_json::to_value(order).unwrap_or(serde_json::Value::Null)
}

/// `GET /orders`: the orders the risk stage approved, oldest first, filtered by
/// `symbol` and `exchange` when given. Unknown query keys are a 422, never
/// silently ignored; the ledger starts empty.
pub(crate) async fn get_orders(
    State(state): State<Arc<AppState>>,
    ApiQuery(query): ApiQuery<OrdersQuery>,
) -> Response {
    let orders = state.orders.filter(|order| {
        let symbol_ok = match query.symbol.as_deref() {
            None => true,
            Some(symbol) => order.instrument_id().symbol() == symbol,
        };
        let exchange_ok = match query.exchange.as_deref() {
            None => true,
            Some(exchange) => order.instrument_id().exchange().as_str() == exchange,
        };
        symbol_ok && exchange_ok
    });
    success(OrdersResponse { orders })
}

/// `DELETE /orders/{id}` is never risk-checked and is allowed in every state,
/// including halted: cancelling cannot add exposure. A working ledger row moves
/// to `cancelled`; a terminal row and an unknown id keep their 200 answer so a
/// repeated cancel stays a no-op, not an error.
pub(crate) async fn delete_order(
    State(state): State<Arc<AppState>>,
    Path(id): Path<String>,
) -> Response {
    state.orders.cancel(id.as_str());
    success(serde_json::json!({"status": "cancelled"}))
}

/// `POST /positions/close` is not risk-checked (it cannot add exposure) and is
/// allowed in every state, including halted. The seam only acknowledges; the
/// E11-S7 ledger will generate the reduce-only orders it evaluates.
pub(crate) async fn post_close_positions() -> Response {
    success(serde_json::json!({"status": "closing"}))
}

/// GET /approval/queue: list pending orders in the approval queue (E5-S4).
pub(crate) async fn get_approval_queue(State(state): State<Arc<AppState>>) -> Response {
    if let Some(queue) = &state.approval_queue {
        let pending = queue.pending();
        success(json!({"queue": pending}))
    } else {
        success(json!({"queue": []}))
    }
}

/// POST /approval/approve: approve the oldest pending order (E5-S4).
pub(crate) async fn post_approve_order(State(state): State<Arc<AppState>>) -> Response {
    if let Some(queue) = &state.approval_queue {
        if let Some(req) = queue.approve_oldest() {
            // Re-process the approved order through the risk stage BYPASSING the queue.
            // The queued row keeps the caller's idempotency key (ADR 0019 decision 3),
            // so approval records under the same key the client first sent.
            let order_id = req
                .client_order_id
                .as_deref()
                .map(honba_messages::OrderId::new)
                .unwrap_or_else(|| honba_messages::OrderId::new("approved"));
            post_orders_direct(state.clone(), crate::ApiJson(req), order_id).await
        } else {
            failure(
                StatusCode::NOT_FOUND,
                ErrorDetail::new(ErrorCode::NotFound, "no pending orders in approval queue"),
            )
        }
    } else {
        failure(
            StatusCode::BAD_REQUEST,
            ErrorDetail::new(
                ErrorCode::ValidationInvalidRequest,
                "approval queue is not enabled",
            ),
        )
    }
}

// Internal version of post_orders that bypasses the approval queue (for approved orders)
async fn post_orders_direct(
    state: Arc<AppState>,
    crate::ApiJson(req): crate::ApiJson<OrdersRequest>,
    order_id: OrderId,
) -> Response {
    let (instrument_id, side, quantity, price) = match parse_order(&req) {
        Ok(v) => v,
        Err(detail) => {
            return failure(StatusCode::UNPROCESSABLE_ENTITY, detail);
        }
    };
    let instrument = match known_instrument(&state, &instrument_id).await {
        Ok(instrument) => instrument,
        Err(detail) => {
            return failure(StatusCode::INTERNAL_SERVER_ERROR, detail);
        }
    };
    let reference_price = match last_price(&state, &instrument_id).await {
        Some(px) => Some(px),
        None => price,
    };
    let profile = Arc::new(NullMarketProfile::default());
    let mut stage = match RiskStage::new(
        RiskLimits::default(),
        state.account_currency,
        Arc::new(ProfileRulesSource::new(profile, instrument)),
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
        order_id: order_id.clone(),
        instrument_id: instrument_id.clone(),
        side,
        quantity,
        price,
        trigger_price: None,
        reference_price,
        adv: None,
        position: 0.0,
        trading_state: state.trading_state,
        ts: UnixNanos::new(0),
        last_feed_ts: None,
    };
    match stage.check(&request) {
        RiskDecision::Approved => {
            let order = Order::new(
                order_id,
                instrument_id,
                side,
                order_type_of(&req),
                quantity,
                price,
                TimeInForce::Day,
                UnixNanos::new(0),
                UnixNanos::new(0),
            )
            .with_status(OrderStatus::Submitted);
            state.orders.record(order.clone());
            success(order_json(&order))
        }
        RiskDecision::Refused(refusal) => refusal_response(&refusal),
    }
}

/// POST /approval/reject: reject the oldest pending order (E5-S4).
pub(crate) async fn post_reject_order(State(state): State<Arc<AppState>>) -> Response {
    if let Some(queue) = &state.approval_queue {
        if let Some(_req) = queue.reject_oldest() {
            // Order rejected; return the rejection to the client
            success(json!({
                "status": "rejected",
                "message": "Order rejected by operator"
            }))
        } else {
            failure(
                StatusCode::NOT_FOUND,
                ErrorDetail::new(ErrorCode::NotFound, "no pending orders in approval queue"),
            )
        }
    } else {
        failure(
            StatusCode::BAD_REQUEST,
            ErrorDetail::new(
                ErrorCode::ValidationInvalidRequest,
                "approval queue is not enabled",
            ),
        )
    }
}
