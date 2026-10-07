//! Unit tests for `crate::events::event`.

use crate::events::event::*;
use crate::events::timestamp::UnixNanos;
use crate::identifiers::{OrderId, VenueOrderId};

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

fn ts(n: u64) -> UnixNanos {
    UnixNanos::from_u64(n)
}

#[test]
fn schema_version_is_four() {
    // ADR 0019 decision 2: `order_filled` now means the completing fill.
    assert_eq!(SCHEMA_VERSION, 4);
}

#[test]
fn order_partially_filled_roundtrips_with_cum_qty() {
    let ev = Event::OrderPartiallyFilled {
        order_id: OrderId::new("O-1"),
        last_qty: 1.0,
        last_px: 10.5,
        cum_qty: 1.0,
        ts_event: ts(7),
    };
    let json = serde_json::to_value(&ev).unwrap();
    assert_eq!(json["type"], "order_partially_filled");
    assert_eq!(json["cum_qty"], 1.0);
    assert_eq!(serde_json::from_value::<Event>(json).unwrap(), ev);
    assert_eq!(ev.ts_event(), ts(7));
}

#[test]
fn order_partially_filled_rejects_bad_quantities_and_never_serializes_nan() {
    let partial = |last_qty: f64, last_px: f64, cum_qty: f64| Event::OrderPartiallyFilled {
        order_id: OrderId::new("O-1"),
        last_qty,
        last_px,
        cum_qty,
        ts_event: ts(1),
    };
    for (q, c) in [(0.0, 1.0), (1.0, 0.0), (-1.0, 1.0), (1.0, -1.0)] {
        let json = serde_json::to_value(partial(q, 10.0, c)).unwrap();
        assert!(serde_json::from_value::<Event>(json).is_err(), "{q} {c}");
    }
    assert!(serde_json::to_string(&partial(1.0, f64::NAN, 1.0)).is_err());
    assert!(serde_json::to_string(&partial(1.0, 1.0, f64::INFINITY)).is_err());
}

#[test]
fn order_cancel_requested_and_expired_roundtrip() {
    let requested = Event::OrderCancelRequested {
        order_id: OrderId::new("O-1"),
        ts_event: ts(3),
    };
    let expired = Event::OrderExpired {
        order_id: OrderId::new("O-1"),
        ts_event: ts(4),
    };
    for (ev, tag, t) in [
        (requested, "order_cancel_requested", 3),
        (expired, "order_expired", 4),
    ] {
        let json = serde_json::to_value(&ev).unwrap();
        assert_eq!(json["type"], tag);
        assert_eq!(serde_json::from_value::<Event>(json).unwrap(), ev);
        assert_eq!(ev.ts_event(), ts(t));
        assert!(!ev.is_market_data());
    }
}

#[test]
fn order_accepted_venue_order_id_is_optional_and_additive() {
    let without = Event::OrderAccepted {
        order_id: OrderId::new("O-1"),
        venue_order_id: None,
        ts_event: ts(1),
    };
    let json = serde_json::to_value(&without).unwrap();
    assert!(json.get("venue_order_id").is_none(), "None is omitted");
    assert_eq!(serde_json::from_value::<Event>(json).unwrap(), without);

    let with = Event::OrderAccepted {
        order_id: OrderId::new("O-1"),
        venue_order_id: Some(VenueOrderId::new("V-9")),
        ts_event: ts(1),
    };
    let json = serde_json::to_value(&with).unwrap();
    assert_eq!(json["venue_order_id"], "V-9");
    assert_eq!(serde_json::from_value::<Event>(json).unwrap(), with);
}
