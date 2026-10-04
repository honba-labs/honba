//! Unit tests for the port traits: object safety and thread-safety bounds.

use std::future::Future;
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use honba_entities::{Instrument, Trade};
use honba_messages::{
    Event, Exchange, InstrumentId, Message, Order, OrderId, OrderSide, OrderType, TimeInForce,
    UnixNanos,
};

use crate::{
    Clock, ExecutionGateway, InstrumentMaster, MarketDataFeed, PortResult, SecretStore, Sink,
};

fn assert_send<T: Send + ?Sized>() {}
fn assert_send_sync<T: Send + Sync + ?Sized>() {}
fn assert_send_future<F: Future + Send>(_: F) {}

fn fake_order() -> Order {
    Order::new(
        OrderId::new("fake"),
        InstrumentId::new("X", Exchange::new("TEST")),
        OrderSide::Buy,
        OrderType::Limit,
        10.0,
        Some(100.0),
        TimeInForce::Day,
        UnixNanos::default(),
        UnixNanos::default(),
    )
}

fn take_clock(_: &dyn Clock) {}
fn take_feed(_: &dyn MarketDataFeed) {}
fn take_gateway(_: &dyn ExecutionGateway) {}
fn take_master(_: &dyn InstrumentMaster) {}
fn take_sink(_: Box<dyn Sink>) {}
fn take_secrets(_: &dyn SecretStore) {}

struct Fakes;

#[async_trait]
impl Clock for Fakes {
    async fn now(&self) -> UnixNanos {
        UnixNanos::default()
    }

    async fn sleep(&self, _dur: Duration) -> PortResult<()> {
        Ok(())
    }
}

#[async_trait]
impl MarketDataFeed for Fakes {
    async fn subscribe(&mut self, _symbols: &[InstrumentId]) -> PortResult<()> {
        Ok(())
    }

    async fn unsubscribe(&mut self, _symbols: &[InstrumentId]) -> PortResult<()> {
        Ok(())
    }

    async fn next(&mut self) -> PortResult<Option<Message>> {
        Ok(None)
    }
}

#[async_trait]
impl ExecutionGateway for Fakes {
    async fn submit_order(&mut self, _order: Order) -> PortResult<OrderId> {
        Ok(OrderId::new("fake"))
    }

    async fn cancel_order(&mut self, _id: OrderId) -> PortResult<()> {
        Ok(())
    }

    async fn modify_order(
        &mut self,
        _id: OrderId,
        _new_qty: f64,
        _new_price: Option<f64>,
    ) -> PortResult<()> {
        Ok(())
    }

    async fn next_fill(&mut self) -> PortResult<Option<Trade>> {
        Ok(None)
    }
}

#[async_trait]
impl InstrumentMaster for Fakes {
    async fn get_instrument(&self, _id: &InstrumentId) -> PortResult<Option<Instrument>> {
        Ok(None)
    }

    async fn list_instruments(&self) -> PortResult<Vec<Instrument>> {
        Ok(Vec::new())
    }
}

#[async_trait]
impl Sink for Fakes {
    async fn write(&mut self, _msg: Message) -> PortResult<()> {
        Ok(())
    }

    async fn flush(&mut self) -> PortResult<()> {
        Ok(())
    }
}

#[async_trait]
impl SecretStore for Fakes {
    async fn get_secret(&self, _key: &str) -> PortResult<Option<String>> {
        Ok(None)
    }
}

#[test]
fn traits_are_object_safe() {
    let fakes = Fakes;
    take_clock(&fakes);
    take_feed(&fakes);
    take_gateway(&fakes);
    take_master(&fakes);
    take_secrets(&fakes);
    take_sink(Box::new(Fakes));
}

#[test]
fn traits_can_be_held_in_registry_containers() {
    let _clock: Arc<dyn Clock> = Arc::new(Fakes);
    let _master: Arc<dyn InstrumentMaster> = Arc::new(Fakes);
    let _secrets: Arc<dyn SecretStore> = Arc::new(Fakes);
    let _feed: Box<dyn MarketDataFeed> = Box::new(Fakes);
    let _gateway: Box<dyn ExecutionGateway> = Box::new(Fakes);
    let _sink: Box<dyn Sink> = Box::new(Fakes);

    assert_send_sync::<Arc<dyn Clock>>();
    assert_send_sync::<Arc<dyn InstrumentMaster>>();
    assert_send_sync::<Arc<dyn SecretStore>>();
    assert_send::<Box<dyn MarketDataFeed>>();
    assert_send::<Box<dyn ExecutionGateway>>();
    assert_send::<Box<dyn Sink>>();
}

#[test]
fn read_only_ports_are_send_and_sync() {
    assert_send_sync::<dyn Clock>();
    assert_send_sync::<dyn InstrumentMaster>();
    assert_send_sync::<dyn SecretStore>();
}

#[test]
fn mutable_ports_are_send() {
    assert_send::<dyn MarketDataFeed>();
    assert_send::<dyn ExecutionGateway>();
    assert_send::<dyn Sink>();
}

#[test]
fn futures_returned_by_ports_are_send() {
    let mut fakes = Fakes;
    assert_send_future(fakes.now());
    assert_send_future(fakes.sleep(Duration::from_millis(1)));
    assert_send_future(fakes.subscribe(&[]));
    assert_send_future(fakes.unsubscribe(&[]));
    assert_send_future(fakes.next());
    assert_send_future(fakes.get_instrument(&InstrumentId::new("X", Exchange::new("TEST"))));
    assert_send_future(fakes.list_instruments());
    assert_send_future(fakes.get_secret("key"));
    assert_send_future(fakes.write(Message::new(
        Event::OrderCancelled {
            order_id: OrderId::new("fake"),
            ts_event: UnixNanos::default(),
        },
        UnixNanos::default(),
    )));
    assert_send_future(fakes.flush());
    assert_send_future(fakes.submit_order(fake_order()));
    assert_send_future(fakes.modify_order(OrderId::new("fake"), 5.0, None));
    assert_send_future(fakes.next_fill());
    assert_send_future(fakes.cancel_order(OrderId::new("fake")));
}
