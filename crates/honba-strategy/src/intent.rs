//! Order intents emitted by strategies.

use std::fmt;

use honba_messages::{InstrumentId, Order, OrderId, OrderSide, OrderType, TimeInForce, UnixNanos};
use serde::{Deserialize, Serialize};

/// A strategy's desire to trade, before it becomes a concrete [`Order`].
///
/// Strategies emit intents; the runner converts them into orders with
/// unique ids and timestamps. This indirection keeps strategies free of
/// bookkeeping they shouldn't care about.
///
/// Price fields by order type (checked by [`OrderIntent::validate`], on
/// deserialization, and by [`OrderIntent::into_order`], so an invalid intent
/// built with a constructor or struct literal never becomes an [`Order`]):
///
/// | `order_type`  | `price` (limit) | `trigger_price` (stop) |
/// |---------------|-----------------|------------------------|
/// | `Market`      | none            | none                   |
/// | `Limit`       | required        | none                   |
/// | `StopMarket`  | none            | required               |
/// | `StopLimit`   | required        | required               |
///
/// ```
/// use honba_strategy::OrderIntent;
/// use honba_messages::{InstrumentId, OrderSide, OrderType, Venue};
///
/// let intent = OrderIntent::market_buy(
///     InstrumentId::new("NIFTY50", Venue::new("NSE")),
///     75.0,
/// );
/// assert_eq!(intent.side, OrderSide::Buy);
/// assert_eq!(intent.order_type, OrderType::Market);
/// ```
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(try_from = "OrderIntentRepr")]
pub struct OrderIntent {
    /// The instrument to trade.
    pub instrument_id: InstrumentId,
    /// Buy or sell.
    pub side: OrderSide,
    /// Quantity to trade.
    pub quantity: f64,
    /// Execution instruction.
    pub order_type: OrderType,
    /// Limit price, for `Limit` and `StopLimit` orders.
    pub price: Option<f64>,
    /// Stop trigger price, for `StopMarket` and `StopLimit` orders.
    pub trigger_price: Option<f64>,
    /// How long the order remains active.
    pub time_in_force: TimeInForce,
}

/// Why an [`OrderIntent`] violates its invariants.
#[derive(Clone, Copy, Debug, PartialEq)]
#[non_exhaustive]
pub enum IntentError {
    /// Quantity is zero, negative, or not finite.
    NonPositiveQuantity(f64),
    /// Side is neither buy nor sell.
    NoSide,
    /// A price field is NaN or infinite.
    NonFinitePrice,
    /// The order type needs a limit price but none was given.
    MissingPrice(OrderType),
    /// The order type takes no limit price but one was given.
    UnexpectedPrice(OrderType),
    /// The order type needs a trigger price but none was given.
    MissingTriggerPrice(OrderType),
    /// The order type takes no trigger price but one was given.
    UnexpectedTriggerPrice(OrderType),
}

impl fmt::Display for IntentError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            IntentError::NonPositiveQuantity(q) => write!(f, "quantity must be positive, got {q}"),
            IntentError::NoSide => f.write_str("side must be buy or sell"),
            IntentError::NonFinitePrice => f.write_str("prices must be finite"),
            IntentError::MissingPrice(t) => write!(f, "{t:?} order requires a price"),
            IntentError::UnexpectedPrice(t) => write!(f, "{t:?} order takes no price"),
            IntentError::MissingTriggerPrice(t) => {
                write!(f, "{t:?} order requires a trigger_price")
            }
            IntentError::UnexpectedTriggerPrice(t) => {
                write!(f, "{t:?} order takes no trigger_price")
            }
        }
    }
}

impl std::error::Error for IntentError {}

/// The raw wire form, validated into an [`OrderIntent`].
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct OrderIntentRepr {
    instrument_id: InstrumentId,
    side: OrderSide,
    quantity: f64,
    order_type: OrderType,
    price: Option<f64>,
    trigger_price: Option<f64>,
    time_in_force: TimeInForce,
}

impl TryFrom<OrderIntentRepr> for OrderIntent {
    type Error = IntentError;

    fn try_from(r: OrderIntentRepr) -> Result<Self, Self::Error> {
        let intent = OrderIntent {
            instrument_id: r.instrument_id,
            side: r.side,
            quantity: r.quantity,
            order_type: r.order_type,
            price: r.price,
            trigger_price: r.trigger_price,
            time_in_force: r.time_in_force,
        };
        intent.validate()?;
        Ok(intent)
    }
}

impl OrderIntent {
    fn new(
        instrument_id: InstrumentId,
        side: OrderSide,
        quantity: f64,
        order_type: OrderType,
        price: Option<f64>,
        trigger_price: Option<f64>,
    ) -> Self {
        Self {
            instrument_id,
            side,
            quantity,
            order_type,
            price,
            trigger_price,
            time_in_force: TimeInForce::Day,
        }
    }

    /// A market buy.
    pub fn market_buy(instrument_id: InstrumentId, quantity: f64) -> Self {
        Self::new(
            instrument_id,
            OrderSide::Buy,
            quantity,
            OrderType::Market,
            None,
            None,
        )
    }

    /// A market sell.
    pub fn market_sell(instrument_id: InstrumentId, quantity: f64) -> Self {
        Self::new(
            instrument_id,
            OrderSide::Sell,
            quantity,
            OrderType::Market,
            None,
            None,
        )
    }

    /// A limit buy.
    pub fn limit_buy(instrument_id: InstrumentId, quantity: f64, price: f64) -> Self {
        Self::new(
            instrument_id,
            OrderSide::Buy,
            quantity,
            OrderType::Limit,
            Some(price),
            None,
        )
    }

    /// A limit sell.
    pub fn limit_sell(instrument_id: InstrumentId, quantity: f64, price: f64) -> Self {
        Self::new(
            instrument_id,
            OrderSide::Sell,
            quantity,
            OrderType::Limit,
            Some(price),
            None,
        )
    }

    /// A stop-market buy that triggers at `trigger_price`.
    pub fn stop_buy(instrument_id: InstrumentId, quantity: f64, trigger_price: f64) -> Self {
        Self::new(
            instrument_id,
            OrderSide::Buy,
            quantity,
            OrderType::StopMarket,
            None,
            Some(trigger_price),
        )
    }

    /// A stop-market sell that triggers at `trigger_price`.
    pub fn stop_sell(instrument_id: InstrumentId, quantity: f64, trigger_price: f64) -> Self {
        Self::new(
            instrument_id,
            OrderSide::Sell,
            quantity,
            OrderType::StopMarket,
            None,
            Some(trigger_price),
        )
    }

    /// A stop-limit buy: at `trigger_price` it becomes a limit buy at `limit_price`.
    ///
    /// ```
    /// use honba_strategy::OrderIntent;
    /// use honba_messages::{InstrumentId, OrderType, Venue};
    ///
    /// let i = OrderIntent::stop_limit_buy(
    ///     InstrumentId::new("NIFTY50", Venue::new("NSE")),
    ///     75.0,
    ///     22_000.0,
    ///     22_010.0,
    /// );
    /// assert_eq!(i.order_type, OrderType::StopLimit);
    /// assert_eq!((i.trigger_price, i.price), (Some(22_000.0), Some(22_010.0)));
    /// assert!(i.validate().is_ok());
    /// ```
    pub fn stop_limit_buy(
        instrument_id: InstrumentId,
        quantity: f64,
        trigger_price: f64,
        limit_price: f64,
    ) -> Self {
        Self::new(
            instrument_id,
            OrderSide::Buy,
            quantity,
            OrderType::StopLimit,
            Some(limit_price),
            Some(trigger_price),
        )
    }

    /// A stop-limit sell: at `trigger_price` it becomes a limit sell at `limit_price`.
    pub fn stop_limit_sell(
        instrument_id: InstrumentId,
        quantity: f64,
        trigger_price: f64,
        limit_price: f64,
    ) -> Self {
        Self::new(
            instrument_id,
            OrderSide::Sell,
            quantity,
            OrderType::StopLimit,
            Some(limit_price),
            Some(trigger_price),
        )
    }

    /// Checks the intent's invariants (see the table on [`OrderIntent`]).
    pub fn validate(&self) -> Result<(), IntentError> {
        if !(self.quantity.is_finite() && self.quantity > 0.0) {
            return Err(IntentError::NonPositiveQuantity(self.quantity));
        }
        if !matches!(self.side, OrderSide::Buy | OrderSide::Sell) {
            return Err(IntentError::NoSide);
        }
        if [self.price, self.trigger_price]
            .iter()
            .flatten()
            .any(|p| !p.is_finite())
        {
            return Err(IntentError::NonFinitePrice);
        }
        let (needs_price, needs_trigger) = match self.order_type {
            OrderType::Market => (false, false),
            OrderType::Limit => (true, false),
            OrderType::StopMarket => (false, true),
            OrderType::StopLimit => (true, true),
            // `OrderType` is non-exhaustive; unknown kinds carry no price rules.
            _ => return Ok(()),
        };
        let t = self.order_type;
        match (needs_price, self.price.is_some()) {
            (true, false) => return Err(IntentError::MissingPrice(t)),
            (false, true) => return Err(IntentError::UnexpectedPrice(t)),
            _ => {}
        }
        match (needs_trigger, self.trigger_price.is_some()) {
            (true, false) => Err(IntentError::MissingTriggerPrice(t)),
            (false, true) => Err(IntentError::UnexpectedTriggerPrice(t)),
            _ => Ok(()),
        }
    }

    /// Validates the intent and converts it into a concrete order.
    ///
    /// This is the only way from an intent to an [`Order`], so an intent that
    /// breaks its invariants (for example `market_buy(id, -1.0)`, or a limit
    /// intent without a price built with a struct literal) can never become
    /// one.
    ///
    /// ```
    /// use honba_strategy::{IntentError, OrderIntent};
    /// use honba_messages::{InstrumentId, OrderId, UnixNanos, Venue};
    ///
    /// let id = InstrumentId::new("NIFTY50", Venue::new("NSE"));
    /// let err = OrderIntent::market_buy(id, -1.0)
    ///     .into_order(OrderId::new("O-1"), UnixNanos::from_u64(1))
    ///     .unwrap_err();
    /// assert_eq!(err, IntentError::NonPositiveQuantity(-1.0));
    /// ```
    pub fn into_order(self, order_id: OrderId, ts: UnixNanos) -> Result<Order, IntentError> {
        self.validate()?;
        let order = Order::new(
            order_id,
            self.instrument_id,
            self.side,
            self.order_type,
            self.quantity,
            self.price,
            self.time_in_force,
            ts,
            ts,
        );
        Ok(match self.trigger_price {
            Some(trigger) => order.with_trigger_price(trigger),
            None => order,
        })
    }
}
