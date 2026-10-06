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
    started: bool,
    finished: bool,
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
            started: false,
            finished: false,
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

    /// Number of messages waiting in the queue.
    pub fn pending(&self) -> usize {
        self.queue.len()
    }

    /// Runs `on_start` for every handler. Idempotent: a second call is a no-op.
    ///
    /// [`Engine::run`] calls this for you. A driver that injects messages
    /// incrementally — the async shell, or anything else that owns its own
    /// loop — calls it instead of `run`, then dispatches with
    /// [`Engine::pump`] and closes the lifecycle with [`Engine::finish`].
    ///
    /// Each handler's `on_start` runs at most once: the lifecycle is marked
    /// started before the handlers are called, so a handler that fails
    /// half-way through the fan-out is not started a second time by a retry.
    pub fn start(&mut self) -> Result<()> {
        if self.started {
            return Ok(());
        }
        self.started = true;
        for h in &mut self.handlers {
            h.on_start()?;
        }
        Ok(())
    }

    /// Dispatches one queued message, if any. Returns false when the queue is empty.
    ///
    /// This is the one place a queued message becomes a handler call: the
    /// clock is advanced to the message's `ts_event`, the dispatch is audited,
    /// and each handler in registration order is asked for an
    /// [`EngineOutput`] whose commands are applied before the next handler
    /// runs. After every handler the execution sink is drained and each fill
    /// is turned back into a queued [`Event::OrderFilled`], so a caller that
    /// pumps until `false` closes the command/ack loop in one queue.
    ///
    /// Errors are returned verbatim: an event earlier than one already
    /// dispatched yields [`AlgoError::ClockRegression`](crate::AlgoError::ClockRegression).
    pub fn pump(&mut self) -> Result<bool> {
        let Some(msg) = self.queue.pop() else {
            return Ok(false);
        };
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
        Ok(true)
    }

    /// Drains the queue via [`Engine::pump`], then runs `on_stop` for every handler. Idempotent.
    ///
    /// [`Engine::run`] calls this for you. A driver that injected messages
    /// incrementally calls it instead: draining before stopping is what makes
    /// the audit trail complete, because a message that was accepted into the
    /// queue is dispatched before any handler is told the run is over.
    ///
    /// Each handler's `on_stop` runs at most once, on the same terms as
    /// [`Engine::start`]: a handler that fails half-way through the fan-out is
    /// not stopped a second time by a retry. The queue is drained either way.
    pub fn finish(&mut self) -> Result<()> {
        while self.pump()? {}
        if self.finished {
            return Ok(());
        }
        self.finished = true;
        for h in &mut self.handlers {
            h.on_stop()?;
        }
        Ok(())
    }

    /// Sets the trading state, honouring [`TradingState::can_transition_to`], and records
    /// [`AuditKind::StateChanged`] when it actually changes. Returns the previous state.
    ///
    /// A transition to the state the engine is already in is not a change and
    /// records nothing; a transition [`TradingState::can_transition_to`]
    /// refuses leaves the state alone and records nothing. Both are still
    /// reported through the returned previous state.
    ///
    /// This is the only implementation of the state transition: a handler's
    /// [`EngineOutput::StateChange`] and the async shell's operator command
    /// both go through it, so they cannot drift apart.
    pub fn set_trading_state(&mut self, next: TradingState) -> TradingState {
        let previous = self.trading_state;
        if previous != next && previous.can_transition_to(next) {
            self.trading_state = next;
            self.audit.record(AuditKind::StateChanged {
                from: previous,
                to: next,
            });
        }
        previous
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
            execution.cancel(&order_id, self.clock.now())?;
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
                self.set_trading_state(next);
            }
        }
        Ok(())
    }

    /// Runs the engine to completion against the given feed.
    ///
    /// Each iteration pulls up to `batch_size` events from the feed into the
    /// queue, then dispatches the earliest one through [`Engine::pump`]: the
    /// clock advances to the message's `ts_event`, the dispatch is audited, and
    /// each handler in registration order is asked for an [`EngineOutput`]
    /// whose commands are applied before the next handler runs.
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
    ///
    /// This is [`Engine::start`], the pull/dispatch loop, then
    /// [`Engine::finish`], and nothing else: the three phases are the same
    /// public steps a driver that owns its own loop takes, so a run driven by
    /// `run` and a run driven by `start`/`pump`/`finish` dispatch identically.
    pub fn run(&mut self, feed: &mut dyn DataFeed) -> Result<()> {
        self.start()?;

        loop {
            for _ in 0..self.batch_size {
                match feed.next()? {
                    Some(msg) => self.queue.push(msg),
                    None => break,
                }
            }

            if !self.pump()? {
                break;
            }
        }

        self.finish()
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
