//! The shared `ExecutionEngine` contract, run against every engine in this crate.
//!
//! Contract (ADR 008, decision 13): fills belong to submitted orders, and once
//! the working orders are cancelled `filled + released == ordered` for every
//! order; draining twice never repeats an item; cancelling a finished or
//! unknown order is a no-op.

use std::collections::HashMap;

use honba_engine::{ExecutionEngine, Handler};
use honba_messages::{
    Bar, BarAggregation, BarSpecification, BarType, Event, Exchange, InstrumentId, Order, OrderId,
    OrderSide, OrderType, PriceType, TimeInForce, UnixNanos,
};
use honba_sim::{BarFillEngine, Behavior, PaperExecution, ScriptedExecution};

fn order(id: &str, side: OrderSide, qty: f64, ts: u64) -> Order {
    let t = UnixNanos::from_u64(ts);
    Order::new(
        OrderId::new(id),
        InstrumentId::new("X", Exchange::new("TEST")),
        side,
        OrderType::Market,
        qty,
        None,
        TimeInForce::Day,
        t,
        t,
    )
}

fn check_execution_contract(engine: &mut dyn ExecutionEngine, ids: &[&str]) {
    let mut ordered: HashMap<String, f64> = HashMap::new();
    for (i, id) in ids.iter().enumerate() {
        let qty = 10.0 + i as f64;
        ordered.insert((*id).to_string(), qty);
        engine
            .submit(order(id, OrderSide::Buy, qty, i as u64 + 1))
            .unwrap();
    }
    for id in ids {
        engine.cancel(id).unwrap();
    }
    engine.cancel("unknown-order").unwrap();

    let fills = engine.drain_fills().unwrap();
    let rejections = engine.drain_rejections().unwrap();
    let mut settled: HashMap<String, f64> = HashMap::new();
    for f in &fills {
        assert!(ordered.contains_key(f.order_id().as_str()), "stray fill");
        *settled
            .entry(f.order_id().as_str().to_string())
            .or_default() += f.quantity();
    }
    for r in &rejections {
        assert!(ordered.contains_key(r.order_id.as_str()), "stray rejection");
        assert!(r.quantity > 0.0 && !r.reason.is_empty());
        *settled.entry(r.order_id.as_str().to_string()).or_default() += r.quantity;
    }
    for (id, qty) in &ordered {
        assert_eq!(
            settled.get(id),
            Some(qty),
            "filled + released != ordered for {id}"
        );
    }
    assert!(engine.drain_fills().unwrap().is_empty());
    assert!(engine.drain_rejections().unwrap().is_empty());
}

#[test]
fn bar_fill_engine_honours_the_contract() {
    // A fill needs an observed price: show the engine one bar first.
    let mut engine = BarFillEngine::new();
    let bt = BarType::new(
        InstrumentId::new("X", Exchange::new("TEST")),
        BarSpecification::new(1, BarAggregation::Minute, PriceType::Last),
    );
    let t = UnixNanos::from_u64(1);
    let bar = Bar::new(bt, 10.0, 10.0, 10.0, 10.0, 1.0, t, t);
    engine.on_event(&Event::Bar(bar), t).unwrap();
    check_execution_contract(&mut engine, &["a", "b"]);
}

#[test]
fn paper_execution_honours_the_contract() {
    check_execution_contract(&mut PaperExecution::new(10.0), &["a", "b"]);
}

#[test]
fn scripted_execution_honours_the_contract() {
    let mut engine = ScriptedExecution::new(10.0)
        .with("a", Behavior::Hold)
        .with("b", Behavior::partial(4.0, "insufficient_funds"))
        .with("c", Behavior::reject("no_position"))
        .with("d", Behavior::Fill);
    check_execution_contract(&mut engine, &["a", "b", "c", "d"]);
}
