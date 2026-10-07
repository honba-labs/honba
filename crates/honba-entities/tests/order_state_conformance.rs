//! Cross-language vectors for the order-state machine (ADR 0019 decision 8).
//!
//! Reads `schema/conformance/order_state.json`; the Python test
//! `python/tests/integration/test_order_state_conformance.py` reads the same file. At this
//! level (E2-S6(a)) no engine runs: each step becomes an `ExecutionEvent`, is projected with
//! `order_event()` and applied to `OrderState`. Each op yields at most one wire-event
//! projection (a duplicate no-op yields none), compared to `expect_events`; `expect_state` is
//! compared exactly.

use std::collections::HashMap;
use std::path::PathBuf;

use honba_entities::{Currency, ExecutionEvent, Trade};
use honba_messages::{
    Exchange, IllegalTransition, InstrumentId, OrderId, OrderSide, OrderState, OrderStatus,
    UnixNanos,
};
use serde_json::{json, Value};

const EPS: f64 = 1e-9;

fn fixture() -> Value {
    let path =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../schema/conformance/order_state.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn side(s: &str) -> OrderSide {
    match s {
        "buy" => OrderSide::Buy,
        "sell" => OrderSide::Sell,
        other => panic!("unknown side {other}"),
    }
}

fn error_name(e: &IllegalTransition) -> &'static str {
    match e {
        IllegalTransition::Transition { .. } => "transition",
        IllegalTransition::Overfill { .. } => "overfill",
        IllegalTransition::FillMismatch { .. } => "fill_mismatch",
        IllegalTransition::InvalidQuantity { .. } => "invalid_quantity",
        _ => "other",
    }
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

struct Order {
    state: OrderState,
    instrument: InstrumentId,
    side: OrderSide,
}

#[derive(Default)]
struct Harness {
    orders: HashMap<String, Order>,
    events: Vec<Value>,
}

impl Harness {
    fn step(&mut self, step: &Value) {
        let oid = step["order_id"].as_str().unwrap().to_string();
        let ts = UnixNanos::from_u64(step["ts"].as_u64().unwrap());
        match step["op"].as_str().unwrap() {
            "submit" => {
                let instrument =
                    InstrumentId::new(step["symbol"].as_str().unwrap(), Exchange::new("NSE"));
                let side = side(step["side"].as_str().unwrap());
                let quantity = step["quantity"].as_f64().unwrap();
                self.orders.insert(
                    oid.clone(),
                    Order {
                        state: OrderState::new(),
                        instrument: instrument.clone(),
                        side,
                    },
                );
                let ev = ExecutionEvent::Submitted {
                    order_id: OrderId::new(&oid),
                    instrument_id: instrument,
                    side,
                    quantity,
                    ts,
                };
                self.apply(step, &oid, &ev);
            }
            "cancel" => {
                // Idempotent (ADR 0019 decision 6): unknown, Initialized, terminal or
                // already-requested -> no-op, no event.
                let Some(o) = self.orders.get(&oid) else {
                    return;
                };
                let working = matches!(
                    o.state.status,
                    OrderStatus::Submitted | OrderStatus::Accepted | OrderStatus::PartiallyFilled
                );
                if !working || o.state.cancel_requested {
                    return;
                }
                let ev = ExecutionEvent::CancelRequested {
                    order_id: OrderId::new(&oid),
                    ts,
                };
                self.apply(step, &oid, &ev);
            }
            "venue" => self.venue(step, &oid, ts),
            other => panic!("unsupported op at the state level: {other}"),
        }
    }

    fn venue(&mut self, step: &Value, oid: &str, ts: UnixNanos) {
        let o = &self.orders[oid];
        let (st, instrument_id, side) = (&o.state, o.instrument.clone(), o.side);
        let remainder = st.quantity - st.filled_qty;
        let quantity = if remainder > 0.0 {
            remainder
        } else {
            st.quantity
        };
        let order_id = OrderId::new(oid);
        let venue_order_id = None;
        let ev = match step["event"].as_str().unwrap() {
            "accepted" => ExecutionEvent::Accepted {
                order_id,
                instrument_id,
                side,
                quantity,
                venue_order_id,
                ts,
            },
            "rejected" => ExecutionEvent::Rejected {
                order_id,
                instrument_id,
                side,
                quantity,
                reason: step["reason"].as_str().unwrap_or("venue").to_string(),
                venue_order_id,
                ts,
            },
            "cancelled" => ExecutionEvent::Cancelled {
                order_id,
                instrument_id,
                side,
                quantity,
                venue_order_id,
                ts,
            },
            "expired" => ExecutionEvent::Expired {
                order_id,
                instrument_id,
                side,
                quantity,
                venue_order_id,
                ts,
            },
            "fill" => {
                let last = step["last_qty"].as_f64().unwrap();
                let cum = st.filled_qty + last;
                let trade = Trade::new(
                    order_id,
                    instrument_id,
                    side,
                    last,
                    step["last_px"].as_f64().unwrap(),
                    Currency::Inr,
                    ts,
                    ts,
                );
                ExecutionEvent::Fill {
                    trade,
                    cum_qty: cum,
                    complete: cum + EPS >= st.quantity,
                    venue_order_id,
                }
            }
            other => panic!("unknown venue event {other}"),
        };
        self.apply(step, oid, &ev);
    }

    fn apply(&mut self, step: &Value, oid: &str, ev: &ExecutionEvent) {
        let st = &mut self.orders.get_mut(oid).unwrap().state;
        let released = st.quantity - st.filled_qty;
        let expected_error = step.get("expect_error").and_then(Value::as_str);
        match st.apply(&ev.order_event()) {
            Err(err) => {
                let want = expected_error.unwrap_or_else(|| panic!("unexpected {err}"));
                assert_eq!(error_name(&err), want, "{err}");
            }
            Ok(changed) => {
                assert!(
                    expected_error.is_none(),
                    "expected {expected_error:?}, step was legal"
                );
                if changed {
                    let projected = project(ev, st, released);
                    self.events.push(projected);
                }
            }
        }
    }
}

fn project(ev: &ExecutionEvent, st: &OrderState, released: f64) -> Value {
    let oid = ev.order_id().as_str();
    match ev {
        ExecutionEvent::Submitted { .. } => {
            json!({"type": "order", "order_id": oid, "status": "submitted"})
        }
        ExecutionEvent::Accepted { .. } => json!({"type": "order_accepted", "order_id": oid}),
        ExecutionEvent::CancelRequested { .. } => {
            json!({"type": "order_cancel_requested", "order_id": oid})
        }
        ExecutionEvent::Fill { trade, .. } if st.status == OrderStatus::Filled => {
            json!({"type": "order_filled", "order_id": oid, "last_qty": trade.quantity()})
        }
        ExecutionEvent::Fill { trade, .. } => json!({
            "type": "order_partially_filled", "order_id": oid,
            "last_qty": trade.quantity(), "cum_qty": st.filled_qty
        }),
        ExecutionEvent::Rejected { .. } => {
            json!({"type": "order_rejected", "order_id": oid, "quantity": released})
        }
        ExecutionEvent::Cancelled { .. } => {
            json!({"type": "order_cancelled", "order_id": oid, "quantity": released})
        }
        ExecutionEvent::Expired { .. } => {
            json!({"type": "order_expired", "order_id": oid, "quantity": released})
        }
        _ => panic!("unprojectable event"),
    }
}

fn matches(actual: &Value, expected: &Value) -> bool {
    expected
        .as_object()
        .unwrap()
        .iter()
        .all(|(k, want)| match (actual.get(k), want) {
            (Some(got), Value::Number(_)) => {
                (got.as_f64().unwrap() - want.as_f64().unwrap()).abs() <= EPS
            }
            (Some(got), _) => got == want,
            (None, _) => false,
        })
}

fn run(scenario: &Value) -> Harness {
    let mut h = Harness::default();
    for step in scenario["steps"].as_array().unwrap() {
        h.step(step);
    }
    h
}

#[test]
fn fixture_header_and_required_scenarios() {
    let doc = fixture();
    assert_eq!(doc["fixture_version"], 1);
    assert_eq!(doc["type"], "OrderState");
    let names: Vec<&str> = doc["scenarios"]
        .as_array()
        .unwrap()
        .iter()
        .map(|s| s["name"].as_str().unwrap())
        .collect();
    for required in [
        "partial_fill_sequence",
        "reject_remainder_after_partial",
        "cancel_twice_emits_once",
        "duplicate_terminal_is_noop",
        "ioc_remainder_cancelled",
        "cancel_race",
        "market_fill",
        "limit_fill",
        "reject_at_submit",
        "tif_expiry",
    ] {
        assert!(names.contains(&required), "missing scenario {required}");
    }
    let mut unique = names.clone();
    unique.sort_unstable();
    unique.dedup();
    assert_eq!(unique.len(), names.len(), "duplicate scenario names");
}

#[test]
fn every_scenario_expect_state() {
    for s in fixture()["scenarios"].as_array().unwrap() {
        let h = run(s);
        for (oid, want) in s["expect_state"].as_object().unwrap() {
            let st = &h.orders[oid].state;
            let name = s["name"].as_str().unwrap();
            assert_eq!(
                status_name(st.status),
                want["status"].as_str().unwrap(),
                "{name}"
            );
            assert!(
                (st.filled_qty - want["filled_qty"].as_f64().unwrap()).abs() <= EPS,
                "{name}"
            );
            assert_eq!(
                st.cancel_requested,
                want["cancel_requested"].as_bool().unwrap(),
                "{name}"
            );
        }
    }
}

/// Per-op event projection (not the engine's normalised stream; see the TODO test below).
#[test]
fn every_scenario_expect_events_projection() {
    for s in fixture()["scenarios"].as_array().unwrap() {
        let name = s["name"].as_str().unwrap();
        let h = run(s);
        let want = s["expect_events"].as_array().unwrap();
        assert_eq!(h.events.len(), want.len(), "{name}: {:?}", h.events);
        for (a, e) in h.events.iter().zip(want) {
            assert!(matches(a, e), "{name}: {a} vs {e}");
        }
    }
}

/// TODO(E2-S6(d)): replay these scenarios through every gateway and profile and compare the
/// normalised stream (decision 8). Needs the engines' `drain_events`; out of scope for (a).
#[test]
#[ignore = "TODO(E2-S6(d)): gateway-level normalised-stream replay"]
fn todo_gateway_normalised_stream_replay() {
    unimplemented!("implemented in E2-S6(d)");
}
