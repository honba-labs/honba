//! Conformance of the pure wasm replay surface against `honba-engine`'s own [`ReplayState`].
//!
//! There is no golden file: the expected values are derived from the engine's
//! `ReplayState::from_log` (and the journal header) for a small deterministic journal built here
//! the same way `crates/honba-engine/tests` build one. This pins the wasm JSON shape — sorted
//! `positions`/`orders`, `rejections` keyed by order id, the wire spellings of `side`,
//! `lifecycle` and `trading_state` — to the engine's replay.

use honba_api_wasm::replay::{journal_header_json, journal_record_count, replay_journal_json};
use honba_engine::{AuditKind, AuditLog, JournalHeader, JournalV1, ReplayState, TradingState};
use honba_messages::OrderEventKind;
use serde_json::Value;

fn header() -> JournalHeader {
    JournalHeader {
        schema_version: 3,
        journal_version: 1,
        seed: 42,
        config_hash: "cfg_hash_conformance".to_string(),
        dataset_id: "ds_conformance".to_string(),
        git_rev: Some("abcdef0".to_string()),
        trial_count: Some(2),
    }
}

/// A small deterministic journal: fills on both sides, a cancel, a lifecycle, a rejection and a
/// state change.
fn build_log() -> AuditLog {
    let mut log = AuditLog::new();
    log.record(AuditKind::EventDispatched { ts_event: 1 });
    log.record(AuditKind::EventDispatched { ts_event: 2 });
    log.record(AuditKind::OrderSubmitted {
        order_id: "O-1".to_string(),
        instrument: "X.NSE".to_string(),
        side: "buy".to_string(),
    });
    log.record(AuditKind::OrderSubmitted {
        order_id: "O-2".to_string(),
        instrument: "X.NSE".to_string(),
        side: "sell".to_string(),
    });
    log.record(AuditKind::FillProduced {
        order_id: "O-1".to_string(),
        quantity: 10.0,
        price: 100.0,
        ts_event: 3,
    });
    log.record(AuditKind::FillProduced {
        order_id: "O-2".to_string(),
        quantity: 4.0,
        price: 105.0,
        ts_event: 4,
    });
    log.record(AuditKind::OrderLifecycle {
        order_id: "O-1".to_string(),
        event: OrderEventKind::Accepted,
        ts_event: 5,
    });
    log.record(AuditKind::CancelRequested {
        order_id: "O-2".to_string(),
    });
    log.record(AuditKind::OrderRejected {
        order_id: "O-9".to_string(),
        reason: "trading_halted".to_string(),
    });
    log.record(AuditKind::StateChanged {
        from: TradingState::Active,
        to: TradingState::Reducing,
    });
    log
}

fn ndjson_of(header: &JournalHeader, log: &AuditLog) -> String {
    let mut buf = Vec::new();
    JournalV1::from_audit_log(header.clone(), log)
        .write_ndjson(&mut buf)
        .expect("write_ndjson");
    String::from_utf8(buf).expect("utf8")
}

fn lifecycle_str(kind: Option<OrderEventKind>) -> Value {
    match kind {
        Some(k) => Value::String(k.to_string()),
        None => Value::Null,
    }
}

fn side_str(side: honba_messages::OrderSide) -> &'static str {
    match side {
        honba_messages::OrderSide::Buy => "buy",
        honba_messages::OrderSide::Sell => "sell",
        honba_messages::OrderSide::NoOrderSide => "no_order_side",
        _ => "no_order_side",
    }
}

#[test]
fn header_matches_the_engine_journal_header() {
    let header = header();
    let ndjson = ndjson_of(&header, &build_log());
    let parsed: Value = serde_json::from_str(&journal_header_json(&ndjson).unwrap()).unwrap();
    assert_eq!(parsed, serde_json::to_value(&header).unwrap());
}

#[test]
fn record_count_matches_the_engine_log() {
    let log = build_log();
    let ndjson = ndjson_of(&header(), &log);
    assert_eq!(journal_record_count(&ndjson).unwrap(), log.len());
}

#[test]
fn replay_summary_matches_the_engine_replay_state() {
    let header = header();
    let log = build_log();
    let ndjson = ndjson_of(&header, &log);
    let replay: Value = serde_json::from_str(&replay_journal_json(&ndjson).unwrap()).unwrap();

    let state = ReplayState::from_log(&log);

    assert_eq!(replay["header"], serde_json::to_value(&header).unwrap());
    let r = &replay["replay"];

    assert_eq!(r["dispatches"].as_u64().unwrap(), state.dispatches());
    let expected_state = match state.trading_state() {
        TradingState::Active => "active",
        TradingState::Reducing => "reducing",
        TradingState::Halted => "halted",
    };
    assert_eq!(r["trading_state"], expected_state);

    // positions: length and each (instrument, quantity) match the engine's map.
    let positions = r["positions"].as_array().unwrap();
    assert_eq!(positions.len(), state.positions().len());
    for (instrument, quantity) in state.positions() {
        let entry = positions
            .iter()
            .find(|p| p["instrument"] == instrument.to_string())
            .unwrap_or_else(|| panic!("missing position {instrument}"));
        assert_eq!(entry["quantity"].as_f64().unwrap(), *quantity);
    }
    // Sorted ascending by instrument string.
    let instruments: Vec<&str> = positions
        .iter()
        .map(|p| p["instrument"].as_str().unwrap())
        .collect();
    let mut sorted = instruments.clone();
    sorted.sort_unstable();
    assert_eq!(instruments, sorted);

    // orders: length, then field-by-field against the engine's ReplayOrder.
    let orders = r["orders"].as_array().unwrap();
    assert_eq!(orders.len(), state.orders().len());
    for order in orders {
        let order_id = order["order_id"].as_str().unwrap();
        let expected = state.order(order_id).expect("order present");
        assert_eq!(order["instrument"], expected.instrument.to_string());
        assert_eq!(order["side"], side_str(expected.side));
        assert_eq!(order["filled_qty"].as_f64().unwrap(), expected.filled_qty);
        assert_eq!(order["lifecycle"], lifecycle_str(expected.lifecycle));
        assert_eq!(
            order["cancel_requested"].as_bool().unwrap(),
            expected.cancel_requested
        );
    }
    // Sorted ascending by order id.
    let ids: Vec<&str> = orders
        .iter()
        .map(|o| o["order_id"].as_str().unwrap())
        .collect();
    let mut sorted_ids = ids.clone();
    sorted_ids.sort_unstable();
    assert_eq!(ids, sorted_ids);

    // rejections: each JSON entry equals the engine's rejection lookup.
    let rejections = r["rejections"].as_object().unwrap();
    assert_eq!(rejections["O-9"], "trading_halted");
    for (order_id, reason) in rejections {
        assert_eq!(
            reason.as_str().unwrap(),
            state.rejection(order_id).expect("rejection present")
        );
    }
}

#[test]
fn bad_input_is_refused() {
    assert!(replay_journal_json("").is_err());
    assert!(journal_header_json("").is_err());
    assert!(replay_journal_json("{\"seq\":0,\"type\":\"event_dispatched\"}").is_err());
}
