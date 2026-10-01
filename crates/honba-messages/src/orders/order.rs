//! Orders and their enumeration types.

use serde::{Deserialize, Serialize};

use crate::events::timestamp::UnixNanos;
use crate::identifiers::{InstrumentId, OrderId};
use crate::validation::{
    finite_opt, positive, serialize_finite, serialize_finite_opt, InvariantError,
};

crate::enum_with_all! {
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
}

crate::enum_with_all! {
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
}

crate::enum_with_all! {
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
}

crate::enum_with_all! {
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
///
/// Invariants (checked by [`Order::validate`] and on deserialization):
/// `quantity` is finite and `> 0`; `price` and `trigger_price` are finite
/// when present. An order is a record (it may come back from a venue), so
/// `side` may be `no_order_side`; intents are stricter.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(try_from = "OrderRepr")]
pub struct Order {
    order_id: OrderId,
    instrument_id: InstrumentId,
    side: OrderSide,
    order_type: OrderType,
    #[serde(serialize_with = "serialize_finite")]
    quantity: f64,
    #[serde(serialize_with = "serialize_finite_opt")]
    price: Option<f64>,
    /// Stop trigger price; `None` unless the order is a stop order.
    #[serde(serialize_with = "serialize_finite_opt")]
    trigger_price: Option<f64>,
    status: OrderStatus,
    time_in_force: TimeInForce,
    ts_event: UnixNanos,
    ts_init: UnixNanos,
}

/// The raw wire form, validated into an [`Order`].
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct OrderRepr {
    order_id: OrderId,
    instrument_id: InstrumentId,
    side: OrderSide,
    order_type: OrderType,
    quantity: f64,
    price: Option<f64>,
    trigger_price: Option<f64>,
    status: OrderStatus,
    time_in_force: TimeInForce,
    ts_event: UnixNanos,
    ts_init: UnixNanos,
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
            status: r.status,
            time_in_force: r.time_in_force,
            ts_event: r.ts_event,
            ts_init: r.ts_init,
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
            status: OrderStatus::Initialized,
            time_in_force,
            ts_event,
            ts_init,
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::identifiers::Venue;
    use crate::validation::InvariantError::*;

    fn order() -> Order {
        Order::new(
            OrderId::new("O"),
            InstrumentId::new("X", Venue::new("NSE")),
            OrderSide::Buy,
            OrderType::Limit,
            5.0,
            Some(10.0),
            TimeInForce::Day,
            1.into(),
            1.into(),
        )
    }

    #[test]
    fn validate_reports_typed_errors() {
        assert_eq!(order().validate(), Ok(()));
        let negative = Order {
            quantity: -5.0,
            ..order()
        };
        assert_eq!(
            negative.validate(),
            Err(NotPositive {
                field: "quantity",
                value: -5.0,
            })
        );
        let json = serde_json::to_value(negative).unwrap();
        let err = serde_json::from_value::<Order>(json)
            .unwrap_err()
            .to_string();
        assert!(err.contains("quantity"), "{err}");
    }

    #[test]
    fn all_lists_every_variant_once() {
        assert_eq!(
            OrderSide::ALL,
            &[OrderSide::Buy, OrderSide::Sell, OrderSide::NoOrderSide]
        );
        assert_eq!(OrderType::ALL.len(), 4);
        assert_eq!(OrderStatus::ALL.len(), 8);
        assert_eq!(TimeInForce::ALL.len(), 5);
    }

    #[test]
    fn non_finite_prices_never_serialize_as_null() {
        let nan_trigger = order().with_trigger_price(f64::NAN);
        assert!(serde_json::to_string(&nan_trigger).is_err());
        let inf_price = Order {
            price: Some(f64::INFINITY),
            ..order()
        };
        assert!(serde_json::to_string(&inf_price).is_err());
        // `None` is still written as `null`.
        let market = Order {
            price: None,
            ..order()
        };
        assert_eq!(
            serde_json::to_value(market).unwrap()["price"],
            serde_json::Value::Null
        );
    }
}
