//! Unit tests for the pure query/response mapping of the instruments and bars endpoints.

use honba_entities::{Currency, Instrument, InstrumentKind};
use honba_messages::{
    BarAggregation, BarSpecification, ErrorCode, Exchange, InstrumentId, PriceType, UnixNanos,
};
use serde_json::json;

use crate::{
    instrument_json, parse_instrument_id, parse_timeframe, BarsQuery, InstrumentsQuery,
    DEFAULT_TIMEFRAME,
};

fn spec(step: usize, agg: BarAggregation) -> BarSpecification {
    BarSpecification::new(step, agg, PriceType::Last)
}

fn bars(tf: Option<&str>, from: Option<&str>, to: Option<&str>) -> BarsQuery {
    BarsQuery {
        tf: tf.map(Into::into),
        from: from.map(Into::into),
        to: to.map(Into::into),
    }
}

#[test]
fn timeframes_parse_to_last_price_specs() {
    for (text, want) in [
        ("30s", spec(30, BarAggregation::Second)),
        ("1m", spec(1, BarAggregation::Minute)),
        ("15m", spec(15, BarAggregation::Minute)),
        ("4h", spec(4, BarAggregation::Hour)),
        ("1d", spec(1, BarAggregation::Day)),
        ("2w", spec(2, BarAggregation::Week)),
        ("1mo", spec(1, BarAggregation::Month)),
    ] {
        assert_eq!(parse_timeframe(text).unwrap(), want, "{text}");
    }
}

#[test]
fn malformed_timeframes_are_invalid_requests_naming_the_field() {
    for text in [
        "",
        "m",
        "1",
        "0m",
        "-1m",
        "1x",
        "1 m",
        "1M",
        "99999999999999999999m",
    ] {
        let err = parse_timeframe(text).unwrap_err();
        assert_eq!(err.code, ErrorCode::ValidationInvalidRequest, "{text}");
        assert_eq!(
            err.context.as_ref().unwrap()["field"],
            json!("tf"),
            "{text}"
        );
    }
}

#[test]
fn an_empty_bars_query_means_the_default_timeframe_and_an_open_range() {
    let r = bars(None, None, None).resolve().unwrap();
    assert_eq!(r.spec, parse_timeframe(DEFAULT_TIMEFRAME).unwrap());
    assert_eq!((r.from, r.to), (None, None));
}

#[test]
fn rfc3339_and_plain_dates_resolve_to_utc_nanos() {
    let r = bars(
        Some("1d"),
        Some("2024-01-01"),
        Some("2024-01-02T05:30:00+05:30"),
    )
    .resolve()
    .unwrap();
    assert_eq!(r.from, Some(UnixNanos::from_u64(1_704_067_200_000_000_000)));
    assert_eq!(r.to, Some(UnixNanos::from_u64(1_704_153_600_000_000_000)));
}

#[test]
fn an_unparsable_bound_names_which_one() {
    for (from, to, field) in [
        (Some("yesterday"), None, "from"),
        (None, Some("2024-13-01"), "to"),
    ] {
        let err = bars(None, from, to).resolve().unwrap_err();
        assert_eq!(err.code, ErrorCode::ValidationInvalidRequest);
        assert_eq!(err.context.as_ref().unwrap()["field"], json!(field));
    }
}

#[test]
fn a_bound_before_the_epoch_is_rejected() {
    let err = bars(None, Some("1969-12-31"), None).resolve().unwrap_err();
    assert_eq!(err.code, ErrorCode::ValidationInvalidRequest);
}

#[test]
fn an_empty_or_inverted_range_is_rejected() {
    for (from, to) in [("2024-01-02", "2024-01-02"), ("2024-01-03", "2024-01-02")] {
        let err = bars(None, Some(from), Some(to)).resolve().unwrap_err();
        assert_eq!(err.code, ErrorCode::ValidationInvalidRequest);
        assert_eq!(
            err.context.as_ref().unwrap()["reason"],
            json!("empty_range")
        );
    }
}

#[test]
fn instrument_ids_split_on_the_last_dot() {
    let id = parse_instrument_id("RELIANCE.NSE").unwrap();
    assert_eq!(id, InstrumentId::new("RELIANCE", Exchange::new("NSE")));
    let dotted = parse_instrument_id("BRK.B.NYSE").unwrap();
    assert_eq!(dotted.symbol(), "BRK.B");
    assert_eq!(dotted.exchange().as_str(), "NYSE");
}

#[test]
fn instrument_ids_without_both_parts_are_invalid() {
    for text in ["RELIANCE", ".NSE", "RELIANCE.", ""] {
        let err = parse_instrument_id(text).unwrap_err();
        assert_eq!(err.code, ErrorCode::ValidationInvalidRequest, "{text}");
    }
}

#[test]
fn the_instruments_filter_matches_exactly_and_conjunctively() {
    let id = InstrumentId::new("TCS", Exchange::new("NSE"));
    let q = |exchange: Option<&str>, symbol: Option<&str>| InstrumentsQuery {
        exchange: exchange.map(Into::into),
        symbol: symbol.map(Into::into),
    };
    assert!(q(None, None).matches(&id));
    assert!(q(Some("NSE"), None).matches(&id));
    assert!(q(None, Some("TCS")).matches(&id));
    assert!(q(Some("NSE"), Some("TCS")).matches(&id));
    assert!(!q(Some("BSE"), None).matches(&id));
    assert!(!q(Some("NSE"), Some("INFY")).matches(&id));
    assert!(!q(None, Some("tcs")).matches(&id));
}

#[test]
fn an_instrument_serializes_with_stable_snake_case_fields() {
    let inst = Instrument::new(
        InstrumentId::new("NIFTY", Exchange::new("NSE")),
        InstrumentKind::MutualFund,
        Currency::Inr,
        75.0,
        0.05,
    );
    assert_eq!(
        instrument_json(&inst),
        json!({
            "id": {"symbol": "NIFTY", "exchange": "NSE"},
            "kind": "mutual_fund",
            "currency": "INR",
            "lot_size": 75.0,
            "tick_size": 0.05,
        })
    );
}
