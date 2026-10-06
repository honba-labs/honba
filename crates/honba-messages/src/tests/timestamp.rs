//! Unit tests for the `UnixNanos` JSON form (E11-S2, ADR 0012 rule 4).

use crate::UnixNanos;

fn parse(unix_nanos: &str) -> Result<UnixNanos, serde_json::Error> {
    let json = serde_json::json!({"iso": "x", "unix_nanos": unix_nanos});
    serde_json::from_value(json)
}

#[test]
fn serializes_as_iso_and_a_decimal_string() {
    let ts = UnixNanos::from_u64((1 << 53) + 1);
    let v = serde_json::to_value(ts).unwrap();
    assert_eq!(v["unix_nanos"], "9007199254740993");
    assert_eq!(v["iso"], "1970-04-15T05:59:59.254740993Z");
}

#[test]
fn unix_nanos_must_be_plain_ascii_decimal_digits() {
    // `u64::from_str` alone accepts a leading `+`; the Python reader does not,
    // and the two must agree on what is a timestamp.
    for bad in [
        "+5",
        "",
        "-1",
        " 5",
        "5 ",
        "1.5",
        "1e9",
        "\u{665}",
        "18446744073709551616",
    ] {
        assert!(parse(bad).is_err(), "{bad:?} accepted");
    }
    assert_eq!(parse("007").unwrap(), UnixNanos::from_u64(7));
    assert_eq!(
        parse("18446744073709551615").unwrap(),
        UnixNanos::from_u64(u64::MAX)
    );
}

#[test]
fn a_json_number_is_not_a_timestamp() {
    let v = serde_json::json!({"iso": "x", "unix_nanos": 5});
    assert!(serde_json::from_value::<UnixNanos>(v).is_err());
    assert!(serde_json::from_value::<UnixNanos>(serde_json::json!(5)).is_err());
}
