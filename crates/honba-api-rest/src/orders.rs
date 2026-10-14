//! The `POST /orders` risk gate (ADR 0018 decision 7): approval queue (E5-S4) ->
//! risk stage -> execution gateway (the embedded `NextOpenSim`; no venue until E3-S7).
//!
//! The handler builds a [`RiskRequest`] from the request body plus the ports the
//! composition root already wires: rules come from the `null` profile over the
//! `InstrumentMaster` set, `position` is the signed position the write ledger
//! implies for the instrument ([`OrderLedger::position`]), `trading_state` is the
//! app's operator-set [`AppState::trading_state`] (default `Active`; no registered
//! route changes it, it is set in process via `AppState::with_trading_state`),
//! `reference_price` comes from `QuoteReader` (the close of the latest bar, via
//! mid when a book exists; the limit price when no quote exists), and `ts` /
//! `last_feed_ts` are the latest quote's `ts_event` (no wall clock; both 0 / `None`
//! when no quote exists). Approved orders are also submitted to the embedded simulator ([`crate::exec`]);
//! fills arrive when the market advances ([`AppState::advance_market`]) and move the
//! ledger row to `Filled`. Known limits: market orders only (a limit order is
//! `Rejected` by the simulator), no partial-fill quantity on the row, and order rows
//! still carry a zero timestamp.
//!
//! When the approval queue is enabled (E5-S4), placements are held in the queue
//! until approved by an operator. Cancel and modify bypass the queue.
//! A refusal from the risk stage is the 422 of ADR 0018 decision 5 and never
//! reaches the gateway. Approved placements land in the [`OrderLedger`] keyed by
//! the ADR 0019 decision 3 idempotency key; the decision 6 audit record is not
//! written yet.
//! `DELETE /orders/{id}` cannot add exposure, so it is never risk-checked and is
//! allowed in every state. `POST /positions/close` takes no body: for every instrument
//! with a non-zero ledger position it generates one market order on the opposite side
//! for the full net quantity (it can only reduce the position, so it passes the
//! reduce-only state). Those orders take the same path as `POST /orders` (approval
//! queue when enabled, else the risk stage, then the ledger), so a halted engine still
//! refuses them. `Order` has no reduce-only flag; the reduction is by construction.

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
/// only records orders the risk stage approved. Rows are advanced by the embedded
/// simulator (`Submitted` -> `Filled` / `Rejected`); no venue gateway is wired.
#[derive(Clone, Debug, Default)]
pub struct OrderLedger {
    inner: Arc<Mutex<BTreeMap<String, Order>>>,
}

impl OrderLedger {
    /// An empty ledger.
    pub fn new() -> Self {
        Self::default()
    }

    /// Sets the status of row `order_id`, returning `false` for an unknown id.
    ///
    /// Terminal rows (`Filled`, `Cancelled`, `Rejected`, `Expired`) are never changed.
    pub fn set_status(&self, order_id: &str, status: OrderStatus) -> bool {
        let mut guard = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let Some(row) = guard.get_mut(order_id) else {
            return false;
        };
        if matches!(
            row.status(),
            OrderStatus::Filled
                | OrderStatus::Cancelled
                | OrderStatus::Rejected
                | OrderStatus::Expired
        ) {
            return true;
        }
        *row = row.clone().with_status(status);
        true
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

    /// Signed position of `instrument_id` implied by the ledger: buys positive, sells
    /// negative, summed over rows that still count as exposure (`Submitted`, `Accepted`,
    /// `PartiallyFilled`, `Filled`). Cancelled, rejected, expired and not-yet-submitted
    /// rows are excluded.
    ///
    /// `Order` carries no filled quantity, so a
    /// working or partly filled row counts at its full quantity: a conservative upper
    /// bound on exposure, which is the safe side for risk rules.
    pub fn position(&self, instrument_id: &InstrumentId) -> f64 {
        let guard = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        guard
            .values()
            .filter(|o| o.instrument_id() == instrument_id)
            .filter(|o| {
                matches!(
                    o.status(),
                    OrderStatus::Submitted
                        | OrderStatus::Accepted
                        | OrderStatus::PartiallyFilled
                        | OrderStatus::Filled
                )
            })
            .map(|o| match o.side() {
                OrderSide::Buy => o.quantity(),
                OrderSide::Sell => -o.quantity(),
                _ => 0.0,
            })
            .sum()
    }

    /// The non-zero signed position of every instrument in the ledger, in
    /// instrument order (see [`OrderLedger::position`] for the netting rules).
    pub fn positions(&self) -> Vec<(InstrumentId, f64)> {
        let ids: std::collections::BTreeSet<InstrumentId> = {
            let guard = self
                .inner
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            guard.values().map(|o| o.instrument_id().clone()).collect()
        };
        ids.into_iter()
            .map(|id| {
                let qty = self.position(&id);
                (id, qty)
            })
            .filter(|(_, qty)| qty.abs() > 1e-9)
            .collect()
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

/// Builds the order-gate [`RiskRequest`].
///
/// * `position`: the signed ledger position of the instrument ([`OrderLedger::position`]).
/// * `ts` / `last_feed_ts`: this crate has no clock (and domain code must not read the
///   wall clock), so event time is the `ts_event` of the instrument's latest quote from
///   the `QuoteReader`. With a quote both are that value (feed age 0); with none, `ts`
///   is 0 and `last_feed_ts` is `None` (a staleness rule, if configured, then refuses).
async fn risk_request(
    state: &AppState,
    order_id: OrderId,
    instrument_id: &InstrumentId,
    side: OrderSide,
    quantity: f64,
    price: Option<f64>,
    reference_price: Option<f64>,
) -> RiskRequest {
    let feed_ts = match state.quotes.read_quote(instrument_id, None).await {
        Ok(Some(quote)) => Some(quote.ts_event()),
        _ => None,
    };
    RiskRequest {
        order_id,
        instrument_id: instrument_id.clone(),
        side,
        quantity,
        price,
        trigger_price: None,
        reference_price,
        adv: None,
        position: state.orders.position(instrument_id),
        trading_state: state.trading_state,
        ts: feed_ts.unwrap_or_else(|| UnixNanos::new(0)),
        last_feed_ts: feed_ts,
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
    let request = risk_request(
        &state,
        ledger_id.clone(),
        &instrument_id,
        side,
        quantity,
        price,
        reference_price,
    )
    .await;
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
            state.exec.submit(&state.orders, order.clone());
            success(order_json(
                &state.orders.get(order.order_id().as_str()).unwrap_or(order),
            ))
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
    if state.orders.cancel(id.as_str()) == Some(true) {
        state.exec.cancel(id.as_str(), UnixNanos::new(0));
    }
    success(serde_json::json!({"status": "cancelled"}))
}

/// `POST /positions/close`: for each instrument with a non-zero ledger position,
/// a market order on the opposite side for the full net quantity, which can only
/// reduce the position (so it passes the reduce-only state). Each order takes the
/// `POST /orders` path: held in the approval queue when one is enabled, else risk
/// checked and recorded; a refusal (for example a halted engine) is returned as is.
/// A flat ledger generates nothing. The body is not read, so there is no filter.
pub(crate) async fn post_close_positions(State(state): State<Arc<AppState>>) -> Response {
    for (instrument_id, net) in state.orders.positions() {
        let side = if net > 0.0 { "sell" } else { "buy" };
        let key = format!(
            "close-{}.{}-{}",
            instrument_id.symbol(),
            instrument_id.exchange(),
            state.orders.list().len()
        );
        let req = OrdersRequest {
            instrument_id: Some(instrument_id),
            side: Some(side.to_string()),
            order_type: Some("market".to_string()),
            qty: Some(net.abs()),
            price: None,
            tif: None,
            client_order_id: Some(key.clone()),
        };
        if let Some(queue) = &state.approval_queue {
            if let Err(detail) = queue.enqueue(OrderId::new(key), req) {
                return failure(StatusCode::TOO_MANY_REQUESTS, detail);
            }
        } else if let Err(response) = gate_and_record(&state, &req, OrderId::new(key)).await {
            return response;
        }
    }
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
    match gate_and_record(&state, &req, order_id).await {
        Ok(order) => success(order_json(&order)),
        Err(response) => response,
    }
}

/// Runs the risk stage for `req` and, when approved, records the order in the ledger.
async fn gate_and_record(
    state: &AppState,
    req: &OrdersRequest,
    order_id: OrderId,
) -> Result<Order, Response> {
    let (instrument_id, side, quantity, price) =
        parse_order(req).map_err(|detail| failure(StatusCode::UNPROCESSABLE_ENTITY, detail))?;
    let instrument = known_instrument(state, &instrument_id)
        .await
        .map_err(|detail| failure(StatusCode::INTERNAL_SERVER_ERROR, detail))?;
    let reference_price = match last_price(state, &instrument_id).await {
        Some(px) => Some(px),
        None => price,
    };
    let profile = Arc::new(NullMarketProfile::default());
    let mut stage = RiskStage::new(
        RiskLimits::default(),
        state.account_currency,
        Arc::new(ProfileRulesSource::new(profile, instrument)),
    )
    .map_err(|e| {
        failure(
            StatusCode::INTERNAL_SERVER_ERROR,
            ErrorDetail::new(ErrorCode::InternalError, e.to_string()),
        )
    })?;
    let request = risk_request(
        state,
        order_id.clone(),
        &instrument_id,
        side,
        quantity,
        price,
        reference_price,
    )
    .await;
    match stage.check(&request) {
        RiskDecision::Approved => {
            let order = Order::new(
                order_id,
                instrument_id,
                side,
                order_type_of(req),
                quantity,
                price,
                TimeInForce::Day,
                UnixNanos::new(0),
                UnixNanos::new(0),
            )
            .with_status(OrderStatus::Submitted);
            state.orders.record(order.clone());
            state.exec.submit(&state.orders, order.clone());
            Ok(state.orders.get(order.order_id().as_str()).unwrap_or(order))
        }
        RiskDecision::Refused(refusal) => Err(refusal_response(&refusal)),
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
