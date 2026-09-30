//! Nanosecond-precision timestamps.

use std::fmt;
use std::time::{SystemTime, UNIX_EPOCH};

/// A point in time expressed as nanoseconds since the Unix epoch (1970-01-01T00:00:00Z).
///
/// Every event in Honba carries two of these: `ts_event` (when the venue
/// observed the event) and `ts_init` (when Honba created the message).
///
/// ```
/// use honba_messages::UnixNanos;
///
/// let ts = UnixNanos::from_u64(1_700_000_000_000_000_000);
/// assert_eq!(ts.as_secs(), 1_700_000_000);
/// assert_eq!(ts.as_millis(), 1_700_000_000_000);
/// ```
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, PartialOrd, Ord, Hash)]
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
