//! Orders and their enumeration types.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::errors::ErrorCode;
use crate::events::timestamp::UnixNanos;
use crate::identifiers::{InstrumentId, OrderId};
use crate::validation::{
    finite_opt, non_negative, positive, serialize_finite, serialize_finite_opt, InvariantError,
};

/// Tolerance when checking that the filled quantity does not exceed the order quantity.
const FILL_EPS: f64 = 1e-9;

/// Why an order was refused: the stable [`ErrorCode`] plus a short human message.
///
/// ```
/// use honba_messages::{ErrorCode, RejectReason};
///
/// let r = RejectReason::new(ErrorCode::OrderRejected, "no_position");
/// assert_eq!(r.code(), ErrorCode::OrderRejected);
/// assert_eq!(r.message(), "no_position");
/// ```
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct RejectReason {
    code: ErrorCode,
    message: String,
}

impl RejectReason {
    /// A reason with the stable `code` and a short `message`.
    pub fn new(code: ErrorCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    /// The stable error code.
    pub fn code(&self) -> ErrorCode {
        self.code
    }

    /// The short human-readable message (venue or simulator text).
    pub fn message(&self) -> &str {
        &self.message
    }
}

crate::enum_with_all! {
    /// Which side of the book an order sits on.
    #[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
    #[serde(rename_all = "snake_case")]
    #[non_exhaustive]
    pub enum OrderSide {
        /// Buy side.
        Buy,
        /// Sell side.
        Sell,
        /// No side specified.
        NoOrderSide,
    }
}

crate::enum_with_all! {
    /// The kind of execution instruction.
    #[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
    #[serde(rename_all = "snake_case")]
    #[non_exhaustive]
    pub enum OrderType {
        /// Execute at the best available price.
        Market,
        /// Execute at or better than a limit price.
        Limit,
        /// Become a market order when a stop price is touched.
        StopMarket,
        /// Become a limit order when a stop price is touched.
        StopLimit,
        /// Trailing stop: market order once triggered.
        TrailingStop,
    }
}

crate::enum_with_all! {
    /// The current lifecycle state of an order.
    #[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
    #[serde(rename_all = "snake_case")]
    #[non_exhaustive]
    pub enum OrderStatus {
        /// Created locally, not yet sent.
        Initialized,
        /// Sent to the exchange, awaiting acknowledgement.
        Submitted,
        /// Acknowledged by the exchange.
        Accepted,
        /// Partially filled.
        PartiallyFilled,
        /// Fully filled.
        Filled,
        /// Cancelled by the client or exchange.
        Cancelled,
        /// Rejected by the exchange.
        Rejected,
        /// Expired according to its time-in-force.
        Expired,
    }
}

crate::enum_with_all! {
    /// How long an order remains active.
    #[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
    #[serde(rename_all = "snake_case")]
    #[non_exhaustive]
    pub enum TimeInForce {
        /// Good till cancelled.
        Gtc,
        /// Immediate or cancel.
        Ioc,
        /// Fill or kill.
        Fok,
        /// Valid until the end of the trading day.
        Day,
        /// Good till a specified date.
        Gtd,
    }
}

crate::enum_with_all! {
    /// The holding period a broker applies to a position (ADR 0010).
    ///
    /// This is the market-neutral vocabulary, not a broker's product codes:
    /// Zerodha's `CNC`/`MIS`/`NRML` and Dhan's `CNC`/`INTRADAY`/`MARGIN` live
    /// in the declarative mapping tables (`crate::orders::mapping`), never in
    /// the core enum.
    #[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
    #[serde(rename_all = "snake_case")]
    #[non_exhaustive]
    pub enum Product {
        /// Held overnight and settled into the demat account.
        Delivery,
        /// Squared off before the end of the trading day.
        Intraday,
        /// Carried forward under a margin/derivatives product.
        Carry,
    }
}

/// A client order.
///
/// ```
/// use honba_messages::{
///     InstrumentId, Order, OrderId, OrderSide, OrderStatus, OrderType,
///     TimeInForce, UnixNanos, Exchange,
/// };
///
/// let order = Order::new(
///     OrderId::new("O-1"),
///     InstrumentId::new("NIFTY50", Exchange::new("NSE")),
///     OrderSide::Buy,
///     OrderType::Limit,
///     75.0,
///     Some(22_000.0),
///     TimeInForce::Day,
///     UnixNanos::from_u64(1),
///     UnixNanos::from_u64(1),
/// );
/// assert_eq!(order.status(), OrderStatus::Initialized);
/// assert_eq!(order.quantity(), 75.0);
/// ```
///
/// Invariants (checked by [`Order::validate`] and on deserialization):
/// `quantity` is finite and `> 0`; `price` and `trigger_price` are finite
/// when present; `cancel_requested` is `true` only while the order is
/// `Submitted`, `Accepted` or `PartiallyFilled`. An order is a record (it may come back from an exchange), so
/// `side` may be `no_order_side`; intents are stricter.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(try_from = "OrderRepr")]
pub struct Order {
    pub(crate) order_id: OrderId,
    pub(crate) instrument_id: InstrumentId,
    pub(crate) side: OrderSide,
    pub(crate) order_type: OrderType,
    #[serde(serialize_with = "serialize_finite")]
    pub(crate) quantity: f64,
    #[serde(serialize_with = "serialize_finite_opt")]
    pub(crate) price: Option<f64>,
    /// Stop trigger price; `None` unless the order is a stop order.
    #[serde(serialize_with = "serialize_finite_opt")]
    pub(crate) trigger_price: Option<f64>,
    /// Absolute trailing stop distance (price units).
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        serialize_with = "serialize_finite_opt"
    )]
    pub(crate) trail_amount: Option<f64>,
    /// Percentage trailing stop distance (0 < p < 100).
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        serialize_with = "serialize_finite_opt"
    )]
    pub(crate) trail_percent: Option<f64>,
    pub(crate) status: OrderStatus,
    pub(crate) time_in_force: TimeInForce,
    /// Broker product the order is placed under (ADR 0010); `None` when the
    /// venue does not distinguish. Additive field: a reader without it decodes
    /// as `None` (ADR 0012 rule 1).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) product: Option<Product>,
    pub(crate) ts_event: UnixNanos,
    pub(crate) ts_init: UnixNanos,
    /// A cancel was asked for and not yet answered (ADR 0019); only valid
    /// while the order is working.
    #[serde(default, skip_serializing_if = "is_false")]
    pub(crate) cancel_requested: bool,
    /// Cumulative filled quantity (`0` until a fill; omitted from the wire while `0`).
    /// Additive field: a reader without it decodes as `0` (ADR 0012 rule 1).
    #[serde(
        default,
        skip_serializing_if = "is_zero",
        serialize_with = "serialize_finite"
    )]
    pub(crate) filled_qty: f64,
    /// Volume-weighted average fill price; `None` until a fill. Additive.
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        serialize_with = "serialize_finite_opt"
    )]
    pub(crate) avg_fill_price: Option<f64>,
    /// Why the order (or its unfilled remainder) was refused; `None` otherwise. Additive.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) reject_reason: Option<RejectReason>,
    /// Event time of the latest fill the row has seen; `None` until a fill. Additive: lets a
    /// reader order fills across rows by fill time rather than by row order.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) filled_at: Option<UnixNanos>,
}

fn is_zero(v: &f64) -> bool {
    *v == 0.0
}

fn is_false(v: &bool) -> bool {
    !*v
}

/// The raw wire form, validated into an [`Order`].
#[derive(Deserialize)]
struct OrderRepr {
    order_id: OrderId,
    instrument_id: InstrumentId,
    side: OrderSide,
    order_type: OrderType,
    quantity: f64,
    price: Option<f64>,
    trigger_price: Option<f64>,
    #[serde(default)]
    trail_amount: Option<f64>,
    #[serde(default)]
    trail_percent: Option<f64>,
    status: OrderStatus,
    time_in_force: TimeInForce,
    #[serde(default)]
    product: Option<Product>,
    ts_event: UnixNanos,
    ts_init: UnixNanos,
    #[serde(default)]
    cancel_requested: bool,
    #[serde(default)]
    filled_qty: f64,
    #[serde(default)]
    avg_fill_price: Option<f64>,
    #[serde(default)]
    reject_reason: Option<RejectReason>,
    #[serde(default)]
    filled_at: Option<UnixNanos>,
}

impl TryFrom<OrderRepr> for Order {
    type Error = InvariantError;

    fn try_from(r: OrderRepr) -> Result<Self, Self::Error> {
        let order = Order {
            order_id: r.order_id,
            instrument_id: r.instrument_id,
            side: r.side,
            order_type: r.order_type,
            quantity: r.quantity,
            price: r.price,
            trigger_price: r.trigger_price,
            trail_amount: r.trail_amount,
            trail_percent: r.trail_percent,
            status: r.status,
            time_in_force: r.time_in_force,
            product: r.product,
            ts_event: r.ts_event,
            ts_init: r.ts_init,
            cancel_requested: r.cancel_requested,
            filled_qty: r.filled_qty,
            avg_fill_price: r.avg_fill_price,
            reject_reason: r.reject_reason,
            filled_at: r.filled_at,
        };
        order.validate()?;
        Ok(order)
    }
}

impl Order {
    /// Creates a new order in [`OrderStatus::Initialized`].
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        order_id: OrderId,
        instrument_id: InstrumentId,
        side: OrderSide,
        order_type: OrderType,
        quantity: f64,
        price: Option<f64>,
        time_in_force: TimeInForce,
        ts_event: UnixNanos,
        ts_init: UnixNanos,
    ) -> Self {
        let order = Self {
            order_id,
            instrument_id,
            side,
            order_type,
            quantity,
            price,
            trigger_price: None,
            trail_amount: None,
            trail_percent: None,
            status: OrderStatus::Initialized,
            time_in_force,
            product: None,
            ts_event,
            ts_init,
            cancel_requested: false,
            filled_qty: 0.0,
            avg_fill_price: None,
            reject_reason: None,
            filled_at: None,
        };
        debug_assert!(
            order.validate().is_ok(),
            "invalid order: {:?}",
            order.validate()
        );
        order
    }

    /// Checks the order's invariants (see [`Order`]).
    pub fn validate(&self) -> Result<(), InvariantError> {
        positive("quantity", self.quantity)?;
        finite_opt("price", self.price)?;
        finite_opt("trigger_price", self.trigger_price)?;
        finite_opt("trail_amount", self.trail_amount)?;
        finite_opt("trail_percent", self.trail_percent)?;
        if let Some(amt) = self.trail_amount {
            if amt <= 0.0 {
                return Err(InvariantError::NotPositive {
                    field: "trail_amount",
                    value: amt,
                });
            }
        }
        if let Some(pct) = self.trail_percent {
            if !(0.0 < pct && pct < 100.0) {
                return Err(InvariantError::OutsideRange {
                    field: "trail_percent",
                });
            }
        }
        non_negative("filled_qty", self.filled_qty)?;
        if self.filled_qty > self.quantity + FILL_EPS {
            return Err(InvariantError::OutsideRange {
                field: "filled_qty",
            });
        }
        if let Some(px) = self.avg_fill_price {
            positive("avg_fill_price", px)?;
        }
        if self.cancel_requested
            && !matches!(
                self.status,
                OrderStatus::Submitted | OrderStatus::Accepted | OrderStatus::PartiallyFilled
            )
        {
            return Err(InvariantError::NotAllowed {
                field: "cancel_requested",
            });
        }
        Ok(())
    }

    /// Returns the order id.
    pub fn order_id(&self) -> &OrderId {
        &self.order_id
    }

    /// Returns the instrument.
    pub fn instrument_id(&self) -> &InstrumentId {
        &self.instrument_id
    }

    /// Returns the side.
    pub fn side(&self) -> OrderSide {
        self.side
    }

    /// Returns the order type.
    pub fn order_type(&self) -> OrderType {
        self.order_type
    }

    /// Returns the quantity.
    pub fn quantity(&self) -> f64 {
        self.quantity
    }

    /// Returns the limit price, if any.
    pub fn price(&self) -> Option<f64> {
        self.price
    }

    /// Returns the stop trigger price, if any.
    pub fn trigger_price(&self) -> Option<f64> {
        self.trigger_price
    }

    /// Returns the trail amount, if any.
    pub fn trail_amount(&self) -> Option<f64> {
        self.trail_amount
    }

    /// Returns the trail percent, if any.
    pub fn trail_percent(&self) -> Option<f64> {
        self.trail_percent
    }

    /// Returns the current status.
    pub fn status(&self) -> OrderStatus {
        self.status
    }

    /// Returns the time-in-force.
    pub fn time_in_force(&self) -> TimeInForce {
        self.time_in_force
    }

    /// Returns the broker product, if any.
    pub fn product(&self) -> Option<Product> {
        self.product
    }

    /// Returns the exchange timestamp.
    pub fn ts_event(&self) -> UnixNanos {
        self.ts_event
    }

    /// Returns the Honba timestamp.
    pub fn ts_init(&self) -> UnixNanos {
        self.ts_init
    }

    /// Sets the stop trigger price, returning `self` for chaining.
    ///
    /// ```
    /// use honba_messages::{
    ///     InstrumentId, Order, OrderId, OrderSide, OrderType, TimeInForce, UnixNanos, Exchange,
    /// };
    ///
    /// let order = Order::new(
    ///     OrderId::new("O-1"),
    ///     InstrumentId::new("NIFTY50", Exchange::new("NSE")),
    ///     OrderSide::Sell,
    ///     OrderType::StopLimit,
    ///     75.0,
    ///     Some(21_940.0),
    ///     TimeInForce::Day,
    ///     UnixNanos::from_u64(1),
    ///     UnixNanos::from_u64(1),
    /// )
    /// .with_trigger_price(21_950.0);
    /// assert_eq!(order.trigger_price(), Some(21_950.0));
    /// ```
    pub fn with_trigger_price(mut self, trigger_price: f64) -> Self {
        self.trigger_price = Some(trigger_price);
        self
    }

    /// Sets the trail amount, returning `self` for chaining.
    pub fn with_trail_amount(mut self, trail_amount: f64) -> Self {
        self.trail_amount = Some(trail_amount);
        self
    }

    /// Sets the trail percent, returning `self` for chaining.
    pub fn with_trail_percent(mut self, trail_percent: f64) -> Self {
        self.trail_percent = Some(trail_percent);
        self
    }

    /// Sets the status, returning `self` for chaining.
    pub fn with_status(mut self, status: OrderStatus) -> Self {
        self.status = status;
        self
    }

    /// Sets the broker product, returning `self` for chaining.
    pub fn with_product(mut self, product: Product) -> Self {
        self.product = Some(product);
        self
    }

    /// Returns whether a cancel is pending.
    pub fn cancel_requested(&self) -> bool {
        self.cancel_requested
    }

    /// Sets the pending-cancel flag, returning `self` for chaining.
    pub fn with_cancel_requested(mut self, cancel_requested: bool) -> Self {
        self.cancel_requested = cancel_requested;
        self
    }

    /// Cumulative filled quantity (`0` when nothing has filled).
    pub fn filled_qty(&self) -> f64 {
        self.filled_qty
    }

    /// Volume-weighted average fill price, if anything has filled.
    pub fn avg_fill_price(&self) -> Option<f64> {
        self.avg_fill_price
    }

    /// Why the order (or its remainder) was refused, if it was.
    pub fn reject_reason(&self) -> Option<&RejectReason> {
        self.reject_reason.as_ref()
    }

    /// Event time of the latest fill, if anything has filled.
    pub fn filled_at(&self) -> Option<UnixNanos> {
        self.filled_at
    }

    /// Sets the time of the latest fill, returning `self` for chaining.
    pub fn with_filled_at(mut self, filled_at: UnixNanos) -> Self {
        self.filled_at = Some(filled_at);
        self
    }

    /// Sets the cumulative filled quantity and its average price, returning `self` for chaining.
    pub fn with_fill(mut self, filled_qty: f64, avg_fill_price: f64) -> Self {
        self.filled_qty = filled_qty;
        self.avg_fill_price = Some(avg_fill_price);
        self
    }

    /// Sets the refusal reason, returning `self` for chaining.
    pub fn with_reject_reason(mut self, reason: RejectReason) -> Self {
        self.reject_reason = Some(reason);
        self
    }

    /// Clears the refusal reason, returning `self` for chaining.
    pub fn without_reject_reason(mut self) -> Self {
        self.reject_reason = None;
        self
    }
}
