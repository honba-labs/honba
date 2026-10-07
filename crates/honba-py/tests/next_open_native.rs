//! Replays every chunk 1 and 2 vector of `schema/conformance/next_open_sim.json` through the
//! interpreter-free core of the `honba._honba.NextOpenSimulator` binding (ADR 0016, chunk 3a).
//! The Python twin is `python/tests/integration/test_next_open_sim_native.py`.

use std::path::PathBuf;

use honba::pyclasses::next_open::{
    india_fill_cost, BarIn, CostSpec, NativeSim, OrderIn, SimConfig, SimError,
};
use honba_engine::AlgoError;
use honba_entities::{Currency, Money};
use serde_json::{json, Value};

fn fixture() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../schema/conformance/next_open_sim.json");
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

fn bar(step: &Value) -> BarIn {
    let px = |k: &str| step[k].as_f64().unwrap();
    BarIn {
        symbol: step["symbol"].as_str().unwrap().into(),
        exchange: "NSE".into(),
        ts: step["ts"].as_u64().unwrap(),
        open: px("open"),
        high: px("high"),
        low: px("low"),
        close: px("close"),
        volume: step["volume"].as_f64().unwrap_or(1000.0),
    }
}

fn bars(step: &Value) -> Vec<BarIn> {
    step["bars"].as_array().unwrap().iter().map(bar).collect()
}

fn order(step: &Value) -> OrderIn {
    OrderIn {
        id: step["id"].as_str().unwrap().into(),
        symbol: step["symbol"].as_str().unwrap().into(),
        exchange: "NSE".into(),
        side: step["side"].as_str().unwrap().into(),
        kind: step["type"].as_str().unwrap().into(),
        qty: step["qty"].as_f64().unwrap(),
        price: step["price"].as_f64(),
        trigger: step["trigger"].as_f64(),
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

fn build(config: &Value) -> NativeSim {
    let mut cfg = SimConfig::new(config["cash"].as_i64().unwrap());
    cfg.currency = config["currency"].as_str().unwrap().into();
    cfg.settlement_days = config["settlement_days"].as_i64().unwrap();
    cfg.long_only = config["long_only"].as_bool().unwrap();
    for (symbol, lot) in config["lot_sizes"].as_object().unwrap() {
        cfg.lot_sizes
            .push((symbol.clone(), "NSE".into(), lot.as_f64().unwrap()));
    }
    if let Some(costs) = config.get("costs") {
        cfg.costs = test_costs(costs);
    }
    NativeSim::new(cfg).unwrap()
}

fn run(sc: &Value, via_events: bool) {
    let name = sc["name"].as_str().unwrap();
    let mut sim = build(&sc["config"]);
    let (mut drains, mut probes) = (Vec::<Value>::new(), Vec::<Value>::new());
    for step in sc["steps"].as_array().unwrap() {
        let result: Result<(), SimError> = match step["op"].as_str().unwrap() {
            "bar" => sim.on_bar(&bar(step)),
            "open_session" => sim.open_session(step["ts"].as_u64().unwrap(), &bars(step)),
            "session_open" => sim.on_session_open(step["ts"].as_u64().unwrap(), &bars(step)),
            "set_settlement_days" => sim.set_settlement_days(step["days"].as_i64().unwrap()),
            "probe" => {
                probes.push(
                    json!({"unsettled": sim.unsettled(), "available_cash": sim.available_cash()}),
                );
                Ok(())
            }
            "submit" => sim.submit(&order(step)),
            "cancel" => sim.cancel(step["id"].as_str().unwrap(), step["now"].as_u64().unwrap()),
            "drain" if via_events => {
                // The ordered stream, split back into the legacy fill / rejection views.
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
        "{name}"
    );
    assert_eq!(
        json!(sim.working_orders()),
        fin["working"],
        "{name}: working"
    );
    if fin.get("unsettled").is_some() {
        assert_eq!(
            sim.unsettled(),
            fin["unsettled"].as_i64().unwrap(),
            "{name}: unsettled"
        );
        assert_eq!(
            sim.available_cash(),
            fin["available_cash"].as_i64().unwrap(),
            "{name}"
        );
    }
    if let Some(want) = fin.get("probes") {
        assert_eq!(&Value::Array(probes), want, "{name}: probes");
    }
    let mut positions = sim.positions();
    positions.sort_by(|a, b| (&a.0, &a.1).cmp(&(&b.0, &b.1)));
    let got: serde_json::Map<String, Value> = positions
        .iter()
        .map(|(s, _, q)| (s.clone(), json!(q)))
        .collect();
    assert_eq!(Value::Object(got), fin["positions"], "{name}: positions");
}

#[test]
fn every_chunk_one_and_two_vector_matches_the_python_reference() {
    let doc = fixture();
    let mut replayed = 0;
    for sc in doc["scenarios"].as_array().unwrap() {
        if matches!(sc["chunk"].as_u64().unwrap(), 1 | 2) {
            run(sc, false);
            replayed += 1;
        }
    }
    assert_eq!(replayed, 52);
}

#[test]
fn the_event_stream_splits_into_the_same_52_vector_drains() {
    // `drain_events` is the one drain; its fill and rejection views equal the legacy drains.
    let doc = fixture();
    let mut replayed = 0;
    for sc in doc["scenarios"].as_array().unwrap() {
        if matches!(sc["chunk"].as_u64().unwrap(), 1 | 2) {
            run(sc, true);
            replayed += 1;
        }
    }
    assert_eq!(replayed, 52);
}

#[test]
fn the_named_india_packs_charge_each_leg_rounded_to_minor_units() {
    // 10 @ 2950.0 delivery buy: 885 + 0 + 88 + 3 + 3 + 443 + 176 paise (Python reference: 1598).
    assert_eq!(
        india_fill_cost("india.equity.delivery", "buy", 10.0, 2950.0).unwrap(),
        1598
    );
}
