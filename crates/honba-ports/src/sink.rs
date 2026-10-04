//! The audit-trail port.

use async_trait::async_trait;
use honba_messages::Message;

use crate::error::PortResult;

/// The durable record of what the engine saw and did.
///
/// Every message the kernel processes goes through this port: journals, Parquet partitions and
/// the audit log are all sinks. Two rules follow from the audit stream having to be complete:
///
/// - **Append-only.** Implementations must not reorder messages and must not rewrite or drop
///   one already accepted for a given run. A run's sink is a sequence, not a set.
/// - **Explicit flush.** [`Sink::flush`] is separate from [`Sink::write`] because the engine must
///   be able to say "this is durable" at a boundary (end of run, before a crash-stop, before a
///   hand-off) without waiting for a buffer to happen to fill.
///
/// A sink that fails must keep the failure. Silently discarding messages would produce a journal
/// that looks complete and is not.
#[async_trait]
pub trait Sink: Send {
    /// Appends one message to the stream.
    ///
    /// Returns `Ok(())` once the message is accepted for the stream, which for a buffered sink
    /// may be before it is on disk; call [`Sink::flush`] for durability.
    async fn write(&mut self, msg: Message) -> PortResult<()>;

    /// Makes every previously accepted message durable and returns once it is.
    async fn flush(&mut self) -> PortResult<()>;
}
