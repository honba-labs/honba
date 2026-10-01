//! Unit tests for `crate::events::event`.

use crate::events::event::*;
use crate::events::timestamp::UnixNanos;
use crate::identifiers::OrderId;

#[test]
fn schema_version_accepts_only_current() {
    assert!(SchemaVersion::try_from(SCHEMA_VERSION).is_ok());
    let err = SchemaVersion::try_from(SCHEMA_VERSION + 1).unwrap_err();
    assert!(err.contains("unsupported schema_version"), "{err}");
}

#[test]
fn order_filled_rejects_non_positive_qty_and_never_serializes_nan() {
    let filled = |qty: f64, px: f64| Event::OrderFilled {
        order_id: OrderId::new("O-1"),
        last_qty: qty,
        last_px: px,
        ts_event: UnixNanos::from_u64(1),
    };
    let json = serde_json::to_value(filled(0.0, 10.0)).unwrap();
    assert!(serde_json::from_value::<Event>(json).is_err());
    assert!(serde_json::to_string(&filled(1.0, f64::NAN)).is_err());
    assert!(serde_json::to_string(&filled(f64::INFINITY, 1.0)).is_err());
}

#[test]
fn new_message_has_current_schema_version() {
    let ev = Event::OrderCancelled {
        order_id: OrderId::new("O-1"),
        ts_event: UnixNanos::from_u64(1),
    };
    assert_eq!(
        Message::new(ev, UnixNanos::from_u64(2)).schema_version(),
        SCHEMA_VERSION
    );
}
