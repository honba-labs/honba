//! Execution engine that fills at the most recent bar's close.

use std::sync::{Arc, Mutex};

use honba_engine::{ExecutionEngine, Handler, Result};
use honba_entities::Trade;
use honba_messages::{Event, Order, OrderId, UnixNanos};

#[derive(Default)]
struct Inner {
    last_price: Option<f64>,
    fills: Vec<Trade>,
    next_ts: u64,
}

/// An execution engine that records the most recent bar close (as a
/// [`Handler`]) and fills every order at that price (as an
/// [`ExecutionEngine`]).
///
/// Cheap to clone — clones share the same underlying state. Register one
/// clone with the engine to observe bars, and pass another to the runner to
/// execute orders.
///
/// ```
/// use honba_sim::BarFillEngine;
/// use honba_engine::{ExecutionEngine, Handler};
/// use honba_messages::{
///     Bar, BarAggregation, BarSpecification, BarType, Event, InstrumentId,
///     Order, OrderId, OrderSide, OrderType, PriceType, TimeInForce,
///     UnixNanos, Venue,
/// };
///
/// let mut exec = BarFillEngine::new();
/// let bt = BarType::new(
///     InstrumentId::new("X", Venue::new("TEST")),
///     BarSpecification::new(1, BarAggregation::Minute, PriceType::Last),
/// );
/// let t = UnixNanos::from_u64(2);
/// let bar = Bar::new(bt, 100.0, 102.0, 99.0, 101.0, 1000.0, t, t);
/// exec.on_event(&Event::Bar(bar), t).unwrap();
///
/// // Last price observed from bar close is 101. Submit and drain.
/// let order = Order::new(
///     OrderId::new("O-1"),
///     InstrumentId::new("X", Venue::new("TEST")),
///     OrderSide::Buy, OrderType::Market, 5.0, None, TimeInForce::Day,
///     UnixNanos::from_u64(2), UnixNanos::from_u64(2),
/// );
/// exec.submit(order).unwrap();
/// let fills = exec.drain_fills().unwrap();
/// assert_eq!(fills.len(), 1);
/// assert_eq!(fills[0].price(), 101.0);
/// ```
#[derive(Clone, Default)]
pub struct BarFillEngine {
    inner: Arc<Mutex<Inner>>,
}

impl BarFillEngine {
    /// Creates an engine with no observed price.
    pub fn new() -> Self {
        Self::default()
    }

    /// Returns the most recent observed close, if any.
    pub fn last_price(&self) -> Option<f64> {
        self.inner.lock().unwrap().last_price
    }
}

impl Handler for BarFillEngine {
    fn on_event(&mut self, event: &Event, _ts_init: UnixNanos) -> Result<()> {
        if let Event::Bar(b) = event {
            self.inner.lock().unwrap().last_price = Some(b.close());
        }
        Ok(())
    }
}

impl ExecutionEngine for BarFillEngine {
    fn submit(&mut self, order: Order) -> Result<()> {
        let mut inner = self.inner.lock().unwrap();
        let price = inner.last_price.unwrap_or(0.0);
        let ts = UnixNanos::from_u64(inner.next_ts.max(order.ts_event().as_u64()));
        inner.next_ts = ts.as_u64() + 1;
        inner.fills.push(Trade::new(
            OrderId::new(order.order_id().as_str()),
            order.instrument_id().clone(),
            order.side(),
            order.quantity(),
            price,
            ts,
            ts,
        ));
        Ok(())
    }

    fn cancel(&mut self, _order_id: &str) -> Result<()> {
        Ok(())
    }

    fn drain_fills(&mut self) -> Result<Vec<Trade>> {
        Ok(std::mem::take(&mut self.inner.lock().unwrap().fills))
    }
}
