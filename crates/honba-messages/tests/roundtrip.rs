//! End-to-end construction and accessor tests.

use honba_messages::{
    AggressorSide, Bar, BarAggregation, BarSpecification, BarType, Event, InstrumentId, Message,
    Order, OrderId, OrderSide, OrderStatus, OrderType, PriceType, QuoteTick, Tick, TimeInForce,
    TradeId, TradeTick, UnixNanos, Venue,
};

fn nse(sym: &str) -> InstrumentId {
    InstrumentId::new(sym, Venue::new("NSE"))
}

fn ts(n: u64) -> UnixNanos {
    UnixNanos::from_u64(n)
}

#[test]
fn quote_tick_roundtrip() {
    let q = QuoteTick::new(nse("NIFTY50"), 22_000.0, 22_001.0, 50.0, 75.0, ts(1), ts(1));
    assert_eq!(q.bid_price(), 22_000.0);
    assert_eq!(q.ask_price(), 22_001.0);
    assert_eq!(q.mid_price(), 22_000.5);
}

#[test]
fn trade_tick_roundtrip() {
    let t = TradeTick::new(
        nse("RELIANCE"),
        2_950.5,
        100.0,
        AggressorSide::Seller,
        TradeId::new("T-42"),
        ts(2),
        ts(2),
    );
    assert_eq!(t.price(), 2_950.5);
    assert_eq!(t.size(), 100.0);
    assert_eq!(t.aggressor_side(), AggressorSide::Seller);
    assert_eq!(t.trade_id().as_str(), "T-42");
}

#[test]
fn tick_union_reports_ts() {
    let q = Tick::Quote(QuoteTick::new(nse("X"), 1.0, 2.0, 1.0, 1.0, ts(10), ts(10)));
    let t = Tick::Trade(TradeTick::new(
        nse("X"),
        1.5,
        1.0,
        AggressorSide::Buyer,
        TradeId::new("t"),
        ts(20),
        ts(20),
    ));
    assert_eq!(q.ts_event().as_u64(), 10);
    assert_eq!(t.ts_event().as_u64(), 20);
}

#[test]
fn bar_construction() {
    let bt = BarType::new(
        nse("BANKNIFTY"),
        BarSpecification::new(5, BarAggregation::Minute, PriceType::Last),
    );
    let bar = Bar::new(
        bt,
        48_000.0,
        48_200.0,
        47_900.0,
        48_150.0,
        12_345.0,
        ts(60),
        ts(60),
    );
    assert_eq!(bar.high(), 48_200.0);
    assert_eq!(bar.low(), 47_900.0);
    assert_eq!(bar.bar_type().spec().step(), 5);
    assert_eq!(bar.bar_type().spec().aggregation(), BarAggregation::Minute);
}

#[test]
fn order_lifecycle() {
    let o = Order::new(
        OrderId::new("O-1"),
        nse("NIFTY50"),
        OrderSide::Buy,
        OrderType::Limit,
        75.0,
        Some(22_000.0),
        TimeInForce::Day,
        ts(1),
        ts(1),
    );
    assert_eq!(o.status(), OrderStatus::Initialized);

    let o = o.with_status(OrderStatus::Accepted);
    assert_eq!(o.status(), OrderStatus::Accepted);
}

#[test]
fn event_ts_matches_source() {
    let q = QuoteTick::new(nse("X"), 1.0, 2.0, 1.0, 1.0, ts(99), ts(99));
    let ev = Event::Quote(q.clone());
    assert_eq!(ev.ts_event().as_u64(), 99);
    assert!(ev.is_market_data());

    let order_ev = Event::OrderAccepted {
        order_id: "O-1".into(),
        ts_event: ts(150),
    };
    assert_eq!(order_ev.ts_event().as_u64(), 150);
    assert!(!order_ev.is_market_data());
}

#[test]
fn message_wraps_event() {
    let q = QuoteTick::new(nse("X"), 1.0, 2.0, 1.0, 1.0, ts(1), ts(1));
    let msg = Message::new(Event::Quote(q), ts(2));
    assert_eq!(msg.ts_init().as_u64(), 2);
    assert!(msg.event().is_market_data());
}

#[test]
fn display_impls() {
    let id = nse("NIFTY50");
    assert_eq!(id.to_string(), "NIFTY50.NSE");
    assert_eq!(Venue::new("BSE").to_string(), "BSE");
    assert_eq!(UnixNanos::from_u64(42).to_string(), "42");
}
