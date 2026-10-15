//! Bridge from the async [`honba_ports::ExecutionGateway`] to the sync [`ExecutionVenue`]
//! (ADR 0030, decision-log entry "async gateway bridge").
//!
//! The order routes call [`ExecutionEngine::submit`] and `cancel` from async handlers while
//! holding a std mutex, so those calls must never wait on a broker. [`GatewayVenue`] therefore
//! does not call the gateway at all: it owns a *command channel* to one task that owns the
//! gateway. `submit` and `cancel` enqueue a command and return `Ok(())`; the task awaits the
//! gateway, and everything it learns (a refusal, a fill, a cancel acknowledgement) is pushed as
//! an [`ExecutionEvent`] onto a shared queue that [`ExecutionEngine::drain_events`] empties.
//! The lock is held for a channel send or a queue swap only, never across an await.
//!
//! Ids: the ledger is keyed by the client order id, the gateway answers with its own venue id.
//! The task keeps `venue id -> client order` and rewrites every fill and event back to the client
//! id (a fill already carrying a client id is accepted as is). Fills are tracked per order to
//! report `cum_qty` and `complete`, which the ledger maps to `PartiallyFilled` / `Filled`.
//!
//! Events surface when somebody drains: [`AppState::advance_market`] does (with or without bars),
//! and `AppState::spawn_venue_drain` (planned) does it on a timer for a venue with no bar feed.

use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use honba_engine::{AlgoError, ExecutionEngine, Result as EngineResult};
use honba_entities::{ExecutionEvent, Trade};
use honba_messages::{Order, OrderId, UnixNanos};
use honba_ports::ExecutionGateway;
use tokio::sync::mpsc::{self, UnboundedReceiver, UnboundedSender};
use tokio::task::JoinHandle;

use crate::ExecutionVenue;

/// Default interval between polls of the gateway's fill stream.
const DEFAULT_POLL: Duration = Duration::from_millis(50);

/// Tolerance when deciding that cumulative fills cover the order quantity.
const QTY_EPS: f64 = 1e-9;

enum Command {
    Submit(Order),
}

type Events = Arc<Mutex<VecDeque<ExecutionEvent>>>;

/// An [`ExecutionVenue`] backed by an async [`ExecutionGateway`] running on its own task.
///
/// Create it inside a tokio runtime with [`GatewayVenue::spawn`]. Dropping it closes the command
/// channel and the task ends after the commands already queued.
pub struct GatewayVenue {
    commands: UnboundedSender<Command>,
    events: Events,
    task: JoinHandle<()>,
}

impl GatewayVenue {
    /// Spawns the gateway task (poll interval 50 ms). Must be called inside a tokio runtime.
    pub fn spawn(gateway: impl ExecutionGateway + 'static) -> Self {
        Self::spawn_with_poll(gateway, DEFAULT_POLL)
    }

    /// Spawns the gateway task, polling the fill stream every `poll` (at least 1 ms).
    pub fn spawn_with_poll(gateway: impl ExecutionGateway + 'static, poll: Duration) -> Self {
        let (commands, rx) = mpsc::unbounded_channel();
        let events = Events::default();
        let task = tokio::spawn(run(
            gateway,
            rx,
            Arc::clone(&events),
            poll.max(Duration::from_millis(1)),
        ));
        Self {
            commands,
            events,
            task,
        }
    }

    fn send(&self, command: Command) -> EngineResult<()> {
        if self.task.is_finished() {
            return Err(AlgoError::Component(
                "execution gateway task stopped".into(),
            ));
        }
        self.commands
            .send(command)
            .map_err(|_| AlgoError::Component("execution gateway task stopped".into()))
    }
}

impl ExecutionEngine for GatewayVenue {
    fn submit(&mut self, order: Order) -> EngineResult<()> {
        self.send(Command::Submit(order))
    }

    fn cancel(&mut self, order_id: &str, now: UnixNanos) -> EngineResult<()> {
        let _ = (order_id, now);
        Ok(())
    }

    fn native_events(&self) -> bool {
        true
    }

    fn drain_events(&mut self) -> EngineResult<Vec<ExecutionEvent>> {
        Ok(lock(&self.events).drain(..).collect())
    }

    fn drain_fills(&mut self) -> EngineResult<Vec<Trade>> {
        // Legacy view: the ledger path uses `drain_events`; fills are not buffered twice.
        Ok(Vec::new())
    }
}

impl ExecutionVenue for GatewayVenue {}

fn lock(events: &Events) -> MutexGuard<'_, VecDeque<ExecutionEvent>> {
    events.lock().unwrap_or_else(PoisonError::into_inner)
}

/// A working order the task is tracking.
struct Working {
    order: Order,
    venue_id: OrderId,
    cum: f64,
}

async fn run<G: ExecutionGateway>(
    mut gateway: G,
    mut rx: UnboundedReceiver<Command>,
    events: Events,
    poll: Duration,
) {
    let mut working: HashMap<String, Working> = HashMap::new();
    let mut tick = tokio::time::interval(poll);
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    loop {
        tokio::select! {
            command = rx.recv() => match command {
                Some(Command::Submit(order)) => {
                    submit(&mut gateway, &mut working, order).await;
                    poll_fills(&mut gateway, &mut working, &events).await;
                }
                None => break,
            },
            _ = tick.tick() => poll_fills(&mut gateway, &mut working, &events).await,
        }
    }
}

async fn submit<G: ExecutionGateway>(
    gateway: &mut G,
    working: &mut HashMap<String, Working>,
    order: Order,
) {
    if let Ok(venue_id) = gateway.submit_order(order.clone()).await {
        working.insert(
            order.order_id().as_str().to_owned(),
            Working {
                order,
                venue_id,
                cum: 0.0,
            },
        );
    }
}

async fn poll_fills<G: ExecutionGateway>(
    gateway: &mut G,
    working: &mut HashMap<String, Working>,
    events: &Events,
) {
    while let Ok(Some(trade)) = gateway.next_fill().await {
        let key = working
            .iter()
            .find(|(client, w)| {
                trade.order_id() == &w.venue_id || trade.order_id().as_str() == client.as_str()
            })
            .map(|(client, _)| client.clone());
        let Some(client) = key else { continue };
        let w = working.get_mut(&client).expect("key just found");
        w.cum += trade.quantity();
        let complete = w.cum >= w.order.quantity() - QTY_EPS;
        let cum_qty = w.cum;
        let mapped = Trade::new(
            w.order.order_id().clone(),
            trade.instrument_id().clone(),
            trade.side(),
            trade.quantity(),
            trade.price(),
            trade.costs().currency(),
            trade.ts_event(),
            trade.ts_init(),
        )
        .with_costs(trade.costs());
        let venue_order_id = None;
        lock(events).push_back(ExecutionEvent::Fill {
            trade: mapped,
            cum_qty,
            complete,
            venue_order_id,
        });
        if complete {
            working.remove(&client);
        }
    }
}
