//! Golden-vector contract tests for the JSON wire format (ADR 006).
//!
//! The vectors in `schema/golden/` are shared with the Python tests. Every
//! case must (1) deserialize into the expected Rust value and (2) serialize
//! back to exactly the golden JSON. Invalid cases must be rejected.

use std::collections::BTreeMap;
use std::path::PathBuf;

use honba_messages::{
    AggressorSide, Bar, BarAggregation, BarSpecification, BarType, Event, Exchange, InstrumentId,
    Message, Order, OrderId, OrderSide, OrderStatus, OrderType, PriceType, QuoteTick, TimeInForce,
    TradeId, TradeTick, UnixNanos, SCHEMA_VERSION,
};
use serde::de::DeserializeOwned;
use serde::Serialize;
use serde_json::Value;

const TS: u64 = 1_700_000_060_000_000_000;

struct Golden {
    cases: BTreeMap<String, Value>,
    invalid: BTreeMap<String, Value>,
    /// Raw JSON text that must be rejected (e.g. duplicate keys, which a
    /// parsed `Value` cannot represent).
    invalid_text: BTreeMap<String, String>,
    /// Payloads with unknown fields that a reader must accept (ADR 0012 rule
    /// 1), paired with the canonical JSON the unknown fields are dropped to.
    tolerated: BTreeMap<String, (Value, Value)>,
}

fn load(file: &str, type_name: &str) -> Golden {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../schema/golden")
        .join(file);
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("{path:?}: {e}"));
    let doc: Value = serde_json::from_str(&text).unwrap();
    assert_eq!(doc["schema_version"], u64::from(SCHEMA_VERSION), "{file}");
    assert_eq!(doc["type"], type_name, "{file}");
    let collect = |key: &str| -> BTreeMap<String, Value> {
        doc.get(key)
            .and_then(Value::as_array)
            .map(|cases| {
                cases
                    .iter()
                    .map(|c| (c["name"].as_str().unwrap().to_owned(), c["value"].clone()))
                    .collect()
            })
            .unwrap_or_default()
    };
    let invalid_text = doc
        .get("invalid_text")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|c| {
            let name = c["name"].as_str().unwrap().to_owned();
            (name, c["text"].as_str().unwrap().to_owned())
        })
        .collect();
    let tolerated = doc
        .get("tolerated")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .map(|c| {
            let name = c["name"].as_str().unwrap().to_owned();
            (name, (c["value"].clone(), c["canonical"].clone()))
        })
        .collect();
    Golden {
        cases: collect("cases"),
        invalid: collect("invalid"),
        invalid_text,
        tolerated,
    }
}

/// Asserts that every golden case equals the expected value both ways.
fn check<T>(file: &str, type_name: &str, expected: Vec<(&str, T)>)
where
    T: Serialize + DeserializeOwned + PartialEq + std::fmt::Debug,
{
    let golden = load(file, type_name);
    let expected: BTreeMap<&str, T> = expected.into_iter().collect();
    let golden_names: Vec<&str> = golden.cases.keys().map(String::as_str).collect();
    let expected_names: Vec<&str> = expected.keys().copied().collect();
    assert_eq!(golden_names, expected_names, "{file}: case names differ");

    for (name, json) in &golden.cases {
        let want = &expected[name.as_str()];
        let got: T = serde_json::from_value(json.clone())
            .unwrap_or_else(|e| panic!("{file}/{name}: deserialize failed: {e}"));
        assert_eq!(&got, want, "{file}/{name}: deserialized value");
        assert_eq!(
            &serde_json::to_value(want).unwrap(),
            json,
            "{file}/{name}: serialized JSON"
        );
        // Text round trip (exercises float parsing and u64 handling).
        let text = serde_json::to_string(&got).unwrap();
        let again: T = serde_json::from_str(&text).unwrap();
        assert_eq!(&again, want, "{file}/{name}: text round trip");
    }
    for (name, json) in &golden.invalid {
        let res: Result<T, _> = serde_json::from_value(json.clone());
        assert!(res.is_err(), "{file}/{name}: invalid case was accepted");
    }
    for (name, text) in &golden.invalid_text {
        let res: Result<T, _> = serde_json::from_str(text);
        assert!(res.is_err(), "{file}/{name}: invalid text was accepted");
    }
    for (name, (value, canonical)) in &golden.tolerated {
        let got: T = serde_json::from_value(value.clone())
            .unwrap_or_else(|e| panic!("{file}/{name}: unknown fields must be ignored: {e}"));
        let want: T = serde_json::from_value(canonical.clone()).unwrap();
        assert_eq!(got, want, "{file}/{name}: tolerated value");
        assert_eq!(
            &serde_json::to_value(&got).unwrap(),
            canonical,
            "{file}/{name}"
        );
    }
}

fn nse(sym: &str) -> InstrumentId {
    InstrumentId::new(sym, Exchange::new("NSE"))
}

fn ts(n: u64) -> UnixNanos {
    UnixNanos::from_u64(n)
}

fn banknifty_bar() -> Bar {
    Bar::new(
        BarType::new(
            nse("BANKNIFTY"),
            BarSpecification::new(5, BarAggregation::Minute, PriceType::Last),
        ),
        48_000.0,
        48_200.5,
        47_900.05,
        48_150.3,
        12_345.0,
        ts(TS),
        ts(TS + 1),
    )
}

#[allow(clippy::too_many_arguments)]
fn order(
    id: &str,
    side: OrderSide,
    order_type: OrderType,
    qty: f64,
    price: Option<f64>,
    trigger: Option<f64>,
    status: OrderStatus,
    tif: TimeInForce,
) -> Order {
    let o = Order::new(
        OrderId::new(id),
        nse("NIFTY50"),
        side,
        order_type,
        qty,
        price,
        tif,
        ts(TS),
        ts(TS),
    )
    .with_status(status);
    match trigger {
        Some(t) => o.with_trigger_price(t),
        None => o,
    }
}

fn quote() -> QuoteTick {
    QuoteTick::new(
        nse("NIFTY50"),
        22_000.0,
        22_001.0,
        50.0,
        75.0,
        ts(TS),
        ts(TS),
    )
}

#[test]
fn instrument_id_golden() {
    check(
        "instrument_id.json",
        "InstrumentId",
        vec![
            ("nse_equity", nse("RELIANCE")),
            (
                "bse_index",
                InstrumentId::new("SENSEX", Exchange::new("BSE")),
            ),
        ],
    );
}

#[test]
fn bar_golden() {
    let edges = Bar::new(
        BarType::new(
            nse("RELIANCE"),
            BarSpecification::new(1, BarAggregation::Day, PriceType::Mid),
        ),
        0.1,
        0.1 + 0.2,
        1e-7,
        0.2,
        0.0,
        ts(0),
        ts(u64::MAX),
    );
    check(
        "bar.json",
        "Bar",
        vec![
            ("banknifty_5m_last", banknifty_bar()),
            ("float_and_u64_edges", edges),
        ],
    );
}

#[test]
fn order_golden() {
    use OrderSide::*;
    use OrderStatus::*;
    use OrderType::*;
    use TimeInForce::*;
    check(
        "order.json",
        "Order",
        vec![
            (
                "market_buy_initialized",
                order("O-1", Buy, Market, 75.0, None, None, Initialized, Day),
            ),
            (
                "limit_sell_accepted",
                order(
                    "O-2",
                    Sell,
                    Limit,
                    50.0,
                    Some(22_000.05),
                    None,
                    Accepted,
                    Gtc,
                ),
            ),
            (
                "stop_market_submitted",
                order(
                    "O-3",
                    Buy,
                    StopMarket,
                    75.0,
                    None,
                    Some(21_950.0),
                    Submitted,
                    Ioc,
                ),
            ),
            (
                "stop_limit_partially_filled",
                order(
                    "O-4",
                    Sell,
                    StopLimit,
                    75.0,
                    Some(22_010.0),
                    Some(22_000.0),
                    PartiallyFilled,
                    Fok,
                ),
            ),
            (
                "gtd_expired_no_side",
                order(
                    "O-5",
                    NoOrderSide,
                    Limit,
                    1.0,
                    Some(1.0),
                    None,
                    Expired,
                    Gtd,
                ),
            ),
        ],
    );
}

#[test]
fn event_golden_uses_order_id() {
    let tick = TradeTick::new(
        nse("RELIANCE"),
        2_950.5,
        100.0,
        AggressorSide::Seller,
        TradeId::new("T-42"),
        ts(TS),
        ts(TS),
    );
    let stop_limit = order(
        "O-4",
        OrderSide::Buy,
        OrderType::StopLimit,
        75.0,
        Some(22_010.0),
        Some(22_000.0),
        OrderStatus::Initialized,
        TimeInForce::Day,
    );
    check(
        "event.json",
        "Event",
        vec![
            ("quote", Event::Quote(quote())),
            ("trade_tick", Event::Trade(tick)),
            ("bar", Event::Bar(banknifty_bar())),
            ("order", Event::Order(stop_limit)),
            (
                "order_accepted",
                Event::OrderAccepted {
                    order_id: OrderId::new("O-1"),
                    ts_event: ts(TS),
                },
            ),
            (
                "order_rejected",
                Event::OrderRejected {
                    order_id: OrderId::new("O-2"),
                    reason: "insufficient margin".into(),
                    ts_event: ts(TS),
                },
            ),
            (
                "order_filled",
                Event::OrderFilled {
                    order_id: OrderId::new("O-1"),
                    last_qty: 25.0,
                    last_px: 22_000.05,
                    ts_event: ts(TS),
                },
            ),
            (
                "order_cancelled",
                Event::OrderCancelled {
                    order_id: OrderId::new("O-3"),
                    ts_event: ts(TS),
                },
            ),
        ],
    );
}

#[test]
fn message_golden_carries_schema_version() {
    let filled = Event::OrderFilled {
        order_id: OrderId::new("O-1"),
        last_qty: 75.0,
        last_px: 22_000.0,
        ts_event: ts(TS),
    };
    check(
        "message.json",
        "Message",
        vec![
            ("order_filled", Message::new(filled, ts(TS + 10))),
            ("quote", Message::new(Event::Quote(quote()), ts(TS + 1))),
        ],
    );
}

/// E11-S2: timestamps cross JSON as `{iso, unix_nanos}`. The vectors pin
/// values past 2^53 (where a JSON number would lose nanoseconds), the u64
/// edges, and the rejections (pre-epoch, overflow, a sign, a JSON number).
#[test]
fn unix_nanos_golden() {
    check(
        "unix_nanos.json",
        "UnixNanos",
        vec![
            ("epoch", ts(0)),
            ("one_nanosecond", ts(1)),
            ("two_pow_53", ts(1 << 53)),
            ("two_pow_53_plus_one", ts((1 << 53) + 1)),
            ("nanos_beyond_f64_precision", ts(1_700_000_060_123_456_789)),
            ("u64_max", ts(u64::MAX)),
        ],
    );
}
