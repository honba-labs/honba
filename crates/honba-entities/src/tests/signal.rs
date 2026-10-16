//! Unit tests for the signal contract (ADR 0031).

use std::collections::BTreeMap;

use honba_messages::{Exchange, InstrumentId, UnixNanos};

use crate::screener::Timeframe;
use crate::signal::{
    Reason, ReasonGroup, ReasonPolarity, Signal, SignalDirection, SignalError, SignalHorizon,
    SignalScanRequest, SignalScanResponse,
};

fn reason(code: &str, polarity: ReasonPolarity) -> Reason {
    Reason {
        code: code.to_string(),
        group: ReasonGroup::Trend,
        polarity,
        weight: 0.4,
        label: "test".to_string(),
        detail: "detail".to_string(),
    }
}

fn buy_signal() -> Signal {
    Signal {
        instrument: InstrumentId::new("RELIANCE", Exchange::new("NSE")),
        timeframe: "5m".to_string(),
        horizon: SignalHorizon::Intraday,
        t: UnixNanos::from_u64(1_700_000_000_000_000_000),
        direction: SignalDirection::Buy,
        strength: 1.0,
        anchor_price: 2_500.0,
        ruleset: SignalHorizon::Intraday.ruleset().to_string(),
        reasons: vec![reason("ema_cross_up", ReasonPolarity::Bullish)],
        tags: vec!["ema_cross".to_string()],
        evidence: BTreeMap::new(),
    }
}

#[test]
fn horizon_ruleset_ids_are_frozen() {
    assert_eq!(SignalHorizon::Intraday.ruleset(), "signals/day:v1");
    assert_eq!(SignalHorizon::Swing.ruleset(), "signals/swing:v1");
}

#[test]
fn polarity_maps_to_direction() {
    assert_eq!(ReasonPolarity::Bullish.direction(), SignalDirection::Buy);
    assert_eq!(ReasonPolarity::Bearish.direction(), SignalDirection::Sell);
}

#[test]
fn a_valid_signal_passes_validation() {
    let signal = buy_signal();
    assert_eq!(signal.validate(), Ok(()));
    assert_eq!(signal.reason_codes(), vec!["ema_cross_up"]);
}

#[test]
fn a_signal_without_reasons_is_rejected() {
    let mut signal = buy_signal();
    signal.reasons.clear();
    assert_eq!(signal.validate(), Err(SignalError::NoReasons));
}

#[test]
fn strength_outside_zero_to_one_is_rejected() {
    let mut signal = buy_signal();
    signal.strength = 1.5;
    assert_eq!(
        signal.validate(),
        Err(SignalError::OutOfRange {
            field: "strength",
            value: 1.5
        })
    );
}

#[test]
fn signal_serialization_roundtrips() {
    let signal = buy_signal();
    let json = serde_json::to_string(&signal).unwrap();
    assert!(json.contains("\"direction\":\"buy\""));
    assert!(json.contains("\"horizon\":\"intraday\""));
    assert!(json.contains("\"polarity\":\"bullish\""));
    let back: Signal = serde_json::from_str(&json).unwrap();
    assert_eq!(signal, back);
}

#[test]
fn omits_empty_evidence() {
    let json = serde_json::to_string(&buy_signal()).unwrap();
    assert!(!json.contains("evidence"));
}

#[test]
fn scan_request_rejects_unknown_fields() {
    let ok = serde_json::from_value::<SignalScanRequest>(serde_json::json!({
        "universe": ["NSE:RELIANCE"],
        "timeframe": "1D",
        "horizon": "swing"
    }));
    assert!(ok.is_ok());

    let bad = serde_json::from_value::<SignalScanRequest>(serde_json::json!({
        "universe": ["NSE:RELIANCE"],
        "timeframe": "1D",
        "horizon": "swing",
        "unexpected": 1
    }));
    assert!(bad.is_err());
}

#[test]
fn scan_response_is_an_open_record() {
    let response = SignalScanResponse {
        total: 1,
        signals: vec![buy_signal()],
    };
    let json = serde_json::to_string(&response).unwrap();
    let back: SignalScanResponse = serde_json::from_str(&json).unwrap();
    assert_eq!(response, back);
}

#[test]
fn timeframe_is_reused_from_the_screener_contract() {
    let request: SignalScanRequest = serde_json::from_value(serde_json::json!({
        "universe": [],
        "timeframe": "5",
        "horizon": "intraday",
        "minStrength": 0.5
    }))
    .unwrap();
    assert_eq!(request.timeframe, Timeframe::M5);
    assert_eq!(request.min_strength, 0.5);
}
