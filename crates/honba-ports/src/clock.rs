//! The time source at the edges.

use std::time::Duration;

use async_trait::async_trait;
use honba_messages::UnixNanos;

use crate::error::PortResult;

/// The only clock an edge implementation exposes.
///
/// `honba-engine` advances its own kernel clock with the timestamps on the data it
/// processes. This port is for the *outside* of the kernel: a live session needs real
/// wall-clock time to pace reconnects, timeouts and intraday timers, and a replay needs a
/// clock driven by recorded data. Domain code never calls `UnixNanos::now()`; it receives a
/// timestamp through [`Clock::now`] or on the messages themselves.
///
/// Implementations are shared by reference, so this port is `Send + Sync`.
///
/// ```
/// use std::time::Duration;
/// use async_trait::async_trait;
/// use honba_messages::UnixNanos;
/// use honba_ports::{Clock, PortResult};
///
/// struct StopwatchClock {
///     nanos: std::sync::atomic::AtomicU64,
/// }
///
/// #[async_trait]
/// impl Clock for StopwatchClock {
///     async fn now(&self) -> UnixNanos {
///         UnixNanos::from_u64(self.nanos.load(std::sync::atomic::Ordering::SeqCst))
///     }
///
///     async fn sleep(&self, dur: Duration) -> PortResult<()> {
///         self.nanos
///             .fetch_add(dur.as_nanos() as u64, std::sync::atomic::Ordering::SeqCst);
///         Ok(())
///     }
/// }
/// ```
#[async_trait]
pub trait Clock: Send + Sync {
    /// Returns the current time as nanoseconds since the Unix epoch.
    ///
    /// Successive calls must not move backwards.
    async fn now(&self) -> UnixNanos;

    /// Waits for `dur` to elapse and returns.
    ///
    /// Implementations return [`PortError::Timeout`](crate::PortError::Timeout) when the wait
    /// cannot complete; they never return early.
    async fn sleep(&self, dur: Duration) -> PortResult<()>;
}
