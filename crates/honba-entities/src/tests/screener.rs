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
