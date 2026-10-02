//! Unit tests for screener models.

use crate::screener::{FilterOp, MetricKeySpec, MetricPeriod, ScreenerFilterPredicate, Timeframe};

#[test]
fn metric_key_spec_serialization_roundtrip() {
    let spec = MetricKeySpec {
        key: "RSI".to_string(),
        period: Some(MetricPeriod::Snapshot),
        timeframe: Some(Timeframe::D1),
    };
    let json = serde_json::to_string(&spec).unwrap();
    let deserialized: MetricKeySpec = serde_json::from_str(&json).unwrap();
    assert_eq!(spec, deserialized);
}

#[test]
fn filter_predicate_serialization() {
    let pred = ScreenerFilterPredicate {
        key: "market_cap_basic".to_string(),
        op: FilterOp::Gte,
        value: serde_json::json!(10000000000u64),
        period: None,
        timeframe: None,
    };
    let json = serde_json::to_string(&pred).unwrap();
    assert!(json.contains("\"op\":\"gte\""));
    assert!(json.contains("\"key\":\"market_cap_basic\""));
}

// --- MetricRef and the predicate value contract -------------------------------

use crate::screener::MetricRef;
use crate::EntitiesError;
use serde_json::{json, Value};

fn predicate(op: &str, value: Value) -> Result<ScreenerFilterPredicate, serde_json::Error> {
    serde_json::from_value(json!({"key": "SMA50", "op": op, "value": value}))
}

#[test]
fn metric_ref_serializes_key_only_when_dimensions_absent() {
    let r = MetricRef::new("SMA200");
    assert_eq!(serde_json::to_value(&r).unwrap(), json!({"key": "SMA200"}));
}

#[test]
fn metric_ref_round_trips_period_and_timeframe() {
    let wire = json!({"key": "price_earnings_ttm", "period": "TTM", "timeframe": "1W"});
    let r: MetricRef = serde_json::from_value(wire.clone()).unwrap();
    assert_eq!(r.period, Some(MetricPeriod::Ttm));
    assert_eq!(r.timeframe, Some(Timeframe::W1));
    assert_eq!(serde_json::to_value(&r).unwrap(), wire);
}

#[test]
fn metric_ref_rejects_missing_key_and_unknown_fields() {
    assert!(serde_json::from_value::<MetricRef>(json!({})).is_err());
    assert!(serde_json::from_value::<MetricRef>(json!({"key": 5})).is_err());
    assert!(serde_json::from_value::<MetricRef>(json!({"key": "A", "extra": 1})).is_err());
}

#[test]
fn metric_ref_operand_is_accepted_for_comparison_ops() {
    for op in ["crosses_above", "crosses_below", "gt", "gte", "lt", "lte"] {
        let p = predicate(op, json!({"key": "SMA200", "timeframe": "1D"}))
            .unwrap_or_else(|e| panic!("{op}: {e}"));
        let want = MetricRef {
            key: "SMA200".into(),
            period: None,
            timeframe: Some(Timeframe::D1),
        };
        assert_eq!(p.metric_ref(), Some(want), "{op}");
    }
}

#[test]
fn numeric_threshold_is_accepted_for_comparison_ops() {
    for op in ["crosses_above", "crosses_below", "gt", "gte", "lt", "lte"] {
        for v in [json!(0), json!(50), json!(-1.5), json!(10_000_000_000u64)] {
            let p = predicate(op, v.clone()).unwrap_or_else(|e| panic!("{op} {v}: {e}"));
            assert_eq!(p.value, v);
            assert_eq!(p.metric_ref(), None);
        }
    }
}

#[test]
fn ordering_ops_still_accept_string_scalars() {
    for op in ["gt", "gte", "lt", "lte"] {
        assert!(predicate(op, json!("2024-01-01")).is_ok(), "{op}");
    }
}

#[test]
fn between_and_set_ops_accept_lists() {
    assert!(predicate("between", json!([10, 20.5])).is_ok());
    for op in ["in", "not_in"] {
        for v in [json!([]), json!(["NSE", "BSE"]), json!([1, 2, 3])] {
            assert!(predicate(op, v.clone()).is_ok(), "{op} {v}");
        }
    }
}

#[test]
fn unconstrained_ops_keep_any_value() {
    for op in ["eq", "neq", "like", "has"] {
        for v in [
            json!("NSE"),
            json!(1),
            json!(true),
            Value::Null,
            json!(["a"]),
        ] {
            assert!(predicate(op, v.clone()).is_ok(), "{op} {v}");
        }
    }
}

#[test]
fn value_contract_violations_are_rejected() {
    let cases = [
        ("crosses_above", json!("200")),
        ("crosses_above", json!(true)),
        ("crosses_above", Value::Null),
        ("crosses_above", json!([1, 2])),
        ("crosses_below", json!({"key": "SMA200", "bogus": 1})),
        ("crosses_below", json!({"period": "TTM"})),
        ("gt", Value::Null),
        ("gt", json!(true)),
        ("gt", json!([1])),
        ("gte", json!({"key": 200})),
        ("between", json!([1])),
        ("between", json!([1, 2, 3])),
        ("between", json!(5)),
        ("between", json!([1, "2"])),
        ("between", json!([1, true])),
        ("between", json!({"key": "SMA200"})),
        ("in", json!("NSE")),
        ("in", json!({"key": "SMA200"})),
        ("not_in", json!(5)),
    ];
    for (op, v) in cases {
        assert!(predicate(op, v.clone()).is_err(), "{op} {v} was accepted");
    }
}

#[test]
fn checked_constructor_enforces_the_contract() {
    let ok = ScreenerFilterPredicate::new(
        "SMA50",
        FilterOp::CrossesAbove,
        serde_json::to_value(MetricRef::new("SMA200")).unwrap(),
    )
    .unwrap();
    assert_eq!(
        serde_json::to_value(&ok).unwrap(),
        json!({"key": "SMA50", "op": "crosses_above", "value": {"key": "SMA200"}})
    );
    let err = ScreenerFilterPredicate::new("SMA50", FilterOp::Between, json!([1])).unwrap_err();
    assert!(matches!(err, EntitiesError::InvalidPredicate(_)), "{err:?}");
}

#[test]
fn predicate_rejects_unknown_fields_like_python() {
    let v = json!({"key": "SMA50", "op": "gt", "value": 1, "extra": true});
    assert!(serde_json::from_value::<ScreenerFilterPredicate>(v).is_err());
}
