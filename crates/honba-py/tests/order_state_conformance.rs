//! ``NativeSim`` (``honba._honba.NextOpenSimulator``) replays the
//! order-state conformance vectors under its declared profile (ADR 0019).
//!
//! Only the bar-realisable ``market_fill`` scenario is realisable by the
//! next-open simulator (its L1 price-driven sim fills market orders at the
//! next open).

use std::collections::HashMap;
use std::path::PathBuf;

use honba::pyclasses::next_open::{BarIn, NativeSim, OrderIn, SimConfig};
use honba_messages::{OrderEvent, OrderState, OrderStatus};
use serde_json::{json, Value};

const EPS: f64 = 1e-9;

fn fixture() -> Value {
    let path =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../schema/conformance/order_state.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn status_name(s: OrderStatus) -> &'static str {
    match s {
        OrderStatus::Initialized => "initialized",
        OrderStatus::Submitted => "submitted",
        OrderStatus::Accepted => "accepted",
        OrderStatus::PartiallyFilled => "partially_filled",
        OrderStatus::Filled => "filled",
        OrderStatus::Cancelled => "cancelled",
        OrderStatus::Rejected => "rejected",
        OrderStatus::Expired => "expired",
        _ => "other",
    }
}

fn normalise(events: &[Value], profile: &str) -> Vec<Value> {
    events
        .iter()
        .filter(|e| !(profile == "l1" && e["type"] == "order_accepted"))
        .cloned()
        .collect()
}

fn same(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => {
            (x.as_f64().unwrap() - y.as_f64().unwrap()).abs() <= EPS
        }
        _ => a == b,
    }
}

#[test]
fn native_next_open_replays_market_fill_under_l1() {
    let doc = fixture();
    let scenario = doc["scenarios"]
        .as_array()
        .unwrap()
        .iter()
        .find(|v| v["name"] == "market_fill")
        .unwrap();

    let symbol = "X";
    let mut sim = NativeSim::new(SimConfig::new(100_000_000)).unwrap();

    let mut states: HashMap<String, OrderState> = HashMap::new();
    let mut projected: Vec<Value> = Vec::new();

    let bar = |ts: u64, price: f64| BarIn {
        symbol: symbol.into(),
        exchange: "NSE".into(),
        ts,
        open: price,
        high: price,
        low: price,
        close: price,
        volume: 1000.0,
    };

    for step in scenario["steps"].as_array().unwrap() {
        let op = step["op"].as_str().unwrap();
        match op {
            "submit" => {
                let oid = step["order_id"].as_str().unwrap().to_string();
                let qty = step["quantity"].as_f64().unwrap();
                let ts = step["ts"].as_u64().unwrap();
                let mut st = OrderState::new();
                assert!(st.apply(&OrderEvent::Submitted { quantity: qty }).unwrap());
                states.insert(oid.clone(), st);
                projected.push(json!({"type": "order", "order_id": oid, "status": "submitted"}));
                sim.open_session(ts, &[bar(ts, 10.0)]).unwrap();
                let order = OrderIn {
                    id: oid,
                    symbol: symbol.into(),
                    exchange: "NSE".into(),
                    side: step["side"].as_str().unwrap().into(),
                    kind: step["order_type"].as_str().unwrap().into(),
                    qty,
                    price: step.get("price").and_then(|v| v.as_f64()),
                    trigger: None,
                    ts,
                };
                sim.submit(&order).unwrap();
            }
            "venue" => {
                let last_qty = step["last_qty"].as_f64().unwrap();
                let last_px = step["last_px"].as_f64().unwrap();
                let ts = step["ts"].as_u64().unwrap() + 1;
                sim.open_session(ts, &[bar(ts, last_px)]).unwrap();
                for out in sim.drain_events() {
                    // market_fill only yields a single complete fill
                    assert_eq!(out.kind, "fill", "market_fill drain: unexpected {:?}", out);
                    assert!((out.quantity - last_qty).abs() <= EPS, "fill quantity");
                    let st = states.get_mut(out.order_id.as_str()).unwrap();
                    let complete = out.complete.unwrap_or(true);
                    let changed = st
                        .apply(&OrderEvent::Fill {
                            last_qty: out.quantity,
                            complete,
                        })
                        .unwrap();
                    if changed {
                        if st.status == OrderStatus::Filled {
                            projected.push(json!({"type": "order_filled", "order_id": out.order_id, "last_qty": out.quantity}));
                        } else {
                            projected.push(json!({"type": "order_partially_filled", "order_id": out.order_id, "last_qty": out.quantity, "cum_qty": st.filled_qty}));
                        }
                    }
                }
            }
            "cancel" => unreachable!("market_fill has no cancel"),
            other => panic!("unknown op {other}"),
        }
    }

    let want = normalise(scenario["expect_events"].as_array().unwrap(), "l1");
    let got = normalise(&projected, "l1");

    assert_eq!(
        want.len(),
        got.len(),
        "event count:\ngot  {got:?}\nwant {want:?}"
    );
    for (a, e) in got.iter().zip(want.iter()) {
        for (k, want_v) in e.as_object().unwrap() {
            let got_v = a.get(k).unwrap_or(&Value::Null);
            assert!(
                same(got_v, want_v),
                "event key {k}: got {got_v:?} want {want_v:?}"
            );
        }
    }

    for (oid, want_state) in scenario["expect_state"].as_object().unwrap() {
        let st: &OrderState = &states[oid];
        assert_eq!(
            status_name(st.status),
            want_state["status"].as_str().unwrap(),
            "{oid}"
        );
        assert!(
            (st.filled_qty - want_state["filled_qty"].as_f64().unwrap()).abs() <= EPS,
            "{oid}"
        );
        assert_eq!(
            st.cancel_requested,
            want_state["cancel_requested"].as_bool().unwrap(),
            "{oid}"
        );
    }
}
