//! Order intents emitted by strategies.

use std::fmt;

use honba_messages::{InstrumentId, Order, OrderId, OrderSide, OrderType, TimeInForce, UnixNanos};
use schemars::JsonSchema;
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
/// | `order_type`    | `price` (limit) | `trigger_price` (stop) | `trail_*` (trailing stop) |
/// |-----------------|-----------------|------------------------|---------------------------|
/// | `Market`        | none            | none                   | none                      |
/// | `Limit`         | required        | none                   | none                      |
/// | `StopMarket`    | none            | required               | none                      |
/// | `StopLimit`     | required        | required               | none                      |
/// | `TrailingStop`  | none            | none                   | exactly one required      |
///
/// ```
/// use honba_strategy::OrderIntent;
/// use honba_messages::{InstrumentId, OrderSide, OrderType, Exchange};
///
/// let intent = OrderIntent::market_buy(
///     InstrumentId::new("NIFTY50", Exchange::new("NSE")),
///     75.0,
/// );
/// assert_eq!(intent.side, OrderSide::Buy);
/// assert_eq!(intent.order_type, OrderType::Market);
/// ```
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
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
    /// Absolute trailing stop distance (price units).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trail_amount: Option<f64>,
    /// Percentage trailing stop distance (0 < p < 100).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub trail_percent: Option<f64>,
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
    /// The order type takes no trail_amount but one was given.
    UnexpectedTrailAmount(OrderType),
    /// The order type takes no trail_percent but one was given.
    UnexpectedTrailPercent(OrderType),
    /// Trailing stop order requires exactly one of trail_amount or trail_percent.
    MissingTrail(OrderType),
    /// Trailing stop order cannot have both trail_amount and trail_percent.
    ConflictingTrail(OrderType),
    /// Trail amount is non-positive or not finite.
    InvalidTrailAmount(f64),
    /// Trail percent is outside (0, 100) or not finite.
    InvalidTrailPercent(f64),
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
            IntentError::UnexpectedTrailAmount(t) => {
                write!(f, "{t:?} order takes no trail_amount")
            }
            IntentError::UnexpectedTrailPercent(t) => {
                write!(f, "{t:?} order takes no trail_percent")
            }
            IntentError::MissingTrail(t) | IntentError::ConflictingTrail(t) => {
                write!(
                    f,
                    "{t:?} order requires exactly one of trail_amount or trail_percent"
                )
            }
            IntentError::InvalidTrailAmount(amt) => {
                write!(f, "trail_amount must be finite and positive, got {amt}")
            }
            IntentError::InvalidTrailPercent(pct) => {
                write!(f, "trail_percent must be in (0, 100), got {pct}")
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
    #[serde(default)]
    trail_amount: Option<f64>,
    #[serde(default)]
    trail_percent: Option<f64>,
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
            trail_amount: r.trail_amount,
            trail_percent: r.trail_percent,
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
            trail_amount: None,
            trail_percent: None,
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
    /// use honba_messages::{InstrumentId, OrderType, Exchange};
    ///
    /// let i = OrderIntent::stop_limit_buy(
    ///     InstrumentId::new("NIFTY50", Exchange::new("NSE")),
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

    /// A trailing stop buy with optional trail amount or trail percent.
    pub fn trailing_stop_buy(
        instrument_id: InstrumentId,
        quantity: f64,
        trail_amount: Option<f64>,
        trail_percent: Option<f64>,
    ) -> Self {
        Self {
            instrument_id,
            side: OrderSide::Buy,
            quantity,
            order_type: OrderType::TrailingStop,
            price: None,
            trigger_price: None,
            trail_amount,
            trail_percent,
            time_in_force: TimeInForce::Day,
        }
    }

    /// A trailing stop sell with optional trail amount or trail percent.
    pub fn trailing_stop_sell(
        instrument_id: InstrumentId,
        quantity: f64,
        trail_amount: Option<f64>,
        trail_percent: Option<f64>,
    ) -> Self {
        Self {
            instrument_id,
            side: OrderSide::Sell,
            quantity,
            order_type: OrderType::TrailingStop,
            price: None,
            trigger_price: None,
            trail_amount,
            trail_percent,
            time_in_force: TimeInForce::Day,
        }
    }

    /// A trailing stop buy trailing by a fixed amount.
    pub fn trailing_stop_amount_buy(
        instrument_id: InstrumentId,
        quantity: f64,
        trail_amount: f64,
    ) -> Self {
        Self::trailing_stop_buy(instrument_id, quantity, Some(trail_amount), None)
    }

    /// A trailing stop buy trailing by a percentage.
    pub fn trailing_stop_percent_buy(
        instrument_id: InstrumentId,
        quantity: f64,
        trail_percent: f64,
    ) -> Self {
        Self::trailing_stop_buy(instrument_id, quantity, None, Some(trail_percent))
    }

    /// A trailing stop sell trailing by a fixed amount.
    pub fn trailing_stop_amount_sell(
        instrument_id: InstrumentId,
        quantity: f64,
        trail_amount: f64,
    ) -> Self {
        Self::trailing_stop_sell(instrument_id, quantity, Some(trail_amount), None)
    }

    /// A trailing stop sell trailing by a percentage.
    pub fn trailing_stop_percent_sell(
        instrument_id: InstrumentId,
        quantity: f64,
        trail_percent: f64,
    ) -> Self {
        Self::trailing_stop_sell(instrument_id, quantity, None, Some(trail_percent))
    }

    /// Checks the intent's invariants (see the table on [`OrderIntent`]).
    pub fn validate(&self) -> Result<(), IntentError> {
        if !(self.quantity.is_finite() && self.quantity > 0.0) {
            return Err(IntentError::NonPositiveQuantity(self.quantity));
        }
        if !matches!(self.side, OrderSide::Buy | OrderSide::Sell) {
            return Err(IntentError::NoSide);
        }
        if [self.price, self.trigger_price, self.trail_amount, self.trail_percent]
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
            OrderType::TrailingStop => (false, false),
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
            (true, false) => return Err(IntentError::MissingTriggerPrice(t)),
            (false, true) => return Err(IntentError::UnexpectedTriggerPrice(t)),
            _ => {}
        }
        if self.order_type == OrderType::TrailingStop {
            match (self.trail_amount.is_some(), self.trail_percent.is_some()) {
                (false, false) => return Err(IntentError::MissingTrail(t)),
                (true, true) => return Err(IntentError::ConflictingTrail(t)),
                _ => {}
            }
            if let Some(amt) = self.trail_amount {
                if !amt.is_finite() || amt <= 0.0 {
                    return Err(IntentError::InvalidTrailAmount(amt));
                }
            }
            if let Some(pct) = self.trail_percent {
                if !pct.is_finite() || !(0.0 < pct && pct < 100.0) {
                    return Err(IntentError::InvalidTrailPercent(pct));
                }
            }
        } else {
            if self.trail_amount.is_some() {
                return Err(IntentError::UnexpectedTrailAmount(t));
            }
            if self.trail_percent.is_some() {
                return Err(IntentError::UnexpectedTrailPercent(t));
            }
        }
        Ok(())
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
    /// use honba_messages::{InstrumentId, OrderId, UnixNanos, Exchange};
    ///
    /// let id = InstrumentId::new("NIFTY50", Exchange::new("NSE"));
    /// let err = OrderIntent::market_buy(id, -1.0)
    ///     .into_order(OrderId::new("O-1"), UnixNanos::from_u64(1))
    ///     .unwrap_err();
    /// assert_eq!(err, IntentError::NonPositiveQuantity(-1.0));
    /// ```
    pub fn into_order(self, order_id: OrderId, ts: UnixNanos) -> Result<Order, IntentError> {
        self.validate()?;
        let mut order = Order::new(
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
        if let Some(trigger) = self.trigger_price {
            order = order.with_trigger_price(trigger);
        }
        if let Some(amt) = self.trail_amount {
            order = order.with_trail_amount(amt);
        }
        if let Some(pct) = self.trail_percent {
            order = order.with_trail_percent(pct);
        }
        Ok(order)
    }
}
