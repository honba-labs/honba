//! Value invariants of the wire types (ADR 006).
//!
//! Constructors `debug_assert` these invariants; deserialization enforces
//! them with an [`InvariantError`], so a payload that breaks one is rejected
//! instead of producing a value the rest of the system would mis-handle.
//! Serialization refuses non-finite floats (see [`serialize_finite`]) rather
//! than letting `serde_json` write them as `null`.

use std::fmt;

use serde::ser::Error as _;
use serde::Serializer;

/// Why a value violates the invariants of its type.
///
/// ```
/// use honba_messages::InvariantError;
///
/// let err = InvariantError::NotPositive { field: "quantity", value: -5.0 };
/// assert_eq!(err.to_string(), "quantity must be > 0, got -5");
/// ```
#[derive(Clone, Copy, Debug, PartialEq)]
#[non_exhaustive]
pub enum InvariantError {
    /// A numeric field is NaN or infinite.
    NonFinite {
        /// The field name.
        field: &'static str,
    },
    /// A field that must be strictly positive is not.
    NotPositive {
        /// The field name.
        field: &'static str,
        /// The offending value.
        value: f64,
    },
    /// A field that must be zero or positive is negative.
    Negative {
        /// The field name.
        field: &'static str,
        /// The offending value.
        value: f64,
    },
    /// `lower` exceeds `upper` (a bar's low above its high, a bid above the ask).
    Crossed {
        /// The field that must be the smaller one.
        lower: &'static str,
        /// The field that must be the larger one.
        upper: &'static str,
    },
    /// A bar's open or close lies outside its `[low, high]` range.
    OutsideRange {
        /// The field name.
        field: &'static str,
    },
    /// A field holds a value that is valid for its type but not here
    /// (for example a trade whose side is `no_order_side`).
    NotAllowed {
        /// The field name.
        field: &'static str,
    },
}

impl fmt::Display for InvariantError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            InvariantError::NonFinite { field } => write!(f, "{field} must be finite"),
            InvariantError::NotPositive { field, value } => {
                write!(f, "{field} must be > 0, got {value}")
            }
            InvariantError::Negative { field, value } => {
                write!(f, "{field} must be >= 0, got {value}")
            }
            InvariantError::Crossed { lower, upper } => {
                write!(f, "{lower} must be <= {upper}")
            }
            InvariantError::OutsideRange { field } => {
                write!(f, "{field} must lie within [low, high]")
            }
            InvariantError::NotAllowed { field } => write!(f, "{field} has a disallowed value"),
        }
    }
}

impl std::error::Error for InvariantError {}

/// Checks that `value` is finite.
pub fn finite(field: &'static str, value: f64) -> Result<f64, InvariantError> {
    if value.is_finite() {
        Ok(value)
    } else {
        Err(InvariantError::NonFinite { field })
    }
}

/// Checks that `value`, if present, is finite.
pub fn finite_opt(field: &'static str, value: Option<f64>) -> Result<(), InvariantError> {
    value.map_or(Ok(()), |v| finite(field, v).map(drop))
}

/// Checks that `value` is finite and strictly positive.
pub fn positive(field: &'static str, value: f64) -> Result<f64, InvariantError> {
    if finite(field, value)? > 0.0 {
        Ok(value)
    } else {
        Err(InvariantError::NotPositive { field, value })
    }
}

/// Checks that `value` is finite and not negative.
pub fn non_negative(field: &'static str, value: f64) -> Result<f64, InvariantError> {
    if finite(field, value)? >= 0.0 {
        Ok(value)
    } else {
        Err(InvariantError::Negative { field, value })
    }
}

/// Serializes an `f64`, failing on NaN or infinity.
///
/// `serde_json` would otherwise write a non-finite float as `null`, which a
/// reader cannot tell apart from an absent optional value.
///
/// ```
/// #[derive(serde::Serialize)]
/// struct Px(#[serde(serialize_with = "honba_messages::validation::serialize_finite")] f64);
///
/// assert!(serde_json::to_string(&Px(1.5)).is_ok());
/// assert!(serde_json::to_string(&Px(f64::NAN)).is_err());
/// ```
pub fn serialize_finite<S: Serializer>(value: &f64, serializer: S) -> Result<S::Ok, S::Error> {
    if value.is_finite() {
        serializer.serialize_f64(*value)
    } else {
        Err(S::Error::custom(format!(
            "cannot serialize non-finite f64 {value}"
        )))
    }
}

/// Like [`serialize_finite`] for an optional value; `None` is written as `null`.
pub fn serialize_finite_opt<S: Serializer>(
    value: &Option<f64>,
    serializer: S,
) -> Result<S::Ok, S::Error> {
    match value {
        Some(v) => {
            if v.is_finite() {
                serializer.serialize_some(v)
            } else {
                Err(S::Error::custom(format!(
                    "cannot serialize non-finite f64 {v}"
                )))
            }
        }
        None => serializer.serialize_none(),
    }
}

/// Deserializes an `f64` that must be finite and strictly positive.
pub fn deserialize_positive<'de, D: serde::Deserializer<'de>>(
    field: &'static str,
    deserializer: D,
) -> Result<f64, D::Error> {
    let value: f64 = serde::Deserialize::deserialize(deserializer)?;
    positive(field, value).map_err(serde::de::Error::custom)
}

#[cfg(test)]
mod tests {
    use super::*;

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
}
