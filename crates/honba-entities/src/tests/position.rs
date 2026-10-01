//! Unit tests for `crate::position`.

use honba_messages::{InstrumentId, InvariantError, Venue};

use crate::{Currency, Position, PositionSide};

fn short() -> Position {
    let mut p = Position::flat(InstrumentId::new("X", Venue::new("NSE")), Currency::Inr);
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
fn non_finite_values_never_serialize_as_null() {
    let p = Position {
        realized_pnl: f64::NAN,
        ..short()
    };
    assert!(serde_json::to_string(&p).is_err());
}

#[test]
fn position_side_serializes_lowercase() {
    assert_eq!(serde_json::to_value(PositionSide::Long).unwrap(), "long");
    assert_eq!(serde_json::to_value(PositionSide::Short).unwrap(), "short");
}
