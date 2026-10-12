//! The market-data connector contract: reconnect, backoff, gaps and stops.
//!
//! [`MarketDataFeed`](crate::MarketDataFeed) is the seam a backtest and a live
//! session share. This module is the live half: a connector owns a broker
//! session, reconnects when it drops, and turns the things a strategy must be
//! able to see — a hole in the stream, a reconnect attempt — into typed
//! [`MarketEvent`]s rather than silence.
//!
//! The split of responsibility is deliberate. The connector is the only place
//! that knows a socket exists; the backoff schedule
//! ([`ExponentialBackoff`](crate::ExponentialBackoff)), the silence detector
//! ([`FeedWatchdog`](crate::FeedWatchdog)) and the error taxonomy
//! ([`PortError`](crate::PortError)) are pure pieces it composes. A connector
//! reports its lifecycle through [`MarketDataConnector::state`], so an
//! operator surface can show "reconnecting, attempt 3, in 800 ms" without
//! reading logs.

use async_trait::async_trait;
use honba_messages::{Bar, InstrumentId, QuoteTick, TradeTick, UnixNanos};

use crate::error::PortResult;

/// One item on a connector's stream: market data, or a marker about the stream.
///
/// The data variants wrap the same L0 messages the kernel orders. The markers
/// are connection facts that never existed at the venue — a gap the connector
/// detected ([`MarketEvent::GapMarker`]) and the reconnect it is making
/// ([`MarketEvent::Reconnecting`]) — surfaced as events so a strategy observes
/// them in-band instead of inferring them from a stall.
#[derive(Clone, Debug, PartialEq)]
#[non_exhaustive]
pub enum MarketEvent {
    /// An aggregated bar.
    Bar(Bar),
    /// A quote (top-of-book) update.
    QuoteTick(QuoteTick),
    /// A trade (last-sale) update.
    TradeTick(TradeTick),
    /// A hole in the stream: `actual_ts` arrived where `expected_ts` was due.
    ///
    /// `expected_ts` is the last timestamp the consumer saw for `symbol`;
    /// `actual_ts` is the first timestamp after the silence. The connector
    /// emits this when its [`FeedWatchdog`](crate::FeedWatchdog) trips.
    GapMarker {
        /// The last timestamp delivered before the hole.
        expected_ts: UnixNanos,
        /// The clock time at which the hole was detected.
        actual_ts: UnixNanos,
        /// The instrument whose stream went quiet.
        symbol: InstrumentId,
    },
    /// The connector is reconnecting after a retriable failure.
    ///
    /// Strategies may pause quoting or expect stale data for the next
    /// `backoff_ms`; the next event after a successful reconnect is data.
    Reconnecting {
        /// The reconnect attempt number, 1-based.
        attempt: u32,
        /// The delay waited before this attempt, in milliseconds.
        backoff_ms: u64,
    },
}

/// The instruments a connector should deliver.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SubscriptionParams {
    symbols: Vec<InstrumentId>,
}

impl SubscriptionParams {
    /// Requests `symbols`.
    pub fn new(symbols: Vec<InstrumentId>) -> Self {
        Self { symbols }
    }

    /// The requested instruments.
    pub fn symbols(&self) -> &[InstrumentId] {
        &self.symbols
    }
}

/// An accepted subscription.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Subscription {
    symbols: Vec<InstrumentId>,
}

impl Subscription {
    /// Records that `symbols` are subscribed.
    pub fn new(symbols: Vec<InstrumentId>) -> Self {
        Self { symbols }
    }

    /// The subscribed instruments.
    pub fn symbols(&self) -> &[InstrumentId] {
        &self.symbols
    }
}

/// The lifecycle state of a connector, for operators and strategies.
///
/// A healthy connector is [`ConnectorState::Connected`]. A drop moves it
/// through [`ConnectorState::GapEmitted`] into
/// [`ConnectorState::Reconnecting`]; a failure the connector cannot retry
/// leaves it [`ConnectorState::Disconnected`] for good.
#[derive(Clone, Debug, PartialEq, Eq)]
#[non_exhaustive]
pub enum ConnectorState {
    /// The session is up and delivering events.
    Connected,
    /// The initial subscription is being established.
    Connecting {
        /// The connect attempt number, 1-based.
        attempt: u32,
    },
    /// A retriable failure occurred; reconnecting after the backoff.
    Reconnecting {
        /// The reconnect attempt number, 1-based.
        attempt: u32,
        /// The delay before this attempt, in milliseconds.
        backoff_ms: u64,
    },
    /// The connector stopped; `reason` is permanent for the session.
    Disconnected {
        /// Why the connector stopped.
        reason: String,
    },
    /// A silence gap was observed and marked on the stream.
    GapEmitted,
}

/// A live market-data connection that reconnects, backs off and marks gaps.
///
/// The connector owns a broker session, so it is `Send` and driven by one task
/// — the same rule as [`MarketDataFeed`](crate::MarketDataFeed). On a retriable
/// failure it waits out an [`ExponentialBackoff`](crate::ExponentialBackoff)
/// and retries; on [`PortError::Terminal`](crate::PortError::Terminal) or
/// [`PortError::Unrecoverable`](crate::PortError::Unrecoverable) it stops and
/// stays stopped.
#[async_trait]
pub trait MarketDataConnector: Send {
    /// Subscribes to `params`, establishing the session if needed.
    async fn subscribe(&mut self, params: SubscriptionParams) -> PortResult<Subscription>;

    /// Returns the next event, or `Ok(None)` when the stream ends.
    ///
    /// A retriable failure surfaces as
    /// [`PortError::Retriable`](crate::PortError::Retriable) or a
    /// [`MarketEvent::Reconnecting`] marker; a terminal or unrecoverable
    /// failure surfaces as the matching error and stops the connector, which
    /// the caller must not retry. A gap in the stream arrives as a
    /// [`MarketEvent::GapMarker`].
    async fn next_event(&mut self) -> PortResult<Option<MarketEvent>>;

    /// The connector's current lifecycle state.
    fn state(&self) -> ConnectorState;
}
