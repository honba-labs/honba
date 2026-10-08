//! Unit tests for `crate::pyclasses::run`.

use honba_entities::{Currency, InstrumentKind};
use serde_json::{json, Value};

use crate::pyclasses::run::*;

fn ts_obj(nanos: u64) -> Value {
    let iso = match nanos {
        0 => "1970-01-01T00:00:00.000000000Z",
        1 => "1970-01-01T00:00:00.000000001Z",
        2 => "1970-01-01T00:00:00.000000002Z",
        _ => "2023-11-14T22:14:20.000000000Z",
    };
    json!({"iso": iso, "unix_nanos": nanos.to_string()})
}

fn bar(close: f64, ts: u64) -> Value {
    json!({
        "schema_version": 4,
        "event": {
            "type": "bar",
            "bar_type": {
                "instrument_id": {"symbol": "X", "exchange": "NSE"},
                "spec": {"step": 1, "aggregation": "minute", "price_type": "last"}
            },
            "open": close, "high": close, "low": close, "close": close, "volume": 1.0,
            "ts_event": ts_obj(ts), "ts_init": ts_obj(ts)
        },
        "ts_init": ts_obj(ts)
    })
}

#[test]
fn parses_instrument_metadata() {
    let raw = json!({
        "instrument_id": {"symbol": "NIFTY50", "exchange": "NSE"},
        "kind": "mutual_fund", "currency": "INR", "lot_size": 75.0, "tick_size": 0.05
    });
    let inst = parse_instrument(&raw).unwrap();
    assert_eq!(inst.kind(), InstrumentKind::MutualFund);
    assert_eq!(inst.currency(), Currency::Inr);
    assert_eq!((inst.lot_size(), inst.tick_size()), (75.0, 0.05));

    let mut bad = raw.clone();
    bad["kind"] = json!("crypto");
    assert!(parse_instrument(&bad).unwrap_err().contains("kind"));
    let mut bad = raw;
    bad["lot_size"] = json!(0.0);
    assert!(parse_instrument(&bad).unwrap_err().contains("lot_size"));
}

#[test]
fn runs_buy_and_hold_and_reports_json() {
    let events = json!([bar(10.0, 1), bar(11.0, 2)]).to_string();
    let params = r#"{"instrument_id": {"symbol": "X", "exchange": "NSE"}, "quantity": 2.0}"#;
    let out: Value = serde_json::from_str(
        &run_strategy_json("buy_and_hold", params, &events, "[]", 100.0).unwrap(),
    )
    .unwrap();
    assert_eq!(out["intents"][0]["ts_init"]["unix_nanos"], "1");
    assert_eq!(out["intents"][0]["intent"]["side"], "buy");
    assert_eq!(out["fills"][0]["order_id"], "buy_and_hold-0");
    assert_eq!(out["fills"][0]["price"], 10.0);
    assert_eq!(
        out["positions"],
        json!([{"instrument_id": {"symbol": "X", "exchange": "NSE"}, "quantity": 2.0}])
    );
    assert_eq!(out["cash"]["amount"], 8000);
    assert_eq!(out["observations"], json!([]));
}

#[test]
fn rejects_bad_input() {
    let params = r#"{"instrument_id": {"symbol": "X", "exchange": "NSE"}, "quantity": 1.0}"#;
    let err = run_strategy_json("nope", params, "[]", "[]", 0.0).unwrap_err();
    assert!(err.contains("unknown strategy"), "{err}");
    assert!(run_strategy_json("buy_and_hold", "{}", "[]", "[]", 0.0).is_err());
    assert!(run_strategy_json("buy_and_hold", params, "not json", "[]", 0.0).is_err());
    assert!(run_strategy_json("buy_and_hold", params, "[]", "{}", 0.0).is_err());
    let sma = r#"{"instrument_id": {"symbol": "X", "exchange": "NSE"}, "fast": 5, "slow": 2, "quantity": 1.0}"#;
    let err = run_strategy_json("sma_crossover", sma, "[]", "[]", 0.0).unwrap_err();
    assert!(err.contains("fast"), "{err}");
}

#[test]
fn refuses_an_order_before_any_bar_instead_of_filling_at_zero() {
    let quote = json!({
        "schema_version": 4,
        "event": {
            "type": "quote", "instrument_id": {"symbol": "X", "exchange": "NSE"},
            "bid_price": 1.0, "ask_price": 1.5, "bid_size": 1.0, "ask_size": 1.0,
            "ts_event": ts_obj(1), "ts_init": ts_obj(1)
        },
        "ts_init": ts_obj(1)
    });
    let params = r#"{"instrument_id": {"symbol": "X", "exchange": "NSE"}}"#;
    let events = json!([quote, bar(10.0, 2)]).to_string();
    let err = run_strategy_json("contract_probe", params, &events, "[]", 0.0).unwrap_err();
    assert!(err.contains("before any bar"), "{err}");
}

#[test]
fn scripted_orders_with_risk_report_refusals_and_plain_runs_do_not() {
    let params = json!({"orders": [
        {"bar": 0, "instrument_id": {"symbol": "X", "exchange": "NSE"}, "side": "buy", "quantity": 1.0}
    ]})
    .to_string();
    let events = json!([bar(10.0, 1)]).to_string();
    let inst = json!([{
        "instrument_id": {"symbol": "X", "exchange": "NSE"}, "kind": "equity",
        "currency": "INR", "lot_size": 1.0, "tick_size": 0.05
    }])
    .to_string();
    let plain: Value = serde_json::from_str(
        &run_strategy_json("scripted_orders", &params, &events, &inst, 1000.0).unwrap(),
    )
    .unwrap();
    assert!(plain.get("order_rejections").is_none() && plain.get("risk_refusals").is_none());
    assert_eq!(plain["fills"].as_array().unwrap().len(), 1);

    let risk = json!({"trading_state": "halted"}).to_string();
    let halted: Value = serde_json::from_str(
        &run_strategy_risk_json(
            "scripted_orders",
            &params,
            &events,
            &inst,
            1000.0,
            0.0,
            0.0,
            Some(&risk),
        )
        .unwrap(),
    )
    .unwrap();
    assert_eq!(halted["fills"], json!([]));
    assert_eq!(halted["risk_refusals"][0]["code"], "risk_trading_halted");
    assert_eq!(
        halted["order_rejections"][0]["reason"],
        "risk_trading_halted"
    );
}

#[test]
fn scripted_orders_rejects_bad_params() {
    let events = json!([bar(10.0, 1)]).to_string();
    for bad in [
        r#"{"orders": [{"bar": 0, "instrument_id": {"symbol": "X", "exchange": "NSE"}, "side": "hold", "quantity": 1.0}]}"#,
        r#"{"orders": [{"bar": 0}]}"#,
        r#"{"orders": 3}"#,
    ] {
        assert!(run_strategy_json("scripted_orders", bad, &events, "[]", 0.0).is_err());
    }
}
