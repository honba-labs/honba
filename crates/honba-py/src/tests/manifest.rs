//! Unit tests for `crate::pyclasses::manifest`.

use crate::pyclasses::manifest::verify;

const VALID: &str = r#"{
    "api_version": "1.0.0", "name": "sma", "source_hash": "h",
    "universe": {"named": "NIFTY50"},
    "subscriptions": {"instruments": [{"symbol": "TCS", "exchange": "NSE"}], "trades": true},
    "driving_timeframe": {"interval": 5, "aggregation": "minute"},
    "warmup_bars": 3
}"#;

#[test]
fn a_valid_manifest_compiles_to_ir_json() {
    let ir: serde_json::Value = serde_json::from_str(&verify(VALID).unwrap()).unwrap();
    assert_eq!(ir["universe"]["named"], "NIFTY50");
    assert_eq!(ir["subscriptions"]["trades"][0]["symbol"], "TCS");
    assert_eq!(ir["warmup_bars"], 3);
}

#[test]
fn failures_carry_a_code() {
    assert_eq!(verify("{}").unwrap_err().code, "deserialize");
    let empty = VALID.replace(r#""name": "sma""#, r#""name": """#);
    assert_eq!(verify(&empty).unwrap_err().code, "empty_name");
}
