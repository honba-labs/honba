//! Unit tests for `crate::round_trip`.

use honba_entities::{PositionSide, Trade};
use honba_messages::{InstrumentId, OrderId, OrderSide, UnixNanos, Venue};

use super::{any_instrument, assert_close, long_trip};
use crate::{AnalyticsError, RoundTrip};

fn fill(side: OrderSide, qty: f64, price: f64, ts: u64) -> Trade {
    Trade::new(
        OrderId::new(format!("O-{ts}")),
        any_instrument(),
        side,
        qty,
        price,
        UnixNanos::from_u64(ts),
        UnixNanos::from_u64(ts),
    )
}

#[test]
fn new_long_computes_gross_net_and_pct() {
    let t = long_trip(100.0, 110.0, 2.0, 5.0);
    assert_close(t.gross_pnl, 20.0);
    assert_close(t.net_pnl, 15.0);
    assert_close(t.pnl_pct, 15.0 / 200.0);
}

#[test]
fn new_short_profits_when_price_falls() {
    let t = RoundTrip::new(
        any_instrument(),
        PositionSide::Short,
        3.0,
        50.0,
        40.0,
        UnixNanos::from_u64(1),
        UnixNanos::from_u64(2),
        0.0,
    );
    assert_close(t.gross_pnl, 30.0);
    assert_close(t.net_pnl, 30.0);
}

#[test]
fn zero_entry_notional_gives_zero_pct() {
    assert_eq!(long_trip(0.0, 10.0, 1.0, 0.0).pnl_pct, 0.0);
}

#[test]
fn duration_saturates_at_zero_when_exit_precedes_entry() {
    let mut t = long_trip(1.0, 2.0, 1.0, 0.0);
    assert_eq!(t.duration_nanos(), 1);
    t.exit_ts = UnixNanos::from_u64(0);
    assert_eq!(t.duration_nanos(), 0);
}

#[test]
fn from_fills_sums_fees_and_takes_entry_metadata() {
    let entry = fill(OrderSide::Buy, 2.0, 100.0, 10);
    let exit = fill(OrderSide::Sell, 2.0, 105.0, 20);
    let t = RoundTrip::from_fills(&entry, &exit, 1.0, 2.0).unwrap();
    assert_eq!(t.side, PositionSide::Long);
    assert_eq!(t.instrument_id, any_instrument());
    assert_eq!((t.entry_ts.as_u64(), t.exit_ts.as_u64()), (10, 20));
    assert_close(t.fees, 3.0);
    assert_close(t.gross_pnl, 10.0);
    assert_close(t.net_pnl, 7.0);
}

#[test]
fn from_fills_rejects_same_side_pairs() {
    let a = fill(OrderSide::Buy, 1.0, 100.0, 1);
    let b = fill(OrderSide::Buy, 1.0, 101.0, 2);
    assert!(matches!(
        RoundTrip::from_fills(&a, &b, 0.0, 0.0),
        Err(AnalyticsError::TradeMismatch(_))
    ));
}

#[test]
fn from_fills_rejects_different_instruments() {
    let entry = fill(OrderSide::Buy, 1.0, 100.0, 1);
    let exit = Trade::new(
        OrderId::new("O-2"),
        InstrumentId::new("Y", Venue::new("NSE")),
        OrderSide::Sell,
        1.0,
        101.0,
        UnixNanos::from_u64(2),
        UnixNanos::from_u64(2),
    );
    let err = RoundTrip::from_fills(&entry, &exit, 0.0, 0.0).unwrap_err();
    assert!(err.to_string().contains("different instruments"), "{err}");
}
