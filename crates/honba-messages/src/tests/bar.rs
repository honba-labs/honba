//! Unit tests for `crate::market_data::bar`.

use crate::identifiers::{InstrumentId, Venue};
use crate::market_data::bar::*;
use crate::validation::InvariantError::{self, *};

fn spec(step: usize) -> BarSpecification {
    BarSpecification::new(step, BarAggregation::Minute, PriceType::Last)
}

fn valid() -> Bar {
    let bar_type = BarType::new(InstrumentId::new("X", Venue::new("NSE")), spec(1));
    Bar::new(bar_type, 10.0, 12.0, 9.0, 11.0, 5.0, 1.into(), 1.into())
}

#[test]
fn validate_reports_typed_errors() {
    let cases: [(Bar, InvariantError); 5] = [
        (
            Bar {
                high: 8.0,
                ..valid()
            },
            Crossed {
                lower: "low",
                upper: "high",
            },
        ),
        (
            Bar {
                close: 13.0,
                ..valid()
            },
            OutsideRange { field: "close" },
        ),
        (
            Bar {
                open: 8.5,
                ..valid()
            },
            OutsideRange { field: "open" },
        ),
        (
            Bar {
                volume: -1.0,
                ..valid()
            },
            Negative {
                field: "volume",
                value: -1.0,
            },
        ),
        (
            Bar {
                close: f64::NAN,
                ..valid()
            },
            NonFinite { field: "close" },
        ),
    ];
    assert_eq!(valid().validate(), Ok(()));
    for (bar, err) in cases {
        assert_eq!(bar.validate(), Err(err), "{bar:?}");
    }
}

#[test]
fn deserialize_rejects_invalid_bars_with_the_reason() {
    let mut json = serde_json::to_value(valid()).unwrap();
    json["high"] = 8.0.into();
    let err = serde_json::from_value::<Bar>(json).unwrap_err().to_string();
    assert!(err.contains("low") && err.contains("high"), "{err}");

    let mut json = serde_json::to_value(valid()).unwrap();
    json["bar_type"]["spec"]["step"] = 0.into();
    let err = serde_json::from_value::<Bar>(json).unwrap_err().to_string();
    assert!(err.contains("step"), "{err}");
}

#[test]
fn non_finite_values_never_serialize_as_null() {
    for bar in [
        Bar {
            close: f64::NAN,
            ..valid()
        },
        Bar {
            volume: f64::INFINITY,
            ..valid()
        },
    ] {
        assert!(serde_json::to_string(&bar).is_err(), "{bar:?}");
    }
}
