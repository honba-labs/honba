//! Execution engine that fills at the most recent bar's close.

use std::sync::{Arc, Mutex};

use honba_engine::{ExecutionEngine, Handler, Result};
use honba_entities::{Currency, Money, Trade};
use honba_messages::{Event, Order, OrderId, UnixNanos};

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

    fn of(&self, quantity: f64, price: f64, currency: Currency) -> Money {
        // Round per leg, then sum (ADR 0011): the flat leg and the bps leg each
        // settle to minor units once, so the total is a ledger-reproducible
        // value rather than a rounded-after-summation approximation.
        let flat = Money::from_major_f64(self.flat, currency);
        let bps = Money::mul_qty(
            quantity * price * self.bps / 10_000.0,
            1.0,
            currency,
        );
        match (flat, bps) {
            (Ok(f), Ok(b)) => (f + b).unwrap_or_else(|_| Money::zero(currency)),
            // A non-finite input cannot produce a cost; fall back to zero costs
            // rather than a NaN on the trade.
            _ => Money::zero(currency),
        }
    }
}

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
            self.inner.lock().unwrap().last_price = Some(b.close());
        }
        Ok(honba_engine::EngineOutput::None)
    }
}

impl ExecutionEngine for BarFillEngine {
    fn submit(&mut self, order: Order) -> Result<()> {
        let mut inner = self.inner.lock().unwrap();
        let price = inner.last_price.unwrap_or(0.0);
        let ts = UnixNanos::from_u64(inner.next_ts.max(order.ts_event().as_u64()));
        inner.next_ts = ts.as_u64() + 1;
        let costs = self.costs.of(order.quantity(), price, self.currency);
        inner.fills.push(
            Trade::new(
                OrderId::new(order.order_id().as_str()),
                order.instrument_id().clone(),
                order.side(),
                order.quantity(),
                price,
                self.currency,
                ts,
                ts,
            )
            .with_costs(costs),
        );
        Ok(())
    }

    fn cancel(&mut self, _order_id: &str) -> Result<()> {
        Ok(())
    }

    fn drain_fills(&mut self) -> Result<Vec<Trade>> {
        Ok(std::mem::take(&mut self.inner.lock().unwrap().fills))
    }
}
