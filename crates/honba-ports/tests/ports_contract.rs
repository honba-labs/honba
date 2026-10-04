//! Contract tests for the six port traits through the public API only.
//!
//! Every fake lives in this file: the crate under test ships no I/O.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use async_trait::async_trait;
use honba_entities::{Currency, Instrument, InstrumentKind, Trade};
use honba_messages::{
    Event, InstrumentId, Message, Order, OrderId, OrderSide, OrderType, TimeInForce, UnixNanos,
};
use honba_ports::{
    Clock, ExecutionGateway, InstrumentMaster, MarketDataFeed, PortError, PortResult, SecretStore,
    Sink,
};
use honba_testing::fixtures::instrument;

fn reliance() -> InstrumentId {
    instrument("RELIANCE")
}

fn message(ts: u64) -> Message {
    let t = UnixNanos::from_u64(ts);
    Message::new(
        Event::OrderCancelled {
            order_id: OrderId::new(format!("O-{ts}")),
            ts_event: t,
        },
        t,
    )
}

fn buy_order(id: &str) -> Order {
    Order::new(
        OrderId::new(id),
        reliance(),
        OrderSide::Buy,
        OrderType::Limit,
        10.0,
        Some(100.0),
        TimeInForce::Day,
        UnixNanos::from_u64(1),
        UnixNanos::from_u64(1),
    )
}

struct VecFeed {
    items: VecDeque<Message>,
    subscribed: Vec<InstrumentId>,
    unsubscribed: Vec<InstrumentId>,
}

impl VecFeed {
    fn new(items: Vec<Message>) -> Self {
        Self {
            items: items.into(),
            subscribed: Vec::new(),
            unsubscribed: Vec::new(),
        }
    }
}

#[async_trait]
impl MarketDataFeed for VecFeed {
    async fn subscribe(&mut self, symbols: &[InstrumentId]) -> PortResult<()> {
        self.subscribed.extend_from_slice(symbols);
        Ok(())
    }

    async fn unsubscribe(&mut self, symbols: &[InstrumentId]) -> PortResult<()> {
        self.unsubscribed.extend_from_slice(symbols);
        Ok(())
    }

    async fn next(&mut self) -> PortResult<Option<Message>> {
        Ok(self.items.pop_front())
    }
}

struct VecSink {
    written: Arc<Mutex<Vec<Message>>>,
    flushes: usize,
}

#[async_trait]
impl Sink for VecSink {
    async fn write(&mut self, msg: Message) -> PortResult<()> {
        self.written.lock().expect("sink lock").push(msg);
        Ok(())
    }

    async fn flush(&mut self) -> PortResult<()> {
        self.flushes += 1;
        Ok(())
    }
}

struct FixedClock {
    now: AtomicU64,
}

impl FixedClock {
    fn new(start: u64) -> Self {
        Self {
            now: AtomicU64::new(start),
        }
    }
}

#[async_trait]
impl Clock for FixedClock {
    async fn now(&self) -> UnixNanos {
        UnixNanos::from_u64(self.now.load(Ordering::SeqCst))
    }

    async fn sleep(&self, dur: Duration) -> PortResult<()> {
        let nanos = u64::try_from(dur.as_nanos()).expect("duration fits in u64");
        self.now.fetch_add(nanos, Ordering::SeqCst);
        Ok(())
    }
}

struct OneInstrumentMaster {
    instrument: Instrument,
}

#[async_trait]
impl InstrumentMaster for OneInstrumentMaster {
    async fn get_instrument(&self, id: &InstrumentId) -> PortResult<Option<Instrument>> {
        Ok((id == self.instrument.id()).then(|| self.instrument.clone()))
    }

    async fn list_instruments(&self) -> PortResult<Vec<Instrument>> {
        Ok(vec![self.instrument.clone()])
    }
}

struct StubGateway {
    submitted: Vec<OrderId>,
    cancelled: Vec<OrderId>,
    modified: Vec<(OrderId, f64, Option<f64>)>,
    pending_fill: Option<(OrderId, Order)>,
}

impl StubGateway {
    fn new() -> Self {
        Self {
            submitted: Vec::new(),
            cancelled: Vec::new(),
            modified: Vec::new(),
            pending_fill: None,
        }
    }
}

#[async_trait]
impl ExecutionGateway for StubGateway {
    async fn submit_order(&mut self, order: Order) -> PortResult<OrderId> {
        let id = OrderId::new(format!("STUB-{}", self.submitted.len() + 1));
        self.submitted.push(id.clone());
        if self.pending_fill.is_none() {
            self.pending_fill = Some((id.clone(), order));
        }
        Ok(id)
    }

    async fn cancel_order(&mut self, id: OrderId) -> PortResult<()> {
        self.cancelled.push(id);
        Ok(())
    }

    async fn modify_order(
        &mut self,
        id: OrderId,
        new_qty: f64,
        new_price: Option<f64>,
    ) -> PortResult<()> {
        self.modified.push((id, new_qty, new_price));
        Ok(())
    }

    async fn next_fill(&mut self) -> PortResult<Option<Trade>> {
        Ok(self.pending_fill.take().map(|(id, order)| {
            Trade::new(
                id,
                order.instrument_id().clone(),
                order.side(),
                order.quantity(),
                100.0,
                UnixNanos::from_u64(2),
                UnixNanos::from_u64(2),
            )
        }))
    }
}

struct MapSecretStore {
    secrets: std::collections::HashMap<String, String>,
}

#[async_trait]
impl SecretStore for MapSecretStore {
    async fn get_secret(&self, key: &str) -> PortResult<Option<String>> {
        Ok(self.secrets.get(key).cloned())
    }
}

#[tokio::test]
async fn feed_subscribes_then_drains_to_none() {
    let mut feed = VecFeed::new(vec![message(1), message(2)]);

    feed.subscribe(&[reliance()]).await.unwrap();
    feed.unsubscribe(&[reliance()]).await.unwrap();

    let mut drained = Vec::new();
    while let Some(msg) = feed.next().await.unwrap() {
        drained.push(msg);
    }

    assert_eq!(drained, vec![message(1), message(2)]);
    assert_eq!(feed.subscribed, vec![reliance()]);
    assert_eq!(feed.unsubscribed, vec![reliance()]);
    assert!(feed.next().await.unwrap().is_none(), "stays exhausted");
}

#[tokio::test]
async fn sink_writes_in_order_and_flushes() {
    let written = Arc::new(Mutex::new(Vec::new()));
    let mut sink = VecSink {
        written: Arc::clone(&written),
        flushes: 0,
    };

    sink.write(message(1)).await.unwrap();
    sink.write(message(2)).await.unwrap();
    sink.flush().await.unwrap();
    sink.flush().await.unwrap();

    assert_eq!(sink.flushes, 2);
    let written = written.lock().expect("readback lock");
    assert_eq!(written.len(), 2);
    assert_eq!(written[0], message(1));
    assert_eq!(written[1], message(2));
}

#[tokio::test]
async fn clock_advances_across_sleep() {
    let clock = FixedClock::new(1_000);

    assert_eq!(clock.now().await, UnixNanos::from_u64(1_000));
    clock.sleep(Duration::from_millis(5)).await.unwrap();
    assert_eq!(clock.now().await, UnixNanos::from_u64(1_000 + 5_000_000));
    assert_eq!(clock.now().await, UnixNanos::from_u64(1_000 + 5_000_000));
}

#[tokio::test]
async fn master_looks_up_known_and_unknown_instruments() {
    let known = Instrument::new(reliance(), InstrumentKind::Equity, Currency::Inr, 1.0, 0.05);
    let master = OneInstrumentMaster {
        instrument: known.clone(),
    };

    assert_eq!(
        master.get_instrument(&reliance()).await.unwrap(),
        Some(known.clone())
    );
    assert_eq!(master.list_instruments().await.unwrap(), vec![known]);
    assert_eq!(
        master.get_instrument(&instrument("TCS")).await.unwrap(),
        None
    );
}

#[tokio::test]
async fn gateway_submits_cancels_modifies_and_fills_once() {
    let mut gateway = StubGateway::new();

    let first = gateway.submit_order(buy_order("O-1")).await.unwrap();
    let second = gateway.submit_order(buy_order("O-2")).await.unwrap();
    gateway.cancel_order(first.clone()).await.unwrap();
    gateway
        .modify_order(second.clone(), 5.0, Some(101.5))
        .await
        .unwrap();

    assert_eq!(first, OrderId::new("STUB-1"));
    assert_eq!(second, OrderId::new("STUB-2"));
    assert_eq!(gateway.cancelled, vec![first.clone()]);
    assert_eq!(gateway.modified, vec![(second.clone(), 5.0, Some(101.5))]);

    let fill = gateway.next_fill().await.unwrap().expect("one fill");
    assert_eq!(fill.order_id(), &first);
    assert_eq!(fill.quantity(), 10.0);
    assert_eq!(fill.price(), 100.0);
    assert!(
        gateway.next_fill().await.unwrap().is_none(),
        "fills exhausted"
    );
}

#[tokio::test]
async fn secret_store_returns_present_and_absent_keys() {
    let mut secrets = std::collections::HashMap::new();
    secrets.insert("broker.api_key".to_string(), "s3cret".to_string());
    let store = MapSecretStore { secrets };

    assert_eq!(
        store.get_secret("broker.api_key").await.unwrap(),
        Some("s3cret".into())
    );
    assert_eq!(store.get_secret("broker.missing").await.unwrap(), None);
}

#[tokio::test]
async fn all_six_ports_fit_in_a_registry_struct() {
    let written = Arc::new(Mutex::new(Vec::new()));
    let mut registry = Registry {
        clock: Arc::new(FixedClock::new(7)),
        feed: Box::new(VecFeed::new(vec![message(1)])),
        gateway: Box::new(StubGateway::new()),
        master: Arc::new(OneInstrumentMaster {
            instrument: Instrument::new(
                reliance(),
                InstrumentKind::Equity,
                Currency::Inr,
                1.0,
                0.05,
            ),
        }),
        sink: Box::new(VecSink {
            written: Arc::clone(&written),
            flushes: 0,
        }),
        secrets: Arc::new(MapSecretStore {
            secrets: std::collections::HashMap::new(),
        }),
    };

    assert_eq!(registry.clock.now().await, UnixNanos::from_u64(7));
    assert_eq!(registry.master.list_instruments().await.unwrap().len(), 1);
    assert_eq!(registry.secrets.get_secret("any").await.unwrap(), None);
    assert_eq!(
        registry
            .feed
            .next()
            .await
            .unwrap()
            .expect("one queued message"),
        message(1)
    );
    registry.sink.write(message(9)).await.unwrap();
    registry.sink.flush().await.unwrap();
    assert_eq!(written.lock().expect("readback lock").len(), 1);
    assert_eq!(
        registry
            .gateway
            .submit_order(buy_order("O-1"))
            .await
            .unwrap(),
        OrderId::new("STUB-1")
    );
}

struct Registry {
    clock: Arc<dyn Clock>,
    feed: Box<dyn MarketDataFeed>,
    gateway: Box<dyn ExecutionGateway>,
    master: Arc<dyn InstrumentMaster>,
    sink: Box<dyn Sink>,
    secrets: Arc<dyn SecretStore>,
}

#[test]
fn port_error_is_retryable_only_for_transport_failures() {
    assert!(PortError::Timeout.is_retryable());
    assert!(!PortError::Rejected {
        code: "E_MARGIN".into(),
        message: "insufficient funds".into(),
    }
    .is_retryable());
}
