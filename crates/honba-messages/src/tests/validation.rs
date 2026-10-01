//! Unit tests for `crate::validation`.

use crate::validation::*;

#[test]
fn checks_classify_values() {
    assert_eq!(finite("x", 1.0), Ok(1.0));
    assert_eq!(
        finite("x", f64::NAN),
        Err(InvariantError::NonFinite { field: "x" })
    );
    assert_eq!(
        positive("q", 0.0),
        Err(InvariantError::NotPositive {
            field: "q",
            value: 0.0
        })
    );
    assert_eq!(
        positive("q", f64::INFINITY),
        Err(InvariantError::NonFinite { field: "q" })
    );
    assert_eq!(non_negative("v", 0.0), Ok(0.0));
    assert_eq!(
        non_negative("v", -1.0),
        Err(InvariantError::Negative {
            field: "v",
            value: -1.0
        })
    );
    assert_eq!(finite_opt("p", None), Ok(()));
    assert!(finite_opt("p", Some(f64::NAN)).is_err());
}
