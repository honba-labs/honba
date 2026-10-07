//! Unit tests for this crate, one file per area.

mod bar_fill;
mod next_open;
mod next_open_costs;
mod next_open_session_open;
mod next_open_settlement;
mod paper;
mod scripted;

use honba_messages::{
    Bar, BarAggregation, BarSpecification, BarType, Event, Exchange, InstrumentId, Order, OrderId,
    OrderSide, OrderType, PriceType, TimeInForce, UnixNanos,
};

/// A placeholder instrument for tests where the instrument does not matter.
fn any_instrument() -> InstrumentId {
    InstrumentId::new("X", Exchange::new("TEST"))
}

/// A one-minute bar event for [`any_instrument`] closing at `close`, at `ts`.
fn bar_event(close: f64, ts: u64) -> Event {
    let bt = BarType::new(
        any_instrument(),
        BarSpecification::new(1, BarAggregation::Minute, PriceType::Last),
    );
    let t = UnixNanos::from_u64(ts);
    Event::Bar(Bar::new(bt, close, close, close, close, 1.0, t, t))
}

/// A day order for [`any_instrument`] stamped at `ts`.
pub(crate) fn order(
    id: &str,
    side: OrderSide,
    order_type: OrderType,
    qty: f64,
    price: Option<f64>,
    ts: u64,
) -> Order {
    let t = UnixNanos::from_u64(ts);
    Order::new(
        OrderId::new(id),
        any_instrument(),
        side,
        order_type,
        qty,
        price,
        TimeInForce::Day,
        t,
        t,
    )
}

fn market(id: &str, side: OrderSide, qty: f64, ts: u64) -> Order {
    order(id, side, OrderType::Market, qty, None, ts)
}

fn limit(id: &str, side: OrderSide, qty: f64, price: f64, ts: u64) -> Order {
    order(id, side, OrderType::Limit, qty, Some(price), ts)
}

/// A day market order for `symbol` on the `TEST` exchange stamped at `ts`.
fn market_for(id: &str, symbol: &str, side: OrderSide, qty: f64, ts: u64) -> Order {
    let t = UnixNanos::from_u64(ts);
    Order::new(
        OrderId::new(id),
        InstrumentId::new(symbol, Exchange::new("TEST")),
        side,
        OrderType::Market,
        qty,
        None,
        TimeInForce::Day,
        t,
        t,
    )
}
