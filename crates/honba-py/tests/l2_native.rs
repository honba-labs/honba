//! Replays every scenario of `schema/conformance/l2_sim.json` through the interpreter-free core
//! of the `honba._honba.L2Simulator` binding (ADR 0030, chunk E3-S1b-2). The Python twin is
//! `python/tests/integration/test_l2_sim_conformance.py`.

use std::path::PathBuf;

use honba::pyclasses::l2::{L2Config, L2OrderIn, NativeL2, QuoteIn, TradeIn};
use honba::pyclasses::next_open::{CostSpec, SimError};
use honba_engine::AlgoError;
use honba_entities::{Currency, Money};
use serde_json::{json, Value};

fn fixture() -> Value {
    let path =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../schema/conformance/l2_sim.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn same(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => x.as_f64() == y.as_f64(),
        (Value::Array(x), Value::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y).all(|(p, q)| same(p, q))
        }
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len() && x.iter().all(|(k, v)| y.get(k).is_some_and(|w| same(v, w)))
        }
        _ => a == b,
    }
}

fn s(step: &Value, k: &str) -> String {
    step[k].as_str().unwrap().to_string()
}

fn quote(step: &Value) -> QuoteIn {
    let f = |k: &str| step[k].as_f64().unwrap();
    QuoteIn {
        symbol: s(step, "symbol"),
        exchange: "NSE".into(),
        ts: step["ts"].as_u64().unwrap(),
        bid: f("bid"),
        ask: f("ask"),
        bid_size: f("bid_size"),
        ask_size: f("ask_size"),
    }
}

fn trade(step: &Value) -> TradeIn {
    TradeIn {
        symbol: s(step, "symbol"),
        exchange: "NSE".into(),
        ts: step["ts"].as_u64().unwrap(),
        price: step["price"].as_f64().unwrap(),
        size: step["size"].as_f64().unwrap(),
        aggressor: step["aggressor"].as_str().unwrap_or("buyer").into(),
    }
}

fn order(step: &Value) -> L2OrderIn {
    L2OrderIn {
        id: s(step, "id"),
        symbol: s(step, "symbol"),
        exchange: "NSE".into(),
        side: s(step, "side"),
        kind: s(step, "type"),
        qty: step["qty"].as_f64().unwrap(),
        price: step["price"].as_f64(),
        trigger: step["trigger"].as_f64(),
        tif: step["tif"].as_str().unwrap_or("day").into(),
        ts: step["ts"].as_u64().unwrap(),
    }
}

/// The vectors' parametric cost model (`flat + (notional * bps + 5000) / 10000`, minor units).
fn test_costs(spec: &Value) -> CostSpec {
    let get = |k: &str| spec[k].as_i64().unwrap_or(0);
    let (fb, fs, bb, bs) = (
        get("flat_buy"),
        get("flat_sell"),
        get("bps_buy"),
        get("bps_sell"),
    );
    CostSpec::Custom(Box::new(move |side, qty, price| {
        let notional = Money::mul_qty(qty, price, Currency::Inr)
            .map_err(|e| AlgoError::Component(e.to_string()))?
            .minor();
        let (flat, bps) = if side == honba_messages::OrderSide::Buy {
            (fb, bb)
        } else {
            (fs, bs)
        };
        Ok(Money::new(
            flat + (notional * bps + 5000).div_euclid(10_000),
            Currency::Inr,
        ))
    }))
}

fn build(config: &Value) -> NativeL2 {
    let mut cfg = L2Config::new(config["cash"].as_i64().unwrap());
    cfg.currency = config["currency"].as_str().unwrap().into();
    cfg.long_only = config["long_only"].as_bool().unwrap();
    for (symbol, lot) in config["lot_sizes"].as_object().unwrap() {
        cfg.lot_sizes
            .push((symbol.clone(), "NSE".into(), lot.as_f64().unwrap()));
    }
    if let Some(costs) = config.get("costs") {
        cfg.costs = test_costs(costs);
    }
    NativeL2::new(cfg).unwrap()
}

fn run(sc: &Value, via_events: bool) {
    let name = sc["name"].as_str().unwrap();
    let mut sim = build(&sc["config"]);
    let mut drains = Vec::<Value>::new();
    for step in sc["steps"].as_array().unwrap() {
        let result: Result<(), SimError> = match step["op"].as_str().unwrap() {
            "quote" => sim.on_quote(&quote(step)),
            "trade" => sim.on_trade(&trade(step)),
            "submit" => sim.submit(&order(step)),
            "cancel" => sim.cancel(step["id"].as_str().unwrap(), step["now"].as_u64().unwrap()),
            "advance_ts" => sim.advance_to(step["ts"].as_u64().unwrap()),
            "session_close" => sim.on_session_close(step["ts"].as_u64().unwrap()),
            "set_position" => sim.set_position(
                step["symbol"].as_str().unwrap(),
                "NSE",
                step["qty"].as_f64().unwrap(),
            ),
            "drain" if via_events => {
                let events = sim.drain_events();
                let fills: Vec<Value> = events
                    .iter()
                    .filter(|e| e.kind == "fill")
                    .map(|f| {
                        json!([f.order_id, f.symbol, f.side, f.quantity, f.price, f.ts, f.costs])
                    })
                    .collect();
                let rej: Vec<Value> = events
                    .iter()
                    .filter(|e| matches!(e.kind, "rejected" | "cancelled" | "expired"))
                    .map(|r| {
                        let reason = r.reason.as_deref().unwrap_or(r.kind);
                        json!([r.order_id, r.symbol, r.side, r.quantity, reason, r.ts])
                    })
                    .collect();
                drains.push(json!({"fills": fills, "rejections": rej}));
                Ok(())
            }
            "drain" => {
                let fills: Vec<Value> = sim
                    .drain_fills()
                    .iter()
                    .map(|f| {
                        json!([f.order_id, f.symbol, f.side, f.quantity, f.price, f.ts, f.costs])
                    })
                    .collect();
                let rej: Vec<Value> = sim
                    .drain_rejections()
                    .iter()
                    .map(|r| json!([r.order_id, r.symbol, r.side, r.quantity, r.reason, r.ts]))
                    .collect();
                drains.push(json!({"fills": fills, "rejections": rej}));
                Ok(())
            }
            other => panic!("{name}: unknown op {other}"),
        };
        let want = step["error"].as_bool().unwrap_or(false);
        assert_eq!(
            result.is_err(),
            want,
            "{name}: step {step}: {:?}",
            result.err()
        );
    }
    let expect = &sc["expect"];
    let drains = Value::Array(drains);
    assert!(
        same(&drains, &expect["drains"]),
        "{name}: drains\n got {drains}\nwant {}",
        expect["drains"]
    );
    let fin = &expect["final"];
    assert_eq!(sim.cash(), fin["cash"].as_i64().unwrap(), "{name}: cash");
    assert_eq!(sim.fees(), fin["fees"].as_i64().unwrap(), "{name}: fees");
    assert_eq!(
        sim.traded_notional(),
        fin["traded_notional"].as_i64().unwrap(),
        "{name}: traded_notional"
    );
    assert_eq!(
        json!(sim.working_orders()),
        fin["working"],
        "{name}: working"
    );
    let mut positions = sim.positions();
    positions.sort_by(|a, b| (&a.0, &a.1).cmp(&(&b.0, &b.1)));
    let got: serde_json::Map<String, Value> = positions
        .iter()
        .map(|(s, _, q)| (s.clone(), json!(q)))
        .collect();
    assert!(
        same(&Value::Object(got), &fin["positions"]),
        "{name}: positions"
    );
}

#[test]
fn every_l2_vector_replays_through_the_pyclass_core() {
    let doc = fixture();
    let scenarios = doc["scenarios"].as_array().unwrap();
    for sc in scenarios {
        run(sc, false);
    }
    assert_eq!(scenarios.len(), 20);
}

#[test]
fn the_event_stream_splits_into_the_same_drains() {
    for sc in fixture()["scenarios"].as_array().unwrap() {
        run(sc, true);
    }
}

#[test]
fn trailing_stop_is_rejected_unsupported_and_bad_input_is_a_value_error() {
    let mut sim = NativeL2::new(L2Config::new(1_000_000)).unwrap();
    let mut o = L2OrderIn {
        id: "t".into(),
        symbol: "AAA".into(),
        exchange: "NSE".into(),
        side: "sell".into(),
        kind: "trailing_stop".into(),
        qty: 1.0,
        price: None,
        trigger: None,
        tif: "day".into(),
        ts: 5,
    };
    sim.submit(&o).unwrap();
    let ev = sim.drain_events();
    assert_eq!(ev.len(), 1);
    assert_eq!(ev[0].kind, "rejected");
    assert_eq!(ev[0].reason.as_deref(), Some("unsupported_order_type"));
    assert!(sim.working_orders().is_empty());

    o.tif = "forever".into();
    assert!(sim.submit(&o).is_err());
    o.tif = "day".into();
    o.kind = "iceberg".into();
    assert!(sim.submit(&o).is_err());

    let crossed = QuoteIn {
        symbol: "AAA".into(),
        exchange: "NSE".into(),
        ts: 6,
        bid: 11.0,
        ask: 10.0,
        bid_size: 1.0,
        ask_size: 1.0,
    };
    assert!(sim.on_quote(&crossed).is_err());
    let neg = QuoteIn {
        bid: 9.0,
        ask_size: -1.0,
        ..crossed
    };
    assert!(sim.on_quote(&neg).is_err());
    let bad = TradeIn {
        symbol: "AAA".into(),
        exchange: "NSE".into(),
        ts: 7,
        price: f64::NAN,
        size: 1.0,
        aggressor: "buyer".into(),
    };
    assert!(sim.on_trade(&bad).is_err());
    assert!(sim.book("AAA", "NSE").is_none());
}
