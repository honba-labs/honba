//! Unit tests for `crate::watchdog`.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use honba_messages::{Exchange, InstrumentId, UnixNanos};

use crate::{Clock, FeedWatchdog, MarketEvent, PortResult};

/// A clock that moves only when told to, so the test never touches wall time.
struct FakeClock {
    now: AtomicU64,
}

impl FakeClock {
    fn new(start: u64) -> Self {
        Self {
            now: AtomicU64::new(start),
        }
    }
}

#[async_trait]
impl Clock for FakeClock {
    async fn now(&self) -> UnixNanos {
        UnixNanos::from_u64(self.now.load(Ordering::SeqCst))
    }

    async fn sleep(&self, dur: Duration) -> PortResult<()> {
        let nanos = u64::try_from(dur.as_nanos()).expect("test durations fit in u64");
        self.now.fetch_add(nanos, Ordering::SeqCst);
        Ok(())
    }
}

fn reliance() -> InstrumentId {
    InstrumentId::new("RELIANCE", Exchange::new("NSE"))
}

#[tokio::test]
async fn watchdog_emits_gap_after_silence() {
    let clock: Arc<dyn Clock> = Arc::new(FakeClock::new(1_000));
    let watchdog = FeedWatchdog::new(100, Arc::clone(&clock));
    let last_seen = UnixNanos::from_u64(1_000);

    assert_eq!(
        watchdog.check(&reliance(), last_seen).await,
        None,
        "no silence yet"
    );

    clock.sleep(Duration::from_nanos(100)).await.unwrap();
    assert_eq!(
        watchdog.check(&reliance(), last_seen).await,
        None,
        "at the limit is not yet a gap"
    );

    clock.sleep(Duration::from_nanos(1)).await.unwrap();
    assert_eq!(
        watchdog.check(&reliance(), last_seen).await,
        Some(MarketEvent::GapMarker {
            expected_ts: UnixNanos::from_u64(1_000),
            actual_ts: UnixNanos::from_u64(1_101),
            symbol: reliance(),
        })
    );
    assert_eq!(watchdog.max_gap_ns(), 100);
}
