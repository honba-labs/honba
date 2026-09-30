//! Order intents emitted by strategies.

use honba_messages::{InstrumentId, Order, OrderId, OrderSide, OrderType, TimeInForce, UnixNanos};

/// A strategy's desire to trade, before it becomes a concrete [`Order`].
///
/// Strategies emit intents; the runner converts them into orders with
/// unique ids and timestamps. This indirection keeps strategies free of
/// bookkeeping they shouldn't care about.
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
#[derive(Clone, Debug, PartialEq)]
pub struct OrderIntent {
    /// The instrument to trade.
    pub instrument_id: InstrumentId,
    /// Buy or sell.
    pub side: OrderSide,
    /// Quantity to trade.
    pub quantity: f64,
    /// Execution instruction.
    pub order_type: OrderType,
    /// Limit or stop price, if the order type requires one.
    pub price: Option<f64>,
    /// How long the order remains active.
    pub time_in_force: TimeInForce,
}

impl OrderIntent {
    /// A market buy.
    pub fn market_buy(instrument_id: InstrumentId, quantity: f64) -> Self {
        Self {
            instrument_id,
            side: OrderSide::Buy,
            quantity,
            order_type: OrderType::Market,
            price: None,
            time_in_force: TimeInForce::Day,
        }
    }

    /// A market sell.
    pub fn market_sell(instrument_id: InstrumentId, quantity: f64) -> Self {
        Self {
            instrument_id,
            side: OrderSide::Sell,
            quantity,
            order_type: OrderType::Market,
            price: None,
            time_in_force: TimeInForce::Day,
        }
    }

    /// A limit buy.
    pub fn limit_buy(instrument_id: InstrumentId, quantity: f64, price: f64) -> Self {
        Self {
            instrument_id,
            side: OrderSide::Buy,
            quantity,
            order_type: OrderType::Limit,
            price: Some(price),
            time_in_force: TimeInForce::Day,
        }
    }

    /// A limit sell.
    pub fn limit_sell(instrument_id: InstrumentId, quantity: f64, price: f64) -> Self {
        Self {
            instrument_id,
            side: OrderSide::Sell,
            quantity,
            order_type: OrderType::Limit,
            price: Some(price),
            time_in_force: TimeInForce::Day,
        }
    }

    /// Converts the intent into a concrete order.
    pub fn into_order(self, order_id: OrderId, ts: UnixNanos) -> Order {
        Order::new(
            order_id,
            self.instrument_id,
            self.side,
            self.order_type,
            self.quantity,
            self.price,
            self.time_in_force,
            ts,
            ts,
        )
    }
}
