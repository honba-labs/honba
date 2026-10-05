//! The `StrategyContext` port is object-safe and usable through `&mut dyn`.

use honba_entities::{Currency, Instrument, Money};
use honba_messages::{InstrumentId, UnixNanos};

use super::any_instrument;
use crate::{OrderIntent, StrategyContext};

/// A hand-written context: proves the trait can be implemented outside the
/// reference ledger and used as a trait object, as a live runner would.
#[derive(Default)]
struct FixedContext {
    submitted: Vec<OrderIntent>,
}

impl StrategyContext for FixedContext {
    fn now(&self) -> UnixNanos {
        UnixNanos::from_u64(42)
    }
    fn position(&self, _instrument_id: &InstrumentId) -> f64 {
        3.0
    }
    fn positions(&self) -> Vec<(InstrumentId, f64)> {
        vec![(any_instrument(), 3.0)]
    }
    fn cash(&self) -> Money {
        Money::new(10_000, Currency::Inr)
    }
    fn busy(&self, _instrument_id: &InstrumentId) -> bool {
        !self.submitted.is_empty()
    }
    fn instrument(&self, _instrument_id: &InstrumentId) -> Option<&Instrument> {
        None
    }
    fn submit(&mut self, intent: OrderIntent) {
        self.submitted.push(intent);
    }
}

fn buy_one(ctx: &mut dyn StrategyContext) {
    let id = any_instrument();
    if !ctx.busy(&id) && ctx.instrument(&id).is_none() {
        ctx.submit(OrderIntent::market_buy(id, 1.0));
    }
}

#[test]
fn a_custom_context_works_as_a_trait_object() {
    let mut ctx = FixedContext::default();
    buy_one(&mut ctx);
    buy_one(&mut ctx); // busy now: no second intent
    assert_eq!(ctx.submitted.len(), 1);
    let view: &dyn StrategyContext = &ctx;
    assert_eq!(view.now(), UnixNanos::from_u64(42));
    assert_eq!(view.position(&any_instrument()), 3.0);
    assert_eq!(view.positions(), vec![(any_instrument(), 3.0)]);
    assert_eq!(view.cash().minor(), 10000);
}
