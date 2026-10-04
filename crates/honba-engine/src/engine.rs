//! The event loop.

use honba_entities::Trade;
use honba_messages::{Event, Message, Order, OrderId, OrderSide, UnixNanos};

use crate::audit::{AuditKind, AuditLog, AuditRecord};
use crate::clock::Clock;
use crate::data::DataFeed;
use crate::error::Result;
use crate::execution::ExecutionEngine;
use crate::handler::{EngineOutput, Handler};
use crate::queue::EventQueue;
use crate::state::TradingState;

/// Default number of events pulled from the feed before dispatching.
///
/// Larger values reorder more aggressively within a window; smaller values
/// reduce latency for live streams. Set to `1` when the feed guarantees
/// non-decreasing `ts_event` and you want immediate dispatch.
pub const DEFAULT_BATCH_SIZE: usize = 1024;

/// Drives events from a [`DataFeed`] through a set of [`Handler`]s.
///
/// An engine belongs to one thread: it holds the clock, the queue, the
/// handlers, the execution sink and the audit log, and shares none of them.
/// Drive it from a single thread, and to hand work to another, move the whole
/// engine rather than its parts.
pub struct Engine {
    clock: Clock,
    queue: EventQueue,
    handlers: Vec<Box<dyn Handler>>,
    batch_size: usize,
    execution: Option<Box<dyn ExecutionEngine>>,
    trading_state: TradingState,
    audit: AuditLog,
    pending_fills: Vec<Trade>,
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
            execution: None,
            trading_state: TradingState::Active,
            audit: AuditLog::new(),
            pending_fills: Vec::new(),
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

    /// Attaches an execution sink. [`EngineOutput::Orders`] / `Cancels` are routed here.
    ///
    /// Replacing a sink drops the old one; its fills already collected by the
    /// engine stay available through [`Engine::drain_fills`].
    pub fn set_execution(&mut self, execution: Box<dyn ExecutionEngine>) {
        self.execution = Some(execution);
    }

    /// Returns the attached execution sink, if any.
    pub fn execution(&self) -> Option<&dyn ExecutionEngine> {
        self.execution.as_deref()
    }

    /// Returns the current trading state.
    pub fn trading_state(&self) -> TradingState {
        self.trading_state
    }

    /// Returns the audit trail, oldest record first.
    pub fn audit(&self) -> &[AuditRecord] {
        self.audit.records()
    }

    /// Injects a message into the queue, exactly as a feed would.
    ///
    /// Used by tests and by the async shell; does not advance the clock.
    pub fn inject(&mut self, msg: Message) {
        self.queue.push(msg);
    }

    /// Drains fills produced since the last call, in production order.
    ///
    /// Fills the engine already observed during a run come first, in the
    /// order it saw them, followed by whatever the sink has produced since.
    /// Calling it twice in a row returns an empty second batch.
    pub fn drain_fills(&mut self) -> Result<Vec<Trade>> {
        self.pull_fills()?;
        Ok(std::mem::take(&mut self.pending_fills))
    }

    fn pull_fills(&mut self) -> Result<Vec<Trade>> {
        let Some(execution) = self.execution.as_deref_mut() else {
            return Ok(Vec::new());
        };
        let fills = execution.drain_fills()?;
        self.pending_fills.extend(fills.iter().cloned());
        Ok(fills)
    }

    fn acknowledge_fills(&mut self) -> Result<()> {
        for fill in self.pull_fills()? {
            let order_id = fill.order_id().clone();
            let quantity = fill.quantity();
            let price = fill.price();
            let ts = self.clock.now();
            self.audit.record(AuditKind::FillProduced {
                order_id: order_id.as_str().to_string(),
                quantity,
                price,
                ts_event: ts.as_u64(),
            });
            self.queue.push_event(
                Event::OrderFilled {
                    order_id,
                    last_qty: quantity,
                    last_px: price,
                    ts_event: ts,
                },
                ts,
            );
        }
        Ok(())
    }

    fn submit(&mut self, order: Order) -> Result<()> {
        let order_id = order.order_id().as_str().to_string();
        if !self.trading_state.accepts_orders() {
            self.audit.record(AuditKind::OrderRejected {
                order_id,
                reason: "trading halted".to_string(),
            });
            return Ok(());
        }
        let Some(execution) = self.execution.as_deref_mut() else {
            self.audit.record(AuditKind::OrderRejected {
                order_id,
                reason: "no execution attached".to_string(),
            });
            return Ok(());
        };
        let instrument = order.instrument_id().to_string();
        let side = side_label(order.side());
        execution.submit(order)?;
        self.audit.record(AuditKind::OrderSubmitted {
            order_id,
            instrument,
            side,
        });
        Ok(())
    }

    fn cancel(&mut self, id: OrderId) -> Result<()> {
        let order_id = id.as_str().to_string();
        if let Some(execution) = self.execution.as_deref_mut() {
            execution.cancel(&order_id)?;
        }
        self.audit.record(AuditKind::OrderCancelled { order_id });
        Ok(())
    }

    fn apply_output(&mut self, output: EngineOutput) -> Result<()> {
        match output {
            EngineOutput::None => {}
            EngineOutput::Orders(orders) => {
                for order in orders {
                    self.submit(order)?;
                }
            }
            EngineOutput::Cancels(ids) => {
                for id in ids {
                    self.cancel(id)?;
                }
            }
            EngineOutput::StateChange(next) => {
                if self.trading_state.can_transition_to(next) {
                    let from = self.trading_state;
                    self.trading_state = next;
                    self.audit
                        .record(AuditKind::StateChanged { from, to: next });
                }
            }
        }
        Ok(())
    }

    /// Runs the engine to completion against the given feed.
    ///
    /// Each iteration pulls up to `batch_size` events from the feed into the
    /// queue, then pops the earliest and dispatches it: the clock advances to
    /// the message's `ts_event`, the dispatch is audited, and each handler in
    /// registration order is asked for an [`EngineOutput`] whose commands are
    /// applied before the next handler runs.
    ///
    /// After every handler the engine drains the execution sink and turns each
    /// fill into an [`Event::OrderFilled`] message stamped with the kernel
    /// clock, so the command/ack loop is closed in one queue and a replay
    /// stays deterministic. That loop needs no iteration cap: `drain_fills`
    /// takes the sink's buffer, so a handler that submits nothing produces no
    /// fills and therefore no new messages.
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
                    self.audit.record(AuditKind::EventDispatched {
                        ts_event: ts_event.as_u64(),
                    });
                    for i in 0..self.handlers.len() {
                        let output = self.handlers[i].on_event(msg.event(), msg.ts_init())?;
                        self.apply_output(output)?;
                        self.acknowledge_fills()?;
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

fn side_label(side: OrderSide) -> String {
    match side {
        OrderSide::Buy => "buy".to_string(),
        OrderSide::Sell => "sell".to_string(),
        OrderSide::NoOrderSide => "no_order_side".to_string(),
        _ => "other".to_string(),
    }
}
