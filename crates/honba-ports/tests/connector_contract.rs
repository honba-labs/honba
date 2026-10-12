//! Contract tests for the market-data connector, through the public API only.
//!
//! A [`SimulatedConnector`] proves the state machine a real venue connector must
//! implement: retriable failures back off and reconnect, terminal failures stop
//! it for good, and a silent period emits a gap marker.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use honba_messages::{
    Bar, BarAggregation, BarSpecification, BarType, Exchange, InstrumentId, PriceType, UnixNanos,
};
use honba_ports::{
    Clock, ConnectorState, ExponentialBackoff, FeedWatchdog, MarketDataConnector, MarketEvent,
    PortError, PortResult, Subscription, SubscriptionParams,
};

/// A clock that moves only when told to, so the tests never touch wall time.
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

fn bar(close: f64, ts: u64) -> Bar {
    let spec = BarSpecification::new(1, BarAggregation::Minute, PriceType::Last);
    Bar::new(
        BarType::new(reliance(), spec),
        close,
        close,
        close,
        close,
        1.0,
        UnixNanos::from_u64(ts),
        UnixNanos::from_u64(ts),
    )
}

fn event_ts(event: &MarketEvent) -> Option<UnixNanos> {
    match event {
        MarketEvent::Bar(bar) => Some(bar.ts_event()),
        MarketEvent::QuoteTick(tick) => Some(tick.ts_event()),
        MarketEvent::TradeTick(tick) => Some(tick.ts_event()),
        _ => None,
    }
}

enum Step {
    Event(MarketEvent),
    Silent(u64),
    Retriable(String),
    Terminal(String),
}

struct SimulatedConnector {
    steps: VecDeque<Step>,
    state: ConnectorState,
    backoff: ExponentialBackoff,
    attempts: u32,
    clock: Arc<dyn Clock>,
    watchdog: FeedWatchdog,
    last_seen: UnixNanos,
    symbol: InstrumentId,
    stopped: Option<PortError>,
}

impl SimulatedConnector {
    fn new(clock: Arc<dyn Clock>, max_gap_ns: u64, steps: Vec<Step>) -> Self {
        Self {
            steps: steps.into(),
            state: ConnectorState::Disconnected {
                reason: "not subscribed".into(),
            },
            backoff: ExponentialBackoff::new(100, 1_000, 0.0, 7),
            attempts: 0,
            watchdog: FeedWatchdog::new(max_gap_ns, Arc::clone(&clock)),
            last_seen: UnixNanos::from_u64(0),
            symbol: reliance(),
            stopped: None,
            clock,
        }
    }
}

#[async_trait]
impl MarketDataConnector for SimulatedConnector {
    async fn subscribe(&mut self, params: SubscriptionParams) -> PortResult<Subscription> {
        if let Some(err) = &self.stopped {
            return Err(err.clone());
        }
        self.last_seen = self.clock.now().await;
        self.state = ConnectorState::Connected;
        Ok(Subscription::new(params.symbols().to_vec()))
    }

    async fn next_event(&mut self) -> PortResult<Option<MarketEvent>> {
        if let Some(err) = &self.stopped {
            return Err(err.clone());
        }
        let Some(step) = self.steps.pop_front() else {
            return Ok(None);
        };
        match step {
            Step::Event(event) => {
                self.state = ConnectorState::Connected;
                if let Some(ts) = event_ts(&event) {
                    self.last_seen = ts;
                }
                Ok(Some(event))
            }
            Step::Silent(nanos) => {
                self.clock.sleep(Duration::from_nanos(nanos)).await?;
                match self.watchdog.check(&self.symbol, self.last_seen).await {
                    Some(marker) => {
                        self.attempts += 1;
                        let backoff_ms = self.backoff.next(self.attempts).as_millis() as u64;
                        self.state = ConnectorState::Reconnecting {
                            attempt: self.attempts,
                            backoff_ms,
                        };
                        Ok(Some(marker))
                    }
                    None => {
                        self.state = ConnectorState::Connected;
                        Ok(None)
                    }
                }
            }
            Step::Retriable(_reason) => {
                self.attempts += 1;
                let backoff_ms = self.backoff.next(self.attempts).as_millis() as u64;
                self.state = ConnectorState::Reconnecting {
                    attempt: self.attempts,
                    backoff_ms,
                };
                self.clock.sleep(Duration::from_millis(backoff_ms)).await?;
                Ok(Some(MarketEvent::Reconnecting {
                    attempt: self.attempts,
                    backoff_ms,
                }))
            }
            Step::Terminal(reason) => {
                let err = PortError::Terminal(reason);
                self.state = ConnectorState::Disconnected {
                    reason: err.to_string(),
                };
                self.stopped = Some(err.clone());
                Err(err)
            }
        }
    }

    fn state(&self) -> ConnectorState {
        self.state.clone()
    }
}

fn connector(steps: Vec<Step>) -> SimulatedConnector {
    let clock: Arc<dyn Clock> = Arc::new(FakeClock::new(0));
    SimulatedConnector::new(clock, 1_000, steps)
}

#[tokio::test]
async fn simulated_feed_drop_emits_gap_marker() {
    let mut connector = connector(vec![
        Step::Event(MarketEvent::Bar(bar(100.0, 100))),
        Step::Silent(5_000),
        Step::Event(MarketEvent::Bar(bar(101.0, 6_000))),
    ]);

    connector
        .subscribe(SubscriptionParams::new(vec![reliance()]))
        .await
        .expect("subscribed");
    assert_eq!(connector.state(), ConnectorState::Connected);

    assert_eq!(
        connector.next_event().await.unwrap(),
        Some(MarketEvent::Bar(bar(100.0, 100)))
    );

    let dropped = connector
        .next_event()
        .await
        .unwrap()
        .expect("a gap marker after the drop");
    assert_eq!(
        dropped,
        MarketEvent::GapMarker {
            expected_ts: UnixNanos::from_u64(100),
            actual_ts: UnixNanos::from_u64(5_000),
            symbol: reliance(),
        }
    );
    assert!(
        matches!(
            connector.state(),
            ConnectorState::Reconnecting { attempt: 1, .. }
        ),
        "the gap moves the connector into reconnecting, got {:?}",
        connector.state()
    );

    assert_eq!(
        connector.next_event().await.unwrap(),
        Some(MarketEvent::Bar(bar(101.0, 6_000)))
    );
    assert_eq!(connector.state(), ConnectorState::Connected);
    assert_eq!(connector.next_event().await.unwrap(), None);
}

#[tokio::test]
async fn terminal_error_stops() {
    let mut connector = connector(vec![
        Step::Event(MarketEvent::Bar(bar(100.0, 10))),
        Step::Terminal("auth rejected".into()),
    ]);

    connector
        .subscribe(SubscriptionParams::new(vec![reliance()]))
        .await
        .expect("subscribed");
    assert!(connector.next_event().await.unwrap().is_some());

    let err = connector.next_event().await.unwrap_err();
    assert_eq!(err, PortError::Terminal("auth rejected".into()));
    assert!(!err.is_retryable(), "terminal must never retry");
    assert!(
        matches!(connector.state(), ConnectorState::Disconnected { .. }),
        "terminal disconnects the connector"
    );

    let again = connector.next_event().await.unwrap_err();
    assert_eq!(
        again,
        PortError::Terminal("auth rejected".into()),
        "no retry after a terminal failure"
    );
    assert!(matches!(
        connector.state(),
        ConnectorState::Disconnected { .. }
    ));
}

#[tokio::test]
async fn retriable_error_backs_off_and_reconnects() {
    let mut connector = connector(vec![
        Step::Retriable("socket reset".into()),
        Step::Event(MarketEvent::Bar(bar(100.0, 50))),
    ]);

    connector
        .subscribe(SubscriptionParams::new(vec![reliance()]))
        .await
        .expect("subscribed");

    assert_eq!(
        connector.next_event().await.unwrap(),
        Some(MarketEvent::Reconnecting {
            attempt: 1,
            backoff_ms: 100,
        })
    );
    assert!(matches!(
        connector.state(),
        ConnectorState::Reconnecting {
            attempt: 1,
            backoff_ms: 100
        }
    ));

    assert_eq!(
        connector.next_event().await.unwrap(),
        Some(MarketEvent::Bar(bar(100.0, 50)))
    );
    assert_eq!(connector.state(), ConnectorState::Connected);
}
