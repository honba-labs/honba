//! `LedgerContext`: the reference `StrategyContext` (ADR 008). Mirrors
//! `python/tests/unit/test_ledger_context.py`.

use honba_entities::{Currency, Instrument, InstrumentKind, Trade};
use honba_messages::{InstrumentId, OrderSide, UnixNanos, Venue};

use crate::{LedgerContext, OrderIntent, StrategyContext};

fn id(symbol: &str, venue: &str) -> InstrumentId {
    InstrumentId::new(symbol, Venue::new(venue))
}

fn fill(instrument_id: &InstrumentId, side: OrderSide, qty: f64, px: f64, costs: f64) -> Trade {
    Trade::new(
        "O-1".into(),
        instrument_id.clone(),
        side,
        qty,
        px,
        UnixNanos::from_u64(1),
        UnixNanos::from_u64(1),
    )
    .with_costs(costs)
}

#[test]
fn starts_empty() {
    let ctx = LedgerContext::new();
    let nifty = id("NIFTY50", "NSE");
    assert_eq!(ctx.now(), UnixNanos::from_u64(0));
    assert_eq!(ctx.cash(), 0.0);
    assert!(ctx.positions().is_empty());
    assert_eq!(ctx.position(&nifty), 0.0);
    assert!(!ctx.busy(&nifty));
    assert!(ctx.instrument(&nifty).is_none());
}

#[test]
fn clock_is_set_by_the_runner() {
    let mut ctx = LedgerContext::new();
    ctx.set_now(UnixNanos::from_u64(1_700_000_000_000_000_000));
    assert_eq!(ctx.now(), UnixNanos::from_u64(1_700_000_000_000_000_000));
}

#[test]
fn instrument_lookup() {
    let nifty = id("NIFTY50", "NSE");
    let meta = Instrument::new(
        nifty.clone(),
        InstrumentKind::Index,
        Currency::Inr,
        75.0,
        0.05,
    );
    let mut ctx = LedgerContext::new();
    ctx.add_instrument(meta.clone());
    assert_eq!(ctx.instrument(&nifty), Some(&meta));
}

#[test]
fn submit_queues_in_order_and_marks_busy_until_filled() {
    let (nifty, infy) = (id("NIFTY50", "NSE"), id("INFY", "NSE"));
    let mut ctx = LedgerContext::new();
    let first = OrderIntent::market_buy(nifty.clone(), 10.0);
    let second = OrderIntent::limit_sell(infy.clone(), 2.0, 1500.0);
    ctx.submit(first.clone());
    ctx.submit(second.clone());
    assert!(ctx.busy(&nifty) && ctx.busy(&infy));
    assert_eq!(ctx.drain_intents(), vec![first, second]);
    assert!(ctx.drain_intents().is_empty());
    assert!(ctx.busy(&nifty), "drained is not filled");
    ctx.apply_fill(&fill(&nifty, OrderSide::Buy, 4.0, 100.0, 0.0));
    assert!(ctx.busy(&nifty), "partial fill");
    ctx.apply_fill(&fill(&nifty, OrderSide::Buy, 6.0, 100.0, 0.0));
    assert!(!ctx.busy(&nifty));
}

#[test]
fn release_clears_pending_for_a_rejected_intent() {
    let nifty = id("NIFTY50", "NSE");
    let mut ctx = LedgerContext::new();
    let intent = OrderIntent::market_sell(nifty.clone(), 5.0);
    ctx.submit(intent.clone());
    ctx.release(&intent);
    assert!(!ctx.busy(&nifty));
}

#[test]
fn fills_move_position_and_cash_including_costs() {
    let nifty = id("NIFTY50", "NSE");
    let mut ctx = LedgerContext::with_cash(1_000.0);
    ctx.apply_fill(&fill(&nifty, OrderSide::Buy, 3.0, 100.0, 1.5));
    assert_eq!(ctx.position(&nifty), 3.0);
    assert_eq!(ctx.cash(), 1_000.0 - (3.0 * 100.0 + 1.5));
    ctx.apply_fill(&fill(&nifty, OrderSide::Sell, 5.0, 110.0, 2.0));
    assert_eq!(ctx.position(&nifty), -2.0, "signed: short 2");
    assert_eq!(ctx.cash(), 698.5 + (5.0 * 110.0 - 2.0));
}

#[test]
fn positions_lists_non_flat_ordered_by_symbol_then_venue() {
    let (nifty, infy) = (id("NIFTY50", "NSE"), id("INFY", "NSE"));
    let (acme_bse, acme_nse) = (id("ACME", "BSE"), id("ACME", "NSE"));
    let mut ctx = LedgerContext::new();
    for i in [&nifty, &acme_nse, &infy, &acme_bse] {
        ctx.apply_fill(&fill(i, OrderSide::Buy, 1.0, 10.0, 0.0));
    }
    ctx.apply_fill(&fill(&infy, OrderSide::Sell, 1.0, 10.0, 0.0));
    assert_eq!(
        ctx.positions(),
        vec![(acme_bse, 1.0), (acme_nse, 1.0), (nifty, 1.0)]
    );
}
