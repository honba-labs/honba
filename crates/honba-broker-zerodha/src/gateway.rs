//! [`ExecutionGateway`] for Zerodha, built on [`KiteClient`].
//!
//! Orders keep the caller's [`OrderId`]; the gateway remembers the Kite order id each one was
//! placed under. Fills are read from `GET /trades`, mapped to [`Trade`], de-duplicated by Kite
//! trade id and handed out one per [`ExecutionGateway::next_fill`] call. Kite trades whose order
//! this gateway did not place are ignored. `ts_init` comes from an injected function so tests
//! stay deterministic.

use std::collections::{HashMap, HashSet, VecDeque};

use async_trait::async_trait;
use honba_entities::{Currency, Trade};
use honba_messages::{InstrumentId, Order, OrderId, OrderSide, OrderType, UnixNanos};
use honba_ports::{ExecutionGateway, PortError, PortResult};

use crate::client::{KiteClient, PlaceOrder};
use crate::mapping::{kite_tag, order_side_from_kite, parse_kite_timestamp, Product};
use crate::transport::HttpTransport;
use crate::wire::TradeRecord;

const VARIETY: &str = "regular";

/// Injected source of `ts_init`.
pub type NowFn = Box<dyn Fn() -> UnixNanos + Send>;

struct Known {
    venue_id: String,
    instrument: InstrumentId,
    side: OrderSide,
}

/// Zerodha implementation of the order-routing port.
pub struct ZerodhaGateway<T: HttpTransport> {
    client: KiteClient<T>,
    product: Product,
    now: NowFn,
    orders: HashMap<OrderId, Known>,
    by_venue: HashMap<String, OrderId>,
    emitted: HashSet<String>,
    queue: VecDeque<Trade>,
}

impl<T: HttpTransport> ZerodhaGateway<T> {
    /// Creates a gateway. `product` is applied to every order (the domain `Order` has none);
    /// `now` supplies `ts_init` for emitted trades.
    pub fn new(client: KiteClient<T>, product: Product, now: NowFn) -> Self {
        Self {
            client,
            product,
            now,
            orders: HashMap::new(),
            by_venue: HashMap::new(),
            emitted: HashSet::new(),
            queue: VecDeque::new(),
        }
    }

    /// The underlying REST client.
    pub fn client(&self) -> &KiteClient<T> {
        &self.client
    }

    fn venue_id(&self, id: &OrderId) -> PortResult<String> {
        self.orders
            .get(id)
            .map(|k| k.venue_id.clone())
            .ok_or_else(|| PortError::InvalidRequest(format!("unknown order id {}", id.as_str())))
    }

    fn to_trade(&self, rec: &TradeRecord) -> Option<Trade> {
        let order_id = self.by_venue.get(rec.order_id.as_deref()?)?;
        let known = self.orders.get(order_id)?;
        let qty = rec.quantity.filter(|q| *q > 0)?;
        let price = rec.average_price.filter(|p| p.is_finite() && *p > 0.0)?;
        let ts_event = parse_kite_timestamp(rec.fill_timestamp.as_deref()?).ok()?;
        let side = rec
            .transaction_type
            .as_deref()
            .and_then(order_side_from_kite)
            .unwrap_or(known.side);
        Some(Trade::new(
            order_id.clone(),
            known.instrument.clone(),
            side,
            qty as f64,
            price,
            Currency::Inr,
            ts_event,
            (self.now)(),
        ))
    }
}

fn required(value: Option<f64>, what: &str) -> PortResult<f64> {
    match value {
        Some(v) if v.is_finite() && v > 0.0 => Ok(v),
        _ => Err(PortError::InvalidRequest(format!("{what} required"))),
    }
}

fn whole_qty(q: f64) -> PortResult<u64> {
    if q.is_finite() && q > 0.0 && q.fract() == 0.0 && q <= u64::MAX as f64 {
        Ok(q as u64)
    } else {
        Err(PortError::InvalidRequest(format!(
            "quantity must be a positive whole number, got {q}"
        )))
    }
}

#[async_trait]
impl<T: HttpTransport> ExecutionGateway for ZerodhaGateway<T> {
    async fn submit_order(&mut self, order: Order) -> PortResult<OrderId> {
        if self.orders.contains_key(order.order_id()) {
            return Ok(order.order_id().clone());
        }
        let ty = order.order_type();
        let price = match ty {
            OrderType::Limit | OrderType::StopLimit => Some(required(order.price(), "price")?),
            _ => None,
        };
        let trigger_price = match ty {
            OrderType::StopMarket | OrderType::StopLimit => {
                Some(required(order.trigger_price(), "trigger price")?)
            }
            _ => None,
        };
        let req = PlaceOrder {
            variety: VARIETY.to_owned(),
            tradingsymbol: order.instrument_id().symbol().to_owned(),
            exchange: order.instrument_id().exchange().as_str().to_owned(),
            side: order.side(),
            order_type: ty,
            quantity: whole_qty(order.quantity())?,
            price,
            trigger_price,
            validity: order.time_in_force(),
            product: self.product,
            tag: kite_tag(order.order_id()),
        };
        let venue_id = self.client.place_order(&req).await?;
        let id = order.order_id().clone();
        self.by_venue.insert(venue_id.clone(), id.clone());
        self.orders.insert(
            id.clone(),
            Known {
                venue_id,
                instrument: order.instrument_id().clone(),
                side: order.side(),
            },
        );
        Ok(id)
    }

    async fn cancel_order(&mut self, id: OrderId) -> PortResult<()> {
        let venue = self.venue_id(&id)?;
        self.client.cancel_order(VARIETY, &venue).await.map(|_| ())
    }

    async fn modify_order(
        &mut self,
        id: OrderId,
        new_qty: f64,
        new_price: Option<f64>,
    ) -> PortResult<()> {
        let venue = self.venue_id(&id)?;
        let qty = whole_qty(new_qty)?;
        self.client
            .modify_order(VARIETY, &venue, Some(qty), new_price, None)
            .await
            .map(|_| ())
    }

    async fn next_fill(&mut self) -> PortResult<Option<Trade>> {
        if let Some(t) = self.queue.pop_front() {
            return Ok(Some(t));
        }
        let records = self.client.trades().await?;
        for rec in &records {
            if self.emitted.contains(&rec.trade_id) {
                continue;
            }
            if let Some(trade) = self.to_trade(rec) {
                self.emitted.insert(rec.trade_id.clone());
                self.queue.push_back(trade);
            }
        }
        Ok(self.queue.pop_front())
    }
}
