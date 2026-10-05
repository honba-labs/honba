//! Unit tests for `crate::position`.

use honba_messages::InvariantError;

use super::any_instrument;

use crate::{Currency, Money, Position, PositionSide};

fn short() -> Position {
    let mut p = Position::flat(any_instrument(), Currency::Inr);
    p.apply_fill(PositionSide::Short, 60.0, 10.0);
    p
}

#[test]
fn validate_reports_typed_errors() {
    assert_eq!(short().validate(), Ok(()));
    let negative = Position {
        quantity: -5.0,
        ..short()
    };
    assert_eq!(
        negative.validate(),
        Err(InvariantError::Negative {
            field: "quantity",
            value: -5.0,
        })
    );
    let json = serde_json::to_value(negative).unwrap();
    let err = serde_json::from_value::<Position>(json)
        .unwrap_err()
        .to_string();
    assert!(err.contains("quantity"), "{err}");
}

#[test]
fn realized_pnl_is_an_integer_on_the_wire() {
    // ADR 0011: a Money can never be NaN, so the "non-finite costs serialize
    // as null" failure mode is gone by construction. What remains is that the
    // wire form carries the integer.
    let mut p = Position::flat(any_instrument(), Currency::Inr);
    p.apply_fill(PositionSide::Long, 100.0, 10.0);
    p.apply_fill(PositionSide::Short, 40.0, 12.0);
    let v = serde_json::to_value(&p).unwrap();
    assert_eq!(v["realized_pnl"]["amount"], serde_json::json!(8000));
    assert_eq!(p.realized_pnl(), Money::new(8_000, Currency::Inr));
}

#[test]
fn a_legacy_float_realized_pnl_still_parses() {
    let json = serde_json::json!({
        "instrument_id": {"symbol": "X", "exchange": "NSE"},
        "currency": "INR",
        "side": "long",
        "quantity": 60.0,
        "avg_price": 10.0,
        "realized_pnl": 80.0,
    });
    let p: Position = serde_json::from_value(json).unwrap();
    assert_eq!(p.realized_pnl().minor(), 8_000);
}

#[test]
fn position_side_serializes_lowercase() {
    assert_eq!(serde_json::to_value(PositionSide::Long).unwrap(), "long");
    assert_eq!(serde_json::to_value(PositionSide::Short).unwrap(), "short");
}
