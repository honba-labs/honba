//! The event loop.

use honba_messages::UnixNanos;

use crate::clock::Clock;
use crate::data::DataFeed;
use crate::error::Result;
use crate::handler::Handler;
use crate::queue::EventQueue;

/// Default number of events pulled from the feed before dispatching.
///
/// Larger values reorder more aggressively within a window; smaller values
/// reduce latency for live streams. Set to `1` when the feed guarantees
/// non-decreasing `ts_event` and you want immediate dispatch.
pub const DEFAULT_BATCH_SIZE: usize = 1024;

/// Drives events from a [`DataFeed`] through a set of [`Handler`]s.
pub struct Engine {
    clock: Clock,
    queue: EventQueue,
    handlers: Vec<Box<dyn Handler>>,
    batch_size: usize,
}

impl Default for Engine {
    fn default() -> Self {
        Self::new()
    }
}

impl Engine {
    /// Creates an engine with the default batch size.
    pub fn new() -> Self {
        Self {
            clock: Clock::default(),
            queue: EventQueue::new(),
            handlers: Vec::new(),
            batch_size: DEFAULT_BATCH_SIZE,
        }
    }

    /// Sets the batch size.
    ///
    /// The feed is read up to `batch_size` events at a time, then the queue
    /// is drained in `ts_event` order before the next batch is pulled. Use
    /// `1` for a strictly-ordered feed that must dispatch immediately.
    pub fn with_batch_size(mut self, batch_size: usize) -> Self {
        assert!(batch_size > 0, "batch_size must be positive");
        self.batch_size = batch_size;
        self
    }

    /// Returns the current clock value.
    pub fn now(&self) -> UnixNanos {
        self.clock.now()
    }

    /// Registers a handler.
    pub fn add_handler<H: Handler + 'static>(&mut self, handler: H) {
        self.handlers.push(Box::new(handler));
    }

    /// Runs the engine to completion against the given feed.
    ///
    /// Each iteration pulls up to `batch_size` events from the feed into the
    /// queue, then pops the earliest and dispatches it. Time ordering within
    /// a batch is enforced by the queue; ordering across batches is enforced
    /// by the monotonic [`Clock`].
    ///
    /// If the feed produces events earlier than ones already dispatched, the
    /// clock returns [`AlgoError::ClockRegression`](crate::AlgoError::ClockRegression)
    /// and the run aborts.
    pub fn run(&mut self, feed: &mut dyn DataFeed) -> Result<()> {
        for h in &mut self.handlers {
            h.on_start()?;
        }

        loop {
            for _ in 0..self.batch_size {
                match feed.next()? {
                    Some(msg) => self.queue.push(msg),
                    None => break,
                }
            }

            match self.queue.pop() {
                Some(msg) => {
                    let ts_event = msg.event().ts_event();
                    self.clock.advance_to(ts_event)?;
                    for h in &mut self.handlers {
                        h.on_event(msg.event(), msg.ts_init())?;
                    }
                }
                None => break,
            }
        }

        for h in &mut self.handlers {
            h.on_stop()?;
        }

        Ok(())
    }
}
