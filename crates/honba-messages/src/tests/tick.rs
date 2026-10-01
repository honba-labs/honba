//! Unit tests for `crate::market_data::tick`.

use crate::identifiers::{InstrumentId, TradeId, Venue};
use crate::market_data::tick::*;
use crate::validation::InvariantError::*;

fn id() -> InstrumentId {
    InstrumentId::new("X", Venue::new("NSE"))
}

fn quote() -> QuoteTick {
    QuoteTick::new(id(), 10.0, 10.5, 1.0, 2.0, 1.into(), 1.into())
}

fn trade() -> TradeTick {
    TradeTick::new(
        id(),
        10.0,
        3.0,
        AggressorSide::Buyer,
        TradeId::new("T"),
        1.into(),
        1.into(),
    )
}

#[test]
fn quote_validate_reports_typed_errors() {
    assert_eq!(quote().validate(), Ok(()));
    let crossed = QuoteTick {
        ask_price: 9.0,
        ..quote()
    };
    assert_eq!(
        crossed.validate(),
        Err(Crossed {
            lower: "bid_price",
            upper: "ask_price",
        })
    );
    let negative = QuoteTick {
        ask_size: -2.0,
        ..quote()
    };
    assert_eq!(
        negative.validate(),
        Err(Negative {
            field: "ask_size",
            value: -2.0,
        })
    );
    let json = serde_json::to_value(crossed).unwrap();
    assert!(serde_json::from_value::<QuoteTick>(json).is_err());
}

#[test]
fn trade_tick_validate_reports_typed_errors() {
    assert_eq!(trade().validate(), Ok(()));
    let negative = TradeTick {
        size: -3.0,
        ..trade()
    };
    assert_eq!(
        negative.validate(),
        Err(Negative {
            field: "size",
            value: -3.0,
        })
    );
    let json = serde_json::to_value(negative).unwrap();
    assert!(serde_json::from_value::<TradeTick>(json).is_err());
}

#[test]
fn non_finite_values_never_serialize_as_null() {
    let q = QuoteTick {
        bid_size: f64::NAN,
        ..quote()
    };
    assert!(serde_json::to_string(&q).is_err());
    let t = TradeTick {
        price: f64::NEG_INFINITY,
        ..trade()
    };
    assert!(serde_json::to_string(&t).is_err());
}
