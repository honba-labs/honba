//! Edge cases of the rejection queue through the real runner flow
//! (strategy -> runner -> scripted engine -> fills / rejections -> context).
//!
//! Vectors for the common paths live in `order_rejections.rs`.

use honba_engine::{Handler, Result};
use honba_entities::Trade;
use honba_messages::{Bar, UnixNanos};
use honba_sim::{Behavior, ScriptedExecution};
use honba_strategy::{OrderIntent, Strategy, StrategyContext, StrategyRunner};
use honba_testing::fixtures::instrument;
use honba_testing::VecFeed;

/// Buys the listed quantities of `X` on the first bar; on a fill of the first
/// order it submits `follow_up` more (if any).
struct Edge {
    first_bar: Vec<f64>,
    follow_up: Option<f64>,
    sent: bool,
}

impl Edge {
    fn new(first_bar: &[f64], follow_up: Option<f64>) -> Self {
        Self {
            first_bar: first_bar.to_vec(),
            follow_up,
            sent: false,
        }
    }
}

impl Strategy for Edge {
    fn name(&self) -> &str {
        "edge"
    }
    fn on_bar(&mut self, ctx: &mut dyn StrategyContext, _bar: &Bar) -> Result<()> {
        if !std::mem::replace(&mut self.sent, true) {
            for q in &self.first_bar {
                ctx.submit(OrderIntent::market_buy(instrument("X"), *q));
            }
        }
        Ok(())
    }
    fn on_fill(&mut self, ctx: &mut dyn StrategyContext, _fill: &Trade) -> Result<()> {
        if let Some(q) = self.follow_up.take() {
            ctx.submit(OrderIntent::market_buy(instrument("X"), q));
        }
        Ok(())
    }
}

fn bar(r: &mut impl Handler, ts: u64) {
    let event = VecFeed::bar("X", 10.0, ts).event().clone();
    r.on_event(&event, UnixNanos::from_u64(ts)).unwrap();
}

#[test]
fn cancelling_a_held_order_is_final_and_a_later_bar_changes_nothing() {
    let exec = ScriptedExecution::new(10.0).with("edge-0", Behavior::Hold);
    let mut r = StrategyRunner::new(Edge::new(&[4.0], None), exec);
    bar(&mut r, 1);
    assert!(r.context().busy(&instrument("X")));

    r.cancel("edge-0").unwrap();
    r.cancel("edge-0").unwrap(); // a second cancel is a no-op
    bar(&mut r, 2);
    assert!(!r.context().busy(&instrument("X")));
    assert_eq!(r.order_rejections().len(), 1, "released exactly once");
    assert!(r.order_rejections()[0].cancelled);
    assert!(r.fills().is_empty());
    assert_eq!(r.context().position(&instrument("X")), 0.0);
}

#[test]
fn rejecting_one_of_two_orders_on_an_instrument_keeps_the_other_pending() {
    let exec = ScriptedExecution::new(10.0)
        .with("edge-0", Behavior::reject("insufficient_funds"))
        .with("edge-1", Behavior::Hold);
    let mut r = StrategyRunner::new(Edge::new(&[3.0, 2.0], None), exec);
    bar(&mut r, 1);
    assert_eq!(r.order_rejections().len(), 1);
    assert_eq!(r.order_rejections()[0].quantity, 3.0);
    assert!(
        r.context().busy(&instrument("X")),
        "the 2 still working must stay pending"
    );

    r.cancel("edge-1").unwrap();
    assert!(!r.context().busy(&instrument("X")));
    assert_eq!(r.order_rejections().len(), 2);
}

#[test]
fn a_rejection_in_the_same_event_as_a_fill_that_submits_a_follow_up() {
    let exec = ScriptedExecution::new(10.0).with("edge-1", Behavior::reject("insufficient_funds"));
    let mut r = StrategyRunner::new(Edge::new(&[2.0, 3.0], Some(1.0)), exec);
    bar(&mut r, 1);
    // edge-0 filled, edge-1 rejected (3 released); the follow-up (1) is queued
    // and counts as pending until the next event submits it.
    assert_eq!(r.fills().len(), 1);
    assert_eq!(r.order_rejections().len(), 1);
    assert!(r.context().busy(&instrument("X")), "follow-up pending");

    bar(&mut r, 2);
    let ids: Vec<&str> = r.fills().iter().map(|f| f.order_id().as_str()).collect();
    assert_eq!(ids, ["edge-0", "edge-2"]);
    assert!(!r.context().busy(&instrument("X")));
    assert_eq!(r.context().position(&instrument("X")), 3.0);
    assert_eq!(r.order_rejections().len(), 1);
}
