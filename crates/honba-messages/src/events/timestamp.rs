//! Nanosecond-precision timestamps.

use chrono::{TimeZone, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use std::fmt;
use std::time::{SystemTime, UNIX_EPOCH};

/// JSON representation of UnixNanos with ISO-8601 string and unix_nanos string.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
struct UnixNanosJson {
    iso: String,
    unix_nanos: String,
}

/// A point in time expressed as nanoseconds since the Unix epoch (1970-01-01T00:00:00Z).
///
/// Every event in Honba carries two of these: `ts_event` (when the exchange
/// observed the event) and `ts_init` (when Honba created the message).
///
/// Serializes as an object with both ISO-8601 and unix_nanos fields to avoid
/// JSON number precision issues (u64 exceeds Number.MAX_SAFE_INTEGER).
///
/// ```
/// use honba_messages::UnixNanos;
///
/// let ts = UnixNanos::from_u64(1_700_000_000_000_000_000);
/// assert_eq!(ts.as_secs(), 1_700_000_000);
/// assert_eq!(ts.as_millis(), 1_700_000_000_000);
/// ```
#[derive(
    Clone,
    Copy,
    Debug,
    Default,
    PartialEq,
    Eq,
    PartialOrd,
    Ord,
    Hash,
)]
pub struct UnixNanos(u64);

impl UnixNanos {
    /// Creates a timestamp from a raw nanosecond value.
    pub const fn new(value: u64) -> Self {
        Self(value)
    }

    /// Alias for [`UnixNanos::new`].
    pub const fn from_u64(value: u64) -> Self {
        Self(value)
    }

    /// Returns the current wall-clock time as nanoseconds since the Unix epoch.
    ///
    /// ```
    /// use honba_messages::UnixNanos;
    ///
    /// let ts = UnixNanos::now();
    /// assert!(ts.as_u64() > 0);
    /// ```
    pub fn now() -> Self {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64)
            .unwrap_or(0);
        Self(nanos)
    }

    /// Returns the raw nanosecond value.
    pub const fn as_u64(&self) -> u64 {
        self.0
    }

    /// Returns the timestamp as whole seconds since the Unix epoch.
    pub const fn as_secs(&self) -> u64 {
        self.0 / 1_000_000_000
    }

    /// Returns the timestamp as whole milliseconds since the Unix epoch.
    pub const fn as_millis(&self) -> u64 {
        self.0 / 1_000_000
    }

    /// Returns the timestamp as floating-point seconds since the Unix epoch.
    pub fn as_secs_f64(&self) -> f64 {
        self.0 as f64 / 1_000_000_000.0
    }

    /// Returns the ISO-8601 string representation with nanosecond precision.
    pub fn to_iso_string(&self) -> String {
        let secs = self.0 / 1_000_000_000;
        let nanos = (self.0 % 1_000_000_000) as u32;
        let dt = Utc.timestamp_opt(secs as i64, nanos).single().unwrap();
        dt.to_rfc3339_opts(chrono::SecondsFormat::Nanos, true)
    }

    /// Returns the unix nanoseconds as a string.
    pub fn to_unix_nanos_string(&self) -> String {
        self.0.to_string()
    }
}

impl Serialize for UnixNanos {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        let json = UnixNanosJson {
            iso: self.to_iso_string(),
            unix_nanos: self.to_unix_nanos_string(),
        };
        json.serialize(serializer)
    }
}

impl<'de> Deserialize<'de> for UnixNanos {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let json = UnixNanosJson::deserialize(deserializer)?;
        let nanos = json.unix_nanos.parse::<u64>().map_err(serde::de::Error::custom)?;
        Ok(Self(nanos))
    }
}

impl JsonSchema for UnixNanos {
    fn schema_name() -> String {
        "UnixNanos".to_string()
    }

    fn json_schema(gen: &mut schemars::gen::SchemaGenerator) -> schemars::schema::Schema {
        UnixNanosJson::json_schema(gen)
    }

    fn is_referenceable() -> bool {
        true
    }
}

impl fmt::Display for UnixNanos {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl From<u64> for UnixNanos {
    fn from(value: u64) -> Self {
        Self(value)
    }
}

impl From<UnixNanos> for u64 {
    fn from(value: UnixNanos) -> Self {
        value.0
    }
}