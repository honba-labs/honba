//! Unit tests for `crate::bar_fill`.

use honba_engine::{ExecutionEngine, Handler};
use honba_entities::Trade;
use honba_messages::{OrderSide, OrderType, UnixNanos};

use super::{any_instrument, bar_event, limit, market, order};
use crate::BarFillEngine;

fn observe(exec: &mut BarFillEngine, close: f64, ts: u64) {
    exec.on_event(&bar_event(close, ts), UnixNanos::from_u64(ts))
        .unwrap();
}

#[test]
fn new_engine_has_no_price_and_no_fills() {
    let mut exec = BarFillEngine::new();
    assert_eq!(exec.last_price(), None);
    assert!(exec.drain_fills().unwrap().is_empty());
}

#[test]
fn tracks_the_latest_bar_close() {
    let mut exec = BarFillEngine::new();
    observe(&mut exec, 100.0, 1);
    observe(&mut exec, 101.5, 2);
    assert_eq!(exec.last_price(), Some(101.5));
}

#[test]
fn market_buy_fills_full_quantity_at_last_close() {
    let mut exec = BarFillEngine::new();
    observe(&mut exec, 101.0, 5);
    exec.submit(market("O-1", OrderSide::Buy, 3.0, 5)).unwrap();

    let fills = exec.drain_fills().unwrap();
    assert_eq!(fills.len(), 1);
    let f = &fills[0];
    assert_eq!(f.order_id().as_str(), "O-1");
    assert_eq!(f.instrument_id(), &any_instrument());
    assert_eq!(f.side(), OrderSide::Buy);
    assert_eq!(f.quantity(), 3.0);
    assert_eq!(f.price(), 101.0);
    assert_eq!(f.ts_event(), UnixNanos::from_u64(5));
}

#[test]
fn market_sell_keeps_its_side() {
    let mut exec = BarFillEngine::new();
    observe(&mut exec, 50.0, 1);
    exec.submit(market("O-1", OrderSide::Sell, 2.0, 1)).unwrap();
    let fills = exec.drain_fills().unwrap();
    assert_eq!(fills[0].side(), OrderSide::Sell);
    assert_eq!(fills[0].notional(), 100.0);
}

#[test]
fn marketable_limit_buy_fills_at_last_close() {
    let mut exec = BarFillEngine::new();
    observe(&mut exec, 101.0, 1);
    exec.submit(limit("O-1", OrderSide::Buy, 1.0, 105.0, 1))
        .unwrap();
    let fills = exec.drain_fills().unwrap();
    assert_eq!(fills.len(), 1);
    assert_eq!(fills[0].price(), 101.0);
}

#[test]
#[ignore = "known gap: ADR 006 (BarFillEngine ignores limit prices)"]
fn non_marketable_limit_buy_is_not_filled() {
    let mut exec = BarFillEngine::new();
    observe(&mut exec, 101.0, 1);
    exec.submit(limit("O-1", OrderSide::Buy, 1.0, 95.0, 1))
        .unwrap();
    assert!(exec.drain_fills().unwrap().is_empty());
}

#[test]
#[ignore = "known gap: ADR 006 (BarFillEngine ignores stop trigger prices)"]
fn stop_buy_is_not_filled_before_its_trigger() {
    let mut exec = BarFillEngine::new();
    observe(&mut exec, 101.0, 1);
    let stop =
        order("O-1", OrderSide::Buy, OrderType::StopMarket, 1.0, None, 1).with_trigger_price(110.0);
    exec.submit(stop).unwrap();
    assert!(exec.drain_fills().unwrap().is_empty());
}

#[test]
#[ignore = "known gap: BarFillEngine has no reject path; before any bar it fills at price 0.0"]
fn order_before_any_bar_is_not_filled_at_zero() {
    let mut exec = BarFillEngine::new();
    exec.submit(market("O-1", OrderSide::Buy, 1.0, 1)).unwrap();
    assert!(exec.drain_fills().unwrap().iter().all(|f| f.price() > 0.0));
}

#[test]
fn fill_timestamps_are_strictly_increasing_and_never_before_the_order() {
    let mut exec = BarFillEngine::new();
    observe(&mut exec, 10.0, 1);
    exec.submit(market("O-1", OrderSide::Buy, 1.0, 7)).unwrap();
    exec.submit(market("O-2", OrderSide::Sell, 1.0, 7)).unwrap();
    exec.submit(market("O-3", OrderSide::Buy, 1.0, 3)).unwrap();
    let ts: Vec<u64> = exec
        .drain_fills()
        .unwrap()
        .iter()
        .map(|f| f.ts_event().as_u64())
        .collect();
    assert_eq!(ts, [7, 8, 9]);
}

#[test]
fn drain_empties_the_fill_buffer() {
    let mut exec = BarFillEngine::new();
    observe(&mut exec, 10.0, 1);
    exec.submit(market("O-1", OrderSide::Buy, 1.0, 1)).unwrap();
    assert_eq!(exec.drain_fills().unwrap().len(), 1);
    assert!(exec.drain_fills().unwrap().is_empty());
}

#[test]
fn clones_share_prices_and_fills() {
    let mut observer = BarFillEngine::new();
    let mut executor = observer.clone();
    observe(&mut observer, 42.0, 1);
    assert_eq!(executor.last_price(), Some(42.0));
    executor
        .submit(market("O-1", OrderSide::Buy, 1.0, 1))
        .unwrap();
    assert_eq!(observer.drain_fills().unwrap().len(), 1);
    assert!(executor.drain_fills().unwrap().is_empty());
}

#[test]
fn cancel_is_a_no_op() {
    let mut exec = BarFillEngine::new();
    observe(&mut exec, 10.0, 1);
    exec.submit(market("O-1", OrderSide::Buy, 1.0, 1)).unwrap();
    exec.cancel("O-1").unwrap();
    assert_eq!(exec.drain_fills().unwrap().len(), 1);
}

#[test]
fn identical_inputs_give_identical_fills() {
    fn run() -> Vec<Trade> {
        let mut exec = BarFillEngine::new();
        for (i, close) in [100.0, 101.0, 99.5, 102.0].into_iter().enumerate() {
            let ts = i as u64 + 1;
            observe(&mut exec, close, ts);
            let side = if i % 2 == 0 {
                OrderSide::Buy
            } else {
                OrderSide::Sell
            };
            exec.submit(market(&format!("O-{ts}"), side, 1.0, ts))
                .unwrap();
        }
        exec.drain_fills().unwrap()
    }
    let a = run();
    assert_eq!(a.len(), 4);
    assert_eq!(a, run());
}
