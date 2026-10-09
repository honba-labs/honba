//! Orders and their enumeration types.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::events::timestamp::UnixNanos;
use crate::identifiers::{InstrumentId, OrderId};
use crate::validation::{
    finite_opt, positive, serialize_finite, serialize_finite_opt, InvariantError,
};

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
    pub(crate) ts_event: UnixNanos,
    pub(crate) ts_init: UnixNanos,
    /// A cancel was asked for and not yet answered (ADR 0019); only valid
    /// while the order is working.
    #[serde(default, skip_serializing_if = "is_false")]
    pub(crate) cancel_requested: bool,
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
    ts_event: UnixNanos,
    ts_init: UnixNanos,
    #[serde(default)]
    cancel_requested: bool,
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
            ts_event: r.ts_event,
            ts_init: r.ts_init,
            cancel_requested: r.cancel_requested,
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
            ts_event,
            ts_init,
            cancel_requested: false,
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

    /// Returns whether a cancel is pending.
    pub fn cancel_requested(&self) -> bool {
        self.cancel_requested
    }

    /// Sets the pending-cancel flag, returning `self` for chaining.
    pub fn with_cancel_requested(mut self, cancel_requested: bool) -> Self {
        self.cancel_requested = cancel_requested;
        self
    }
}
