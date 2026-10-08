//! Cross-language golden vectors for the risk stage (ADR 0018 decision 11).
//!
//! Reads `schema/conformance/risk_decisions.json`; the Python runner
//! (`python/tests/integration/test_risk_conformance.py`) reads the same file. Each case replays
//! its `requests` in order on one fresh [`RiskStage`] (the rate rule has memory) and compares,
//! per request, the decision, the wire code and the whole `context`. Integers are compared
//! exactly, floats within `1e-9`.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Arc;

use honba_entities::Currency;
use honba_market::{InstrumentRules, PriceBand};
use honba_messages::{Exchange, InstrumentId, OrderId, OrderSide, TradingState, UnixNanos};
use honba_risk::{
    OrderRateLimit, RiskCheck, RiskDecision, RiskLimits, RiskRequest, RiskStage, RulesSource,
};
use serde_json::{json, Value};

const EPS: f64 = 1e-9;

fn fixture() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../schema/conformance/risk_decisions.json");
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("cannot read fixture {}: {e}", path.display()));
    serde_json::from_str(&text).expect("fixture is valid JSON")
}

fn instrument_id(s: &str) -> InstrumentId {
    let (symbol, exchange) = s
        .rsplit_once('.')
        .expect("instrument id is SYMBOL.EXCHANGE");
    InstrumentId::new(symbol, Exchange::new(exchange))
}

fn f(v: &Value, key: &str) -> f64 {
    v[key]
        .as_f64()
        .unwrap_or_else(|| panic!("missing number `{key}` in {v}"))
}

fn opt_f(v: &Value, key: &str) -> Option<f64> {
    match v.get(key) {
        None | Some(Value::Null) => None,
        Some(n) => Some(n.as_f64().unwrap_or_else(|| panic!("`{key}` not a number"))),
    }
}

struct FixtureRules(BTreeMap<InstrumentId, (InstrumentRules, Option<PriceBand>)>);

impl FixtureRules {
    fn from_fixture(instruments: &Value) -> Self {
        let mut map = BTreeMap::new();
        for (name, spec) in instruments.as_object().expect("instruments is an object") {
            let mut rules = InstrumentRules::new(f(spec, "lot_size"), f(spec, "tick_size"));
            if let Some(min) = opt_f(spec, "min_order_quantity") {
                rules.min_order_quantity = min;
            }
            rules.max_order_quantity = opt_f(spec, "max_order_quantity");
            let band = spec
                .get("band")
                .filter(|b| !b.is_null())
                .map(|b| PriceBand::new(f(b, "lower"), f(b, "upper")));
            map.insert(instrument_id(name), (rules, band));
        }
        Self(map)
    }
}

impl RulesSource for FixtureRules {
    fn rules(&self, id: &InstrumentId) -> Option<(InstrumentRules, Option<PriceBand>)> {
        self.0.get(id).cloned()
    }
}

fn limits(v: &Value) -> RiskLimits {
    RiskLimits {
        max_notional: opt_f(v, "max_notional"),
        order_rate: v
            .get("order_rate")
            .filter(|r| !r.is_null())
            .map(|r| OrderRateLimit {
                max_orders: u32::try_from(r["max_orders"].as_u64().expect("max_orders"))
                    .expect("max_orders fits u32"),
                window_ms: r["window_ms"].as_u64().expect("window_ms"),
            }),
        max_participation: opt_f(v, "max_participation"),
    }
}

fn side(s: &str) -> OrderSide {
    match s {
        "buy" => OrderSide::Buy,
        "sell" => OrderSide::Sell,
        other => panic!("unknown side {other}"),
    }
}

fn state(s: &str) -> TradingState {
    match s {
        "active" => TradingState::Active,
        "reducing" => TradingState::Reducing,
        "halted" => TradingState::Halted,
        other => panic!("unknown trading_state {other}"),
    }
}

fn request(v: &Value) -> RiskRequest {
    RiskRequest {
        order_id: OrderId::new(v["order_id"].as_str().expect("order_id")),
        instrument_id: instrument_id(v["instrument_id"].as_str().expect("instrument_id")),
        side: side(v["side"].as_str().expect("side")),
        quantity: f(v, "quantity"),
        price: opt_f(v, "price"),
        trigger_price: opt_f(v, "trigger_price"),
        reference_price: opt_f(v, "reference_price"),
        adv: opt_f(v, "adv"),
        position: f(v, "position"),
        trading_state: state(v["trading_state"].as_str().expect("trading_state")),
        ts: UnixNanos::new(v["ts"].as_u64().expect("ts is an integer of nanoseconds")),
    }
}

/// Integers exact, floats within `EPS`, everything else structurally equal.
fn same(expected: &Value, actual: &Value) -> bool {
    match (expected, actual) {
        (Value::Number(e), Value::Number(a)) => {
            if e.is_f64() || a.is_f64() {
                e.is_f64() && a.is_f64() && (e.as_f64().unwrap() - a.as_f64().unwrap()).abs() <= EPS
            } else {
                e == a
            }
        }
        (Value::Object(e), Value::Object(a)) => {
            e.len() == a.len() && e.iter().all(|(k, v)| a.get(k).is_some_and(|w| same(v, w)))
        }
        (Value::Array(e), Value::Array(a)) => {
            e.len() == a.len() && e.iter().zip(a).all(|(x, y)| same(x, y))
        }
        _ => expected == actual,
    }
}

fn observed(d: &RiskDecision) -> Value {
    match d {
        RiskDecision::Approved => json!({ "decision": "approved" }),
        RiskDecision::Refused(r) => json!({
            "decision": "refused",
            "code": r.error_code().as_str(),
            "context": r.context(),
        }),
    }
}

#[test]
fn risk_conformance() {
    let fx = fixture();
    assert_eq!(fx["fixture_version"], 1);
    assert_eq!(fx["type"], "RiskDecision");
    let currency: Currency =
        serde_json::from_value(fx["currency"].clone()).expect("known currency code");
    let rules: Arc<dyn RulesSource> = Arc::new(FixtureRules::from_fixture(&fx["instruments"]));

    let cases = fx["cases"].as_array().expect("cases");
    assert!(!cases.is_empty(), "fixture has no cases");
    let mut failures = Vec::new();
    for case in cases {
        let name = case["name"].as_str().expect("case name");
        let mut stage = RiskStage::new(limits(&case["limits"]), currency, Arc::clone(&rules))
            .unwrap_or_else(|e| panic!("{name}: invalid limits: {e}"));
        let requests = case["requests"].as_array().expect("requests");
        let expects = case["expect"].as_array().expect("expect");
        assert_eq!(
            requests.len(),
            expects.len(),
            "{name}: requests/expect length"
        );
        for (i, (req, expect)) in requests.iter().zip(expects).enumerate() {
            let got = observed(&stage.check(&request(req)));
            if !same(expect, &got) {
                failures.push(format!(
                    "{name}[{i}]\n  expected {expect}\n  actual   {got}"
                ));
            }
        }
    }
    assert!(
        failures.is_empty(),
        "{} mismatches:\n{}",
        failures.len(),
        failures.join("\n")
    );
}
