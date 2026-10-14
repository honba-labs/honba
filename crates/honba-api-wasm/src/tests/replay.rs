//! Journal v1 replay tests for the pure replay module.

use honba_engine::{AuditKind, AuditLog, JournalHeader, JournalV1, TradingState};
use honba_messages::OrderEventKind;
use serde_json::Value;

use crate::replay::{journal_header_json, journal_record_count, replay_journal_json};

fn header() -> JournalHeader {
    JournalHeader {
        schema_version: 3,
        journal_version: 1,
        seed: 42,
        config_hash: "cfg_hash".to_string(),
        dataset_id: "ds_test".to_string(),
        git_rev: None,
        trial_count: Some(1),
    }
}

/// Serializes a journal to an in-memory NDJSON string.
fn ndjson_of(header: JournalHeader, log: &AuditLog) -> String {
    let mut buf = Vec::new();
    JournalV1::from_audit_log(header, log)
        .write_ndjson(&mut buf)
        .expect("write_ndjson");
    String::from_utf8(buf).expect("utf8")
}

/// A journal exercising every replay-visible record kind.
fn rich_log() -> AuditLog {
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
    log.record(AuditKind::OrderSubmitted {
        order_id: "O-3".to_string(),
        instrument: "Y.NSE".to_string(),
        side: "buy".to_string(),
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

#[test]
fn header_roundtrips_as_json() {
    let json = journal_header_json(&ndjson_of(header(), &AuditLog::new())).unwrap();
    let parsed: Value = serde_json::from_str(&json).unwrap();
    assert_eq!(parsed["schema_version"], 3);
    assert_eq!(parsed["journal_version"], 1);
    assert_eq!(parsed["seed"], 42);
    assert_eq!(parsed["config_hash"], "cfg_hash");
    assert_eq!(parsed["dataset_id"], "ds_test");
    assert_eq!(parsed["trial_count"], 1);
}

#[test]
fn record_count_excludes_the_header() {
    let log = rich_log();
    let count = journal_record_count(&ndjson_of(header(), &log)).unwrap();
    assert_eq!(count, log.len());
    assert_eq!(count, 11);
}

#[test]
fn empty_journal_replays_to_the_default_state() {
    let ndjson = ndjson_of(header(), &AuditLog::new());
    let replay: Value = serde_json::from_str(&replay_journal_json(&ndjson).unwrap()).unwrap();
    assert_eq!(replay["header"]["seed"], 42);
    assert_eq!(replay["replay"]["trading_state"], "active");
    assert_eq!(replay["replay"]["dispatches"], 0);
    assert_eq!(replay["replay"]["positions"].as_array().unwrap().len(), 0);
    assert_eq!(replay["replay"]["orders"].as_array().unwrap().len(), 0);
    assert_eq!(replay["replay"]["rejections"].as_object().unwrap().len(), 0);
}

#[test]
fn replay_reports_dispatches_positions_orders_and_rejections() {
    let ndjson = ndjson_of(header(), &rich_log());
    let replay: Value = serde_json::from_str(&replay_journal_json(&ndjson).unwrap()).unwrap();
    let r = &replay["replay"];

    assert_eq!(r["trading_state"], "reducing");
    assert_eq!(r["dispatches"], 2);

    assert_eq!(
        r["positions"],
        serde_json::json!([{"instrument": "X.NSE", "quantity": 6.0}])
    );

    let orders = r["orders"].as_array().unwrap();
    assert_eq!(orders.len(), 3);
    // Sorted by order id.
    assert_eq!(orders[0]["order_id"], "O-1");
    assert_eq!(orders[0]["instrument"], "X.NSE");
    assert_eq!(orders[0]["side"], "buy");
    assert_eq!(orders[0]["filled_qty"], 10.0);
    assert_eq!(orders[0]["lifecycle"], "accepted");
    assert_eq!(orders[0]["cancel_requested"], false);
    assert_eq!(orders[1]["order_id"], "O-2");
    assert_eq!(orders[1]["side"], "sell");
    assert_eq!(orders[1]["filled_qty"], 4.0);
    assert!(orders[1]["lifecycle"].is_null());
    assert_eq!(orders[1]["cancel_requested"], true);
    assert_eq!(orders[2]["order_id"], "O-3");
    assert_eq!(orders[2]["instrument"], "Y.NSE");
    assert_eq!(orders[2]["filled_qty"], 0.0);

    assert_eq!(
        r["rejections"],
        serde_json::json!({"O-9": "trading_halted"})
    );
}

#[test]
fn positions_are_sorted_by_instrument() {
    let mut log = AuditLog::new();
    for (order_id, instrument) in [("A", "Z.NSE"), ("B", "A.NSE")] {
        log.record(AuditKind::OrderSubmitted {
            order_id: order_id.to_string(),
            instrument: instrument.to_string(),
            side: "buy".to_string(),
        });
        log.record(AuditKind::FillProduced {
            order_id: order_id.to_string(),
            quantity: 1.0,
            price: 1.0,
            ts_event: 1,
        });
    }
    let replay: Value =
        serde_json::from_str(&replay_journal_json(&ndjson_of(header(), &log)).unwrap()).unwrap();
    let positions = replay["replay"]["positions"].as_array().unwrap();
    assert_eq!(positions[0]["instrument"], "A.NSE");
    assert_eq!(positions[1]["instrument"], "Z.NSE");
}

#[test]
fn empty_stream_is_refused() {
    let err = replay_journal_json("").unwrap_err();
    assert!(err.contains("empty_journal: empty journal stream"), "{err}");
    assert!(journal_header_json("").is_err());
}

#[test]
fn missing_header_is_refused() {
    let line = serde_json::json!({"seq": 0, "type": "event_dispatched", "ts_event": 1}).to_string();
    let err = replay_journal_json(&line).unwrap_err();
    assert!(
        err.contains("missing_header: missing journal v1 header"),
        "{err}"
    );
}

#[test]
fn unsupported_journal_version_is_refused() {
    let line = serde_json::json!({
        "type": "header",
        "schema_version": 3,
        "journal_version": 2,
        "seed": 1,
        "config_hash": "c",
        "dataset_id": "d",
    })
    .to_string();
    let err = journal_header_json(&line).unwrap_err();
    assert!(
        err.contains("unsupported_journal_version: unsupported journal version: 2"),
        "{err}"
    );
}

#[test]
fn malformed_record_line_is_refused() {
    let ndjson = format!(
        "{}\nnot json\n",
        ndjson_of(header(), &AuditLog::new()).trim()
    );
    let err = replay_journal_json(&ndjson).unwrap_err();
    assert!(err.contains("invalid_record:"), "{err}");
    assert!(journal_header_json(&ndjson).is_err());
}

#[test]
fn malformed_header_json_is_not_a_record_failure() {
    let err = replay_journal_json("not json\n").unwrap_err();
    assert!(err.contains("invalid_header_json:"), "{err}");
}

#[test]
fn header_value_failure_is_not_a_record_failure() {
    let mut value = serde_json::to_value(header()).unwrap();
    value.as_object_mut().unwrap().remove("seed");
    value
        .as_object_mut()
        .unwrap()
        .insert("type".to_string(), serde_json::json!("header"));
    let line = format!("{}\n", value);
    eprintln!("Test input line: {}", line);
    let err = replay_journal_json(&line).unwrap_err();
    eprintln!("Got error: {}", err);
    assert!(
        err.contains("invalid_header:"),
        "expected invalid_header: got {err}"
    );
}
