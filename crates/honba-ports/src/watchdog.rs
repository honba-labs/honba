//! A silent-feed watchdog.

use std::sync::Arc;

use honba_messages::{InstrumentId, UnixNanos};

use crate::{Clock, MarketEvent};

/// Watches one symbol's feed for silence.
///
/// A live connector reads events off a socket; when the socket goes quiet the
/// consumer is blind. The watchdog answers one question against the injected
/// [`Clock`]: has `last_seen` been alive longer than `max_gap_ns`? If so it
/// returns a [`MarketEvent::GapMarker`] naming the hole, so a strategy sees a
/// gap instead of a stale book. It never sleeps, never reads a wall clock and
/// never spawns — the connector calls it when an event arrives or when it is
/// about to block.
///
/// ```
/// use std::sync::Arc;
/// # use honba_messages::{Exchange, InstrumentId, UnixNanos};
/// # use honba_ports::{Clock, FeedWatchdog, MarketEvent, PortResult};
/// # use async_trait::async_trait;
/// # struct C;
/// # #[async_trait]
/// # impl Clock for C {
/// #     async fn now(&self) -> UnixNanos { UnixNanos::from_u64(5_000) }
/// #     async fn sleep(&self, _: std::time::Duration) -> PortResult<()> { Ok(()) }
/// # }
/// # #[tokio::main(flavor = "current_thread")]
/// # async fn main() {
/// let clock: Arc<dyn Clock> = Arc::new(C);
/// let watchdog = FeedWatchdog::new(1_000, clock);
/// let symbol = InstrumentId::new("RELIANCE", Exchange::new("NSE"));
///
/// let marker = watchdog.check(&symbol, UnixNanos::from_u64(0)).await;
/// assert!(matches!(marker, Some(MarketEvent::GapMarker { .. })));
/// # }
/// ```
pub struct FeedWatchdog {
    max_gap_ns: u64,
    clock: Arc<dyn Clock>,
}

impl FeedWatchdog {
    /// Creates a watchdog that flags more than `max_gap_ns` of silence.
    pub fn new(max_gap_ns: u64, clock: Arc<dyn Clock>) -> Self {
        Self { max_gap_ns, clock }
    }

    /// The configured maximum silence, in nanoseconds.
    pub fn max_gap_ns(&self) -> u64 {
        self.max_gap_ns
    }

    /// Returns a [`MarketEvent::GapMarker`] when the clock has run past
    /// `last_seen` by more than `max_gap_ns`, and `None` otherwise.
    ///
    /// `expected_ts` is `last_seen` — the timestamp the consumer still expects
    /// — and `actual_ts` is now, so the gap records both ends of the hole.
    pub async fn check(&self, symbol: &InstrumentId, last_seen: UnixNanos) -> Option<MarketEvent> {
        let now = self.clock.now().await;
        let silence = now.as_u64().saturating_sub(last_seen.as_u64());
        (silence > self.max_gap_ns).then_some(MarketEvent::GapMarker {
            expected_ts: last_seen,
            actual_ts: now,
            symbol: symbol.clone(),
        })
    }
}
