//! Unit tests for `crate::pyclasses::risk` (ADR 0018 decision 10): JSON in, JSON out.

use honba_messages::{Exchange, InstrumentId, TradingState};
use honba_risk::{OrderRateLimit, RiskCheck, RiskDecision};
use serde_json::{json, Value};

use crate::pyclasses::risk::*;

fn instrument(extra: Value) -> Value {
    let mut v = json!({
        "instrument_id": {"symbol": "X", "exchange": "NSE"},
        "kind": "equity", "currency": "INR", "lot_size": 1.0, "tick_size": 0.05
    });
    for (k, val) in extra.as_object().unwrap() {
        v[k] = val.clone();
    }
    v
}

fn request(extra: Value) -> Value {
    let mut v = json!({
        "order_id": "A", "instrument_id": "X.NSE", "side": "buy", "quantity": 1.0,
        "price": 100.0, "position": 0.0, "trading_state": "active", "ts": 1
    });
    for (k, val) in extra.as_object().unwrap() {
        v[k] = val.clone();
    }
    v
}

fn x() -> InstrumentId {
    InstrumentId::new("X", Exchange::new("NSE"))
}

#[test]
fn limits_parse_the_toml_table_shape() {
    assert_eq!(parse_limits(&json!({})).unwrap(), Default::default());
    let l = parse_limits(&json!({
        "max_notional": 500000.0, "order_rate": {"max_orders": 30, "window_ms": 1000}
    }))
    .unwrap();
    assert_eq!(l.max_notional, Some(500_000.0));
    assert_eq!(
        l.order_rate,
        Some(OrderRateLimit {
            max_orders: 30,
            window_ms: 1000
        })
    );
}

#[test]
fn limits_refuse_unknown_keys_and_invalid_values() {
    for bad in [
        json!({"max_notionl": 1.0}),
        json!({"order_rate": {"max_orders": 1, "window_ms": 1, "burst": 2}}),
        json!({"max_notional": 0.0}),
        json!({"max_notional": -2.0}),
        json!({"order_rate": {"max_orders": 0, "window_ms": 1}}),
        json!({"order_rate": {"max_orders": 1, "window_ms": 0}}),
    ] {
        let e = parse_limits(&bad).unwrap_err();
        assert!(e.starts_with("invalid risk limits"), "{bad}: {e}");
    }
}

#[test]
fn request_omits_optional_keys() {
    let r = parse_request(&request(json!({}))).unwrap();
    assert_eq!(r.instrument_id, x());
    assert_eq!((r.trigger_price, r.reference_price), (None, None));
    assert_eq!(r.price, Some(100.0));
    assert_eq!(r.ts.as_u64(), 1);
    let r = parse_request(&request(
        json!({"trigger_price": 99.0, "reference_price": 98.0}),
    ))
    .unwrap();
    assert_eq!(
        (r.trigger_price, r.reference_price),
        (Some(99.0), Some(98.0))
    );
}

#[test]
fn request_errors_name_the_problem() {
    for (patch, needle) in [
        (json!({"side": "hold"}), "side"),
        (json!({"trading_state": "paused"}), "trading_state"),
        (json!({"instrument_id": "NOEXCHANGE"}), "instrument_id"),
        (json!({"ts": -1}), "ts"),
        (json!({"ts": 1.5}), "ts"),
        (json!({"quantity": "many"}), "quantity"),
        (json!({"leverage": 5}), "leverage"),
    ] {
        let e = parse_request(&request(patch.clone())).unwrap_err();
        assert!(e.starts_with("invalid risk request"), "{patch}: {e}");
        assert!(e.contains(needle), "{patch}: {e}");
    }
    let mut missing = request(json!({}));
    missing.as_object_mut().unwrap().remove("position");
    assert!(parse_request(&missing).unwrap_err().contains("position"));
}

#[test]
fn stage_applies_instrument_overrides_over_the_profile() {
    let extra = json!({"max_order_quantity": 10.0, "band": {"lower": 90.0, "upper": 110.0}});
    let mut stage = build_stage(Default::default(), "INR", "null", &[instrument(extra)]).unwrap();
    let freeze = stage.check(&parse_request(&request(json!({"quantity": 11.0}))).unwrap());
    assert_eq!(decision_json(&freeze)["code"], "risk_quantity_over_freeze");
    let band = stage.check(&parse_request(&request(json!({"price": 111.0}))).unwrap());
    assert_eq!(decision_json(&band)["code"], "risk_price_band_exceeded");
    let ok = stage.check(&parse_request(&request(json!({}))).unwrap());
    assert_eq!(ok, RiskDecision::Approved);
}

#[test]
fn stage_construction_errors() {
    let e = build_stage(Default::default(), "INR", "mars", &[])
        .err()
        .expect("an error");
    assert!(e.contains("market") && e.contains("mars"), "{e}");
    let e = build_stage(Default::default(), "XXX", "null", &[])
        .err()
        .expect("an error");
    assert!(e.contains("currency"), "{e}");
    let e = build_stage(
        Default::default(),
        "INR",
        "null",
        &[instrument(json!({"band": {"lower": 2.0, "upper": 1.0}}))],
    )
    .err()
    .expect("an error");
    assert!(e.contains("band"), "{e}");
    let e = build_stage(
        Default::default(),
        "INR",
        "null",
        &[instrument(json!({"max_order_quantity": -1.0}))],
    )
    .err()
    .expect("an error");
    assert!(e.contains("max_order_quantity"), "{e}");
    let e = build_stage(
        Default::default(),
        "INR",
        "null",
        &[instrument(json!({"lot_size": 0.0}))],
    )
    .err()
    .expect("an error");
    assert!(e.contains("lot_size"), "{e}");
}

#[test]
fn decision_json_shape() {
    assert_eq!(
        decision_json(&RiskDecision::Approved),
        json!({"approved": true, "code": null, "rule": null, "context": {}})
    );
    let mut stage =
        build_stage(Default::default(), "INR", "null", &[instrument(json!({}))]).unwrap();
    let d = stage.check(&parse_request(&request(json!({"trading_state": "halted"}))).unwrap());
    assert_eq!(
        decision_json(&d),
        json!({
            "approved": false, "code": "risk_trading_halted", "rule": "trading_halted",
            "context": {"rule": "trading_halted"}
        })
    );
}

#[test]
fn run_risk_parses_all_fields() {
    let spec = json!({
        "limits": {"max_notional": 10.0},
        "positions": [{"instrument_id": {"symbol": "X", "exchange": "NSE"}, "quantity": 100.0}],
        "trading_state": "reducing",
        "state_changes": [{"ts_init": 2000, "state": "halted"}],
        "market": "null"
    });
    let r = parse_run_risk(&spec.to_string()).unwrap();
    assert_eq!(r.limits.max_notional, Some(10.0));
    assert_eq!(r.positions, vec![(x(), 100.0)]);
    assert_eq!(r.trading_state, TradingState::Reducing);
    assert_eq!(r.state_changes, vec![(2000, TradingState::Halted)]);
    assert_eq!(r.market, "null");

    let d = parse_run_risk("{}").unwrap();
    assert_eq!(d.trading_state, TradingState::Active);
    assert_eq!(d.market, "null");
    assert!(d.positions.is_empty() && d.state_changes.is_empty());
}

#[test]
fn run_risk_refuses_bad_specs() {
    for bad in [
        json!({"surprise": 1}),
        json!({"limits": {"bogus": 1}}),
        json!({"trading_state": "paused"}),
        json!({"state_changes": [{"ts_init": 1, "state": "paused"}]}),
        json!({"state_changes": [{"ts_init": 2, "state": "halted"}, {"ts_init": 1, "state": "active"}]}),
        json!({"positions": [{"instrument_id": {"symbol": "X", "exchange": "NSE"}}]}),
        json!({"positions": [{"instrument_id": {"symbol": "X", "exchange": "NSE"}, "quantity": "a"}]}),
    ] {
        let e = parse_run_risk(&bad.to_string()).unwrap_err();
        assert!(e.starts_with("invalid risk"), "{bad}: {e}");
    }
    assert!(parse_run_risk("not json")
        .unwrap_err()
        .starts_with("invalid risk"));
}

#[test]
fn limits_refuse_a_non_table_order_rate() {
    let e = parse_limits(&json!({"order_rate": [1, 2]})).unwrap_err();
    assert!(e.contains("order_rate must be a table"), "{e}");
}
