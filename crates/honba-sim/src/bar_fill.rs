//! Execution engine that fills at the most recent bar's close.

use std::sync::{Arc, Mutex};

use honba_engine::{AlgoError, ExecutionEngine, Handler, LegacyDrains, OrderRejection, Result};
use honba_entities::{Currency, ExecutionEvent, Money, Trade};
use honba_messages::{Event, Order, OrderId, OrderSide, OrderType, UnixNanos};

/// Largest accepted flat cost per fill.
pub const MAX_FLAT_COST: f64 = 1e9;

/// Largest accepted proportional cost, in basis points of the fill notional
/// (10 000 bps = 100%).
pub const MAX_COST_BPS: f64 = 10_000.0;

/// Why a [`FillCosts`] could not be built.
#[derive(Debug, Clone, PartialEq, thiserror::Error)]
pub enum FillCostsError {
    /// The flat cost is not a finite number in `[0, MAX_FLAT_COST]`.
    #[error("flat fill cost must be finite and within 0..={MAX_FLAT_COST}, got {0}")]
    InvalidFlat(f64),
    /// The proportional cost is not a finite number in `[0, MAX_COST_BPS]`.
    #[error("fill cost bps must be finite and within 0..={MAX_COST_BPS}, got {0}")]
    InvalidBps(f64),
}

/// Per-fill transaction costs charged by [`BarFillEngine`] (ADR 008).
///
/// The cost of a fill is the flat component plus the proportional component of
/// the notional, each **rounded to minor units before summation** (ADR 0011):
/// costs are computed per leg and each leg rounds once, so the reported total
/// is one a broker's ledger can reproduce. The cost is never negative and not
/// signed by side: `Trade::costs` carries it, a buy debits `quantity * price +
/// costs` and a sell credits `quantity * price - costs`. The default is no
/// costs.
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct FillCosts {
    flat: f64,
    bps: f64,
}

impl FillCosts {
    /// Validates and builds the costs: `flat` in `[0, MAX_FLAT_COST]` and
    /// `bps` in `[0, MAX_COST_BPS]`, both finite.
    pub fn new(flat: f64, bps: f64) -> std::result::Result<Self, FillCostsError> {
        if !(flat.is_finite() && (0.0..=MAX_FLAT_COST).contains(&flat)) {
            return Err(FillCostsError::InvalidFlat(flat));
        }
        if !(bps.is_finite() && (0.0..=MAX_COST_BPS).contains(&bps)) {
            return Err(FillCostsError::InvalidBps(bps));
        }
        Ok(Self { flat, bps })
    }

    /// The flat cost per fill.
    pub fn flat(&self) -> f64 {
        self.flat
    }

    /// The proportional cost in basis points of the fill notional.
    pub fn bps(&self) -> f64 {
        self.bps
    }

    /// The cost of one fill: each leg rounded to minor units once, then summed.
    ///
    /// A leg that cannot be represented (a non-finite or i64-overflowing
    /// notional) is an error, never a zero cost: a free fill would round in the
    /// reporter's favour (ADR 0011).
    fn of(&self, quantity: f64, price: f64, currency: Currency) -> Result<Money> {
        let leg_error =
            |e: honba_entities::MoneyError| AlgoError::Component(format!("fill cost: {e}"));
        let flat = Money::from_major_f64(self.flat, currency).map_err(leg_error)?;
        let bps = Money::from_major_f64(quantity * price * self.bps / 10_000.0, currency)
            .map_err(leg_error)?;
        (flat + bps).map_err(|e| AlgoError::Component(format!("fill cost: {e}")))
    }
}

#[derive(Clone)]
struct RestingTrailingStop {
    order: Order,
    peak: f64,
    trough: f64,
}

#[derive(Default)]
struct Inner {
    last_price: Option<f64>,
    resting_trailing: Vec<RestingTrailingStop>,
    events: Vec<ExecutionEvent>,
    legacy: LegacyDrains,
    next_ts: u64,
}

/// An execution engine that records the most recent bar close (as a
/// [`Handler`]) and fills every order at that price (as an
/// [`ExecutionEngine`]). Each order produces one complete
/// [`ExecutionEvent::Fill`]; it never acknowledges (L1).
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
///     UnixNanos, Exchange,
/// };
///
/// let mut exec = BarFillEngine::new();
/// let bt = BarType::new(
///     InstrumentId::new("X", Exchange::new("TEST")),
///     BarSpecification::new(1, BarAggregation::Minute, PriceType::Last),
/// );
/// let t = UnixNanos::from_u64(2);
/// let bar = Bar::new(bt, 100.0, 102.0, 99.0, 101.0, 1000.0, t, t);
/// exec.on_event(&Event::Bar(bar), t).unwrap();
///
/// // Last price observed from bar close is 101. Submit and drain.
/// let order = Order::new(
///     OrderId::new("O-1"),
///     InstrumentId::new("X", Exchange::new("TEST")),
///     OrderSide::Buy, OrderType::Market, 5.0, None, TimeInForce::Day,
///     UnixNanos::from_u64(2), UnixNanos::from_u64(2),
/// );
/// exec.submit(order).unwrap();
/// let fills = exec.drain_fills().unwrap();
/// assert_eq!(fills.len(), 1);
/// assert_eq!(fills[0].price(), 101.0);
/// ```
#[derive(Clone)]
pub struct BarFillEngine {
    inner: Arc<Mutex<Inner>>,
    costs: FillCosts,
    /// The currency costs settle in. An `Order` carries no currency (it is a
    /// market instruction, not a ledger entry), so the engine declares which
    /// currency its fills cost in — INR for the Indian market by default.
    currency: Currency,
}

impl Default for BarFillEngine {
    fn default() -> Self {
        Self {
            inner: Arc::new(Mutex::new(Inner::default())),
            costs: FillCosts::default(),
            currency: Currency::Inr,
        }
    }
}

impl BarFillEngine {
    /// Creates an engine with no observed price and no fill costs.
    pub fn new() -> Self {
        Self::default()
    }

    /// Creates an engine that charges `costs` on every fill.
    pub fn with_costs(costs: FillCosts) -> Self {
        Self {
            costs,
            ..Self::default()
        }
    }

    /// Sets the settlement currency costs are charged in.
    pub fn with_currency(mut self, currency: Currency) -> Self {
        self.currency = currency;
        self
    }

    /// The settlement currency.
    pub fn currency(&self) -> Currency {
        self.currency
    }

    /// Returns the most recent observed close, if any.
    pub fn last_price(&self) -> Option<f64> {
        self.inner.lock().unwrap().last_price
    }
}

impl Handler for BarFillEngine {
    fn on_event(
        &mut self,
        event: &Event,
        _ts_init: UnixNanos,
    ) -> Result<honba_engine::EngineOutput> {
        if let Event::Bar(b) = event {
            let mut inner = self.inner.lock().unwrap();
            inner.last_price = Some(b.close());
            let iid = b.bar_type().instrument_id();
            let mut triggered = Vec::new();
            inner.resting_trailing.retain_mut(|stop| {
                if stop.order.instrument_id() != iid {
                    return true;
                }
                match stop.order.side() {
                    OrderSide::Sell => {
                        stop.peak = stop.peak.max(b.high());
                        let trigger = if let Some(amt) = stop.order.trail_amount() {
                            stop.peak - amt
                        } else if let Some(pct) = stop.order.trail_percent() {
                            stop.peak * (1.0 - pct / 100.0)
                        } else {
                            stop.peak
                        };
                        if b.low() <= trigger {
                            let px = if b.open() <= trigger {
                                b.open()
                            } else {
                                trigger
                            };
                            triggered.push((stop.order.clone(), px));
                            false
                        } else {
                            true
                        }
                    }
                    OrderSide::Buy => {
                        stop.trough = stop.trough.min(b.low());
                        let trigger = if let Some(amt) = stop.order.trail_amount() {
                            stop.trough + amt
                        } else if let Some(pct) = stop.order.trail_percent() {
                            stop.trough * (1.0 + pct / 100.0)
                        } else {
                            stop.trough
                        };
                        if b.high() >= trigger {
                            let px = if b.open() >= trigger {
                                b.open()
                            } else {
                                trigger
                            };
                            triggered.push((stop.order.clone(), px));
                            false
                        } else {
                            true
                        }
                    }
                    OrderSide::NoOrderSide | _ => false,
                }
            });
            for (order, price) in triggered {
                let costs = self.costs.of(order.quantity(), price, self.currency)?;
                let ts = UnixNanos::from_u64(inner.next_ts.max(b.ts_event().as_u64()));
                inner.next_ts = ts.as_u64() + 1;
                let trade = Trade::new(
                    OrderId::new(order.order_id().as_str()),
                    order.instrument_id().clone(),
                    order.side(),
                    order.quantity(),
                    price,
                    self.currency,
                    ts,
                    ts,
                )
                .with_costs(costs);
                inner.events.push(ExecutionEvent::Fill {
                    trade,
                    cum_qty: order.quantity(),
                    complete: true,
                    venue_order_id: None,
                });
            }
        }
        Ok(honba_engine::EngineOutput::None)
    }
}

impl ExecutionEngine for BarFillEngine {
    fn submit(&mut self, order: Order) -> Result<()> {
        let mut inner = self.inner.lock().unwrap();
        if order.order_type() == OrderType::TrailingStop {
            let ref_px = inner.last_price.unwrap_or(0.0);
            inner.resting_trailing.push(RestingTrailingStop {
                order,
                peak: ref_px,
                trough: ref_px,
            });
            return Ok(());
        }
        let price = inner.last_price.unwrap_or(0.0);
        // Costs first: a fill whose cost cannot be represented is refused
        // before it consumes a timestamp or reaches the fill buffer.
        let costs = self.costs.of(order.quantity(), price, self.currency)?;
        let ts = UnixNanos::from_u64(inner.next_ts.max(order.ts_event().as_u64()));
        inner.next_ts = ts.as_u64() + 1;
        let trade = Trade::new(
            OrderId::new(order.order_id().as_str()),
            order.instrument_id().clone(),
            order.side(),
            order.quantity(),
            price,
            self.currency,
            ts,
            ts,
        )
        .with_costs(costs);
        inner.events.push(ExecutionEvent::Fill {
            trade,
            cum_qty: order.quantity(),
            complete: true,
            venue_order_id: None,
        });
        Ok(())
    }

    fn cancel(&mut self, order_id: &str, _now: UnixNanos) -> Result<()> {
        let mut inner = self.inner.lock().unwrap();
        inner
            .resting_trailing
            .retain(|s| s.order.order_id().as_str() != order_id);
        Ok(())
    }

    fn drain_events(&mut self) -> Result<Vec<ExecutionEvent>> {
        Ok(std::mem::take(&mut self.inner.lock().unwrap().events))
    }

    fn native_events(&self) -> bool {
        true
    }

    fn drain_fills(&mut self) -> Result<Vec<Trade>> {
        let mut inner = self.inner.lock().unwrap();
        let events = std::mem::take(&mut inner.events);
        inner.legacy.absorb(events);
        Ok(inner.legacy.take_fills())
    }

    fn drain_rejections(&mut self) -> Result<Vec<OrderRejection>> {
        let mut inner = self.inner.lock().unwrap();
        let events = std::mem::take(&mut inner.events);
        inner.legacy.absorb(events);
        Ok(inner.legacy.take_rejections())
    }
}
