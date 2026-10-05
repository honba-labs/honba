//! Unit tests for `crate::round_trip`.

use honba_entities::{Currency, Money, PositionSide, Trade};
use honba_messages::{Exchange, InstrumentId, OrderId, OrderSide, UnixNanos};

use super::{any_instrument, assert_close, long_trip};
use crate::{AnalyticsError, RoundTrip};

fn fill(side: OrderSide, qty: f64, price: f64, ts: u64) -> Trade {
    Trade::new(
        OrderId::new(format!("O-{ts}")),
        any_instrument(),
        side,
        qty,
        price,
        Currency::Inr,
        UnixNanos::from_u64(ts),
        UnixNanos::from_u64(ts),
    )
}

fn fill_with_costs(side: OrderSide, qty: f64, price: f64, ts: u64, costs: f64) -> Trade {
    fill(side, qty, price, ts).with_costs(Money::from_major_f64(costs, Currency::Inr).unwrap())
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
    // Fees are read off the trades now: 1.00 + 2.00 = 3.00.
    let entry = fill_with_costs(OrderSide::Buy, 2.0, 100.0, 10, 1.0);
    let exit = fill_with_costs(OrderSide::Sell, 2.0, 105.0, 20, 2.0);
    let t = RoundTrip::from_fills(&entry, &exit).unwrap();
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
        RoundTrip::from_fills(&a, &b),
        Err(AnalyticsError::TradeMismatch(_))
    ));
}

#[test]
fn from_fills_rejects_different_instruments() {
    let entry = fill(OrderSide::Buy, 1.0, 100.0, 1);
    let exit = Trade::new(
        OrderId::new("O-2"),
        InstrumentId::new("Y", Exchange::new("NSE")),
        OrderSide::Sell,
        1.0,
        101.0,
        Currency::Inr,
        UnixNanos::from_u64(2),
        UnixNanos::from_u64(2),
    );
    let err = RoundTrip::from_fills(&entry, &exit).unwrap_err();
    assert!(err.to_string().contains("different instruments"), "{err}");
}
