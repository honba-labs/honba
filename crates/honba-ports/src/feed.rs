//! The market-data port.

use async_trait::async_trait;
use honba_messages::{InstrumentId, Message};

use crate::error::PortResult;

/// An ordered stream of market data.
///
/// This is the async mirror of the kernel's synchronous feed: the engine calls
/// `honba_engine::DataFeed::next() -> Option<Message>`, and the async shell drains this port to
/// feed it. One stream of [`Message`], not one stream per event kind, because the queue is keyed
/// on `Message` and the audit sink consumes `Message`; a per-kind split would force the edge to
/// merge and reorder streams that the kernel already knows how to order.
///
/// Implementations deliver whatever the venue delivers. Ordering is the kernel's job: bursts and
/// out-of-order arrival are normal and are resolved downstream on `ts_event`. A feed that has
/// nothing to deliver returns `Ok(None)`; `Err` means the stream itself is broken (see
/// [`PortError::is_retryable`](crate::PortError::is_retryable)).
///
/// The port owns a subscription, so it is `Send` and driven by one task.
#[async_trait]
pub trait MarketDataFeed: Send {
    /// Starts delivering updates for `symbols`, adding to any existing subscription.
    ///
    /// Subscribing to an already-subscribed symbol is not an error and must not duplicate
    /// deliveries.
    async fn subscribe(&mut self, symbols: &[InstrumentId]) -> PortResult<()>;

    /// Stops delivering updates for `symbols`.
    ///
    /// Unsubscribing from a symbol that is not subscribed is not an error.
    async fn unsubscribe(&mut self, symbols: &[InstrumentId]) -> PortResult<()>;

    /// Returns the next message, or `Ok(None)` when the feed is exhausted.
    ///
    /// `Ok(None)` means "nothing right now, and nothing more is queued". A closed or failed
    /// connection is an error, not a silent end of stream.
    async fn next(&mut self) -> PortResult<Option<Message>>;
}
