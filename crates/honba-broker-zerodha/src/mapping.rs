//! Pure mapping between Honba domain vocabulary and Kite Connect vocabulary.

use honba_messages::{OrderId, OrderSide, OrderStatus, OrderType, TimeInForce, UnixNanos};

use crate::wire::OrderRecord;

/// A value that has no Kite equivalent or could not be parsed.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[non_exhaustive]
pub enum MappingError {
    /// The domain value cannot be expressed on Kite.
    #[error("unsupported by Kite: {0}")]
    Unsupported(String),
    /// A Kite value could not be parsed.
    #[error("invalid Kite value: {0}")]
    Invalid(String),
}

/// Kite product type.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Product {
    /// Cash and carry (delivery equity).
    Cnc,
    /// Margin intraday square-off.
    Mis,
    /// Normal (F&O carry-forward).
    Nrml,
}

impl Product {
    /// Kite wire string.
    pub fn as_str(self) -> &'static str {
        match self {
            Product::Cnc => "CNC",
            Product::Mis => "MIS",
            Product::Nrml => "NRML",
        }
    }
}

/// Domain side -> `BUY`/`SELL`.
pub fn order_side_to_kite(side: OrderSide) -> Result<&'static str, MappingError> {
    match side {
        OrderSide::Buy => Ok("BUY"),
        OrderSide::Sell => Ok("SELL"),
        other => Err(MappingError::Unsupported(format!("order side {other:?}"))),
    }
}

/// `BUY`/`SELL` -> domain side.
pub fn order_side_from_kite(s: &str) -> Option<OrderSide> {
    match s {
        "BUY" => Some(OrderSide::Buy),
        "SELL" => Some(OrderSide::Sell),
        _ => None,
    }
}

/// Domain order type -> Kite order type.
pub fn order_type_to_kite(t: OrderType) -> Result<&'static str, MappingError> {
    match t {
        OrderType::Market => Ok("MARKET"),
        OrderType::Limit => Ok("LIMIT"),
        OrderType::StopMarket => Ok("SL-M"),
        OrderType::StopLimit => Ok("SL"),
        other => Err(MappingError::Unsupported(format!("order type {other:?}"))),
    }
}

/// Kite order type -> domain order type.
pub fn order_type_from_kite(s: &str) -> Option<OrderType> {
    match s {
        "MARKET" => Some(OrderType::Market),
        "LIMIT" => Some(OrderType::Limit),
        "SL-M" => Some(OrderType::StopMarket),
        "SL" => Some(OrderType::StopLimit),
        _ => None,
    }
}

/// Domain time in force -> Kite validity. Only `DAY` and `IOC` are supported.
pub fn tif_to_kite(t: TimeInForce) -> Result<&'static str, MappingError> {
    match t {
        TimeInForce::Day => Ok("DAY"),
        TimeInForce::Ioc => Ok("IOC"),
        other => Err(MappingError::Unsupported(format!(
            "time in force {other:?}"
        ))),
    }
}

/// Kite status string -> domain status. Unknown and in-flight states map to `Accepted`.
pub fn status_from_kite(s: &str) -> OrderStatus {
    match s {
        "COMPLETE" => OrderStatus::Filled,
        "CANCELLED" => OrderStatus::Cancelled,
        "REJECTED" => OrderStatus::Rejected,
        _ => OrderStatus::Accepted,
    }
}

/// Status of a full order record; `OPEN` with `0 < filled < quantity` is a partial fill.
pub fn order_status(rec: &OrderRecord) -> OrderStatus {
    let base = status_from_kite(rec.status.as_deref().unwrap_or(""));
    if rec.status.as_deref() == Some("OPEN") {
        if let (Some(filled), Some(qty)) = (rec.filled_quantity, rec.quantity) {
            if filled > 0 && filled < qty {
                return OrderStatus::PartiallyFilled;
            }
        }
    }
    base
}

/// Days since 1970-01-01 for a proleptic Gregorian date (Hinnant's algorithm).
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn days_in_month(y: i64, m: i64) -> i64 {
    match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ if (y % 4 == 0 && y % 100 != 0) || y % 400 == 0 => 29,
        _ => 28,
    }
}

/// Parses `YYYY-MM-DD HH:MM:SS` (IST, +05:30) into UTC [`UnixNanos`].
pub fn parse_kite_timestamp(s: &str) -> Result<UnixNanos, MappingError> {
    let bad = || MappingError::Invalid(format!("timestamp {s:?}"));
    let b = s.as_bytes();
    if b.len() != 19
        || b[4] != b'-'
        || b[7] != b'-'
        || b[10] != b' '
        || b[13] != b':'
        || b[16] != b':'
    {
        return Err(bad());
    }
    let num = |a: usize, z: usize| -> Result<i64, MappingError> {
        let part = &s[a..z];
        if part.bytes().all(|c| c.is_ascii_digit()) {
            part.parse().map_err(|_| bad())
        } else {
            Err(bad())
        }
    };
    let (y, mo, d) = (num(0, 4)?, num(5, 7)?, num(8, 10)?);
    let (h, mi, sec) = (num(11, 13)?, num(14, 16)?, num(17, 19)?);
    if !(1..=12).contains(&mo) || d < 1 || d > days_in_month(y, mo) || h > 23 || mi > 59 || sec > 59
    {
        return Err(bad());
    }
    let secs = days_from_civil(y, mo, d) * 86_400 + h * 3_600 + mi * 60 + sec - 19_800;
    u64::try_from(secs)
        .ok()
        .and_then(|s| s.checked_mul(1_000_000_000))
        .map(UnixNanos::new)
        .ok_or_else(bad)
}

/// Kite order tag: ASCII alphanumerics of the id, at most 20 characters, `HONBA` if empty.
pub fn kite_tag(id: &OrderId) -> String {
    let tag: String = id
        .as_str()
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .take(20)
        .collect();
    if tag.is_empty() {
        "HONBA".to_owned()
    } else {
        tag
    }
}
