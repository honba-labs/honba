//! Orders and their enumeration types.

use serde::{Deserialize, Serialize};

use crate::events::timestamp::UnixNanos;
use crate::identifiers::{InstrumentId, OrderId};

/// Which side of the book an order sits on.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
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

/// The kind of execution instruction.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
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
}

/// The current lifecycle state of an order.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[non_exhaustive]
pub enum OrderStatus {
    /// Created locally, not yet sent.
    Initialized,
    /// Sent to the venue, awaiting acknowledgement.
    Submitted,
    /// Acknowledged by the venue.
    Accepted,
    /// Partially filled.
    PartiallyFilled,
    /// Fully filled.
    Filled,
    /// Cancelled by the client or venue.
    Cancelled,
    /// Rejected by the venue.
    Rejected,
    /// Expired according to its time-in-force.
    Expired,
}

/// How long an order remains active.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
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

/// A client order.
///
/// ```
/// use honba_messages::{
///     InstrumentId, Order, OrderId, OrderSide, OrderStatus, OrderType,
///     TimeInForce, UnixNanos, Venue,
/// };
///
/// let order = Order::new(
///     OrderId::new("O-1"),
///     InstrumentId::new("NIFTY50", Venue::new("NSE")),
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
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Order {
    order_id: OrderId,
    instrument_id: InstrumentId,
    side: OrderSide,
    order_type: OrderType,
    quantity: f64,
    price: Option<f64>,
    /// Stop trigger price; `None` unless the order is a stop order.
    trigger_price: Option<f64>,
    status: OrderStatus,
    time_in_force: TimeInForce,
    ts_event: UnixNanos,
    ts_init: UnixNanos,
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
        debug_assert!(quantity > 0.0, "order quantity must be positive");
        Self {
            order_id,
            instrument_id,
            side,
            order_type,
            quantity,
            price,
            trigger_price: None,
            status: OrderStatus::Initialized,
            time_in_force,
            ts_event,
            ts_init,
        }
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

    /// Returns the current status.
    pub fn status(&self) -> OrderStatus {
        self.status
    }

    /// Returns the time-in-force.
    pub fn time_in_force(&self) -> TimeInForce {
        self.time_in_force
    }

    /// Returns the venue timestamp.
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
    ///     InstrumentId, Order, OrderId, OrderSide, OrderType, TimeInForce, UnixNanos, Venue,
    /// };
    ///
    /// let order = Order::new(
    ///     OrderId::new("O-1"),
    ///     InstrumentId::new("NIFTY50", Venue::new("NSE")),
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

    /// Sets the status, returning `self` for chaining.
    pub fn with_status(mut self, status: OrderStatus) -> Self {
        self.status = status;
        self
    }
}
