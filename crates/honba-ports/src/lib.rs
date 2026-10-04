use async_trait::async_trait;
use honba_entities::Instrument;
use honba_messages::InstrumentId;
use honba_messages::{Bar, Order, OrderId, QuoteTick, TradeTick};
use std::time::Duration;

#[async_trait]
pub trait Clock: Send + Sync {
    async fn now_ns(&self) -> i64;
    async fn sleep(&self, d: Duration) -> anyhow::Result<()>;
}

#[async_trait]
pub trait MarketDataFeed: Send + Sync {
    async fn subscribe(&mut self, symbols: &[InstrumentId]) -> anyhow::Result<()>;
    async fn unsubscribe(&mut self, symbols: &[InstrumentId]) -> anyhow::Result<()>;
    async fn next_bar(&mut self) -> anyhow::Result<Option<Bar>>;
    async fn next_quote(&mut self) -> anyhow::Result<Option<QuoteTick>>;
    async fn next_trade(&mut self) -> anyhow::Result<Option<TradeTick>>;
}

#[async_trait]
pub trait ExecutionGateway: Send + Sync {
    async fn submit_order(&mut self, order: Order) -> anyhow::Result<OrderId>;
    async fn cancel_order(&mut self, id: OrderId) -> anyhow::Result<()>;
    async fn modify_order(
        &mut self,
        id: OrderId,
        new_qty: f64,
        new_price: Option<f64>,
    ) -> anyhow::Result<()>;
    async fn next_fill(&mut self) -> anyhow::Result<Option<honba_entities::Trade>>;
}

#[async_trait]
pub trait InstrumentMaster: Send + Sync {
    async fn get_instrument(&self, id: &InstrumentId) -> anyhow::Result<Option<Instrument>>;
    async fn list_instruments(&self) -> anyhow::Result<Vec<Instrument>>;
}

#[async_trait]
pub trait Sink: Send + Sync {
    async fn write(&mut self, msg: honba_messages::Message) -> anyhow::Result<()>;
}

#[async_trait]
pub trait SecretStore: Send + Sync {
    async fn get_secret(&self, key: &str) -> anyhow::Result<Option<String>>;
}
