//! Unit tests for `crate::audit`.

use crate::{AuditKind, AuditLog, AuditRecord, TradingState};

fn sample_log() -> AuditLog {
    let mut log = AuditLog::new();
    log.record(AuditKind::EventDispatched { ts_event: 7 });
    log.record(AuditKind::OrderSubmitted {
        order_id: "O-1".to_string(),
        instrument: "X.NSE".to_string(),
        side: "buy".to_string(),
    });
    log.record(AuditKind::FillProduced {
        order_id: "O-1".to_string(),
        quantity: 5.0,
        price: 101.0,
        ts_event: 7,
    });
    log
}

#[test]
fn a_fresh_log_is_empty() {
    let log = AuditLog::new();
    assert!(log.is_empty());
    assert_eq!(log.len(), 0);
    assert!(log.records().is_empty());
}

#[test]
fn seq_starts_at_zero_and_advances_by_one_per_record() {
    let mut log = AuditLog::new();
    assert_eq!(log.record(AuditKind::EventDispatched { ts_event: 1 }), 0);
    assert_eq!(
        log.record(AuditKind::OrderCancelled {
            order_id: "O-1".to_string()
        }),
        1
    );
    assert_eq!(
        log.record(AuditKind::StateChanged {
            from: TradingState::Active,
            to: TradingState::Halted,
        }),
        2
    );
    assert_eq!(log.len(), 3);
    assert!(!log.is_empty());
}

#[test]
fn records_are_returned_in_insertion_order_with_matching_seq() {
    let log = sample_log();
    assert_eq!(
        log.records(),
        [
            AuditRecord {
                seq: 0,
                kind: AuditKind::EventDispatched { ts_event: 7 },
            },
            AuditRecord {
                seq: 1,
                kind: AuditKind::OrderSubmitted {
                    order_id: "O-1".to_string(),
                    instrument: "X.NSE".to_string(),
                    side: "buy".to_string(),
                },
            },
            AuditRecord {
                seq: 2,
                kind: AuditKind::FillProduced {
                    order_id: "O-1".to_string(),
                    quantity: 5.0,
                    price: 101.0,
                    ts_event: 7,
                },
            },
        ]
    );
}

#[test]
fn interleaved_kinds_keep_one_monotonic_seq() {
    let mut log = AuditLog::new();
    let kinds = [
        AuditKind::OrderCancelled {
            order_id: "A".to_string(),
        },
        AuditKind::EventDispatched { ts_event: 1 },
        AuditKind::OrderRejected {
            order_id: "B".to_string(),
            reason: "no execution attached".to_string(),
        },
        AuditKind::EventDispatched { ts_event: 2 },
        AuditKind::OrderCancelled {
            order_id: "C".to_string(),
        },
    ];
    let mut expected = Vec::new();
    for (i, kind) in kinds.iter().enumerate() {
        let seq = log.record(kind.clone());
        assert_eq!(seq, i as u64);
        expected.push(AuditRecord {
            seq,
            kind: kind.clone(),
        });
    }
    assert_eq!(log.records(), expected);
}

#[test]
fn logs_with_the_same_history_are_equal_and_debuggable() {
    assert_eq!(sample_log(), sample_log());
    assert_ne!(sample_log(), AuditLog::new());
    let rendered = format!("{:?}", sample_log());
    assert!(rendered.contains("EventDispatched"));
    assert!(rendered.contains("OrderSubmitted"));
    let cloned = sample_log();
    assert_eq!(cloned.records().len(), 3);
    assert_eq!(format!("{cloned:?}"), rendered);
}
