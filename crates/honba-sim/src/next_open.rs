//! Deterministic multi-instrument simulator that fills market orders at the next bar open.
//!
//! Rust port of `honba.backtest.simulated.NextOpenExecution` (ADR 0016); the Python class is the
//! reference and `schema/conformance/next_open_sim.json` pins the two together. Implemented so
//! far (chunk 1): market orders, fill at the open of the instrument's first bar in a later
//! session, cancel, the `unsupported_order_type`, `no_position` and `insufficient_funds`
//! rejections, integer-minor-unit cash, long-only sell caps and lot-sized funding cuts. Not yet:
//! settlement cycles, transaction costs and the `SessionOpen` event (chunk 2).

use honba_engine::{AlgoError, EngineOutput, ExecutionEngine, Handler, OrderRejection, Result};
use honba_entities::{Currency, Money, Trade};
use honba_messages::{Bar, Event, InstrumentId, Order, OrderId, OrderSide, OrderType, UnixNanos};

/// Quantities closer than this are equal (float residue from fractional fills).
const QTY_EPS: f64 = 1e-9;

struct Working {
    order: Order,
    /// Index of the session the order was submitted in (-1 before the first).
    session: i64,
}

struct Open {
    instrument: InstrumentId,
    price: f64,
    ts: UnixNanos,
}

/// An [`ExecutionEngine`] filling market orders at the next session's open.
///
/// A session is one point on the driving clock with an increasing key (the bar's `ts_event`).
/// An order submitted in session *k* fills at the open of its instrument's first usable bar in a
/// later session and waits while the instrument does not print. Within one opening, sells fill
/// before buys, each side in submission order. All cash is integer minor units (ADR 0011).
pub struct NextOpenSim {
    cash: i64,
    currency: Currency,
    fees: i64,
    traded_notional: i64,
    long_only: bool,
    lot_sizes: Vec<(InstrumentId, f64)>,
    positions: Vec<(InstrumentId, f64)>,
    session: i64,
    session_ts: Option<u64>,
    opened: Vec<InstrumentId>,
    working: Vec<Working>,
    fills: Vec<Trade>,
    rejections: Vec<OrderRejection>,
}

fn err(msg: String) -> AlgoError {
    AlgoError::Component(msg)
}

fn usable_open(price: f64) -> bool {
    price.is_finite() && price > 0.0
}

impl NextOpenSim {
    /// Creates a simulator holding `cash` (not negative), long only.
    pub fn new(cash: Money) -> Result<Self> {
        if cash.minor() < 0 {
            return Err(err("cash must be >= 0".into()));
        }
        Ok(Self {
            cash: cash.minor(),
            currency: cash.currency(),
            fees: 0,
            traded_notional: 0,
            long_only: true,
            lot_sizes: Vec::new(),
            positions: Vec::new(),
            session: -1,
            session_ts: None,
            opened: Vec::new(),
            working: Vec::new(),
            fills: Vec::new(),
            rejections: Vec::new(),
        })
    }

    /// Sets whether a sell is capped at the position held (default true).
    #[must_use]
    pub fn with_long_only(mut self, long_only: bool) -> Self {
        self.long_only = long_only;
        self
    }

    /// Sets the quantity step a funding cut floors to for `instrument` (default 1).
    pub fn set_lot_size(&mut self, instrument: &InstrumentId, lot_size: f64) -> Result<()> {
        if !(lot_size.is_finite() && lot_size > 0.0) {
            return Err(err(format!("lot_size must be positive, got {lot_size}")));
        }
        match self.lot_sizes.iter_mut().find(|(i, _)| i == instrument) {
            Some(entry) => entry.1 = lot_size,
            None => self.lot_sizes.push((instrument.clone(), lot_size)),
        }
        Ok(())
    }

    /// Booked cash.
    pub fn cash(&self) -> Money {
        Money::new(self.cash, self.currency)
    }

    /// Transaction costs paid so far.
    pub fn fees(&self) -> Money {
        Money::new(self.fees, self.currency)
    }

    /// Sum of the notional of every fill.
    pub fn traded_notional(&self) -> Money {
        Money::new(self.traded_notional, self.currency)
    }

    /// The net signed position in `instrument` (0 when flat).
    pub fn position(&self, instrument: &InstrumentId) -> f64 {
        self.positions
            .iter()
            .find(|(i, _)| i == instrument)
            .map_or(0.0, |(_, q)| *q)
    }

    /// Every non-flat position, in the order first opened.
    pub fn positions(&self) -> Vec<(InstrumentId, f64)> {
        self.positions.clone()
    }

    /// Ids of orders not yet filled, rejected or cancelled, in submission order.
    pub fn working_orders(&self) -> Vec<String> {
        self.working
            .iter()
            .map(|w| w.order.order_id().as_str().to_string())
            .collect()
    }

    /// Starts session `ts` and fills eligible orders at the opens of `bars`.
    ///
    /// Errors if `ts` does not follow the current session; state is unchanged then.
    pub fn open_session(&mut self, ts: UnixNanos, bars: &[Bar]) -> Result<()> {
        let ts = ts.as_u64();
        if let Some(cur) = self.session_ts {
            if ts <= cur {
                return Err(err(format!("session {ts} does not follow session {cur}")));
            }
        }
        self.session += 1;
        self.session_ts = Some(ts);
        self.opened.clear();
        self.fill_at(bars)
    }

    /// Observes one bar: it opens a session when its ts is later than the current one, opens its
    /// own instrument when it shares the ts, and is an error when earlier or repeated.
    pub fn on_bar(&mut self, bar: &Bar) -> Result<()> {
        let ts = bar.ts_event().as_u64();
        match self.session_ts {
            Some(cur) if ts < cur => Err(err(format!(
                "non-monotonic bar: ts {ts} is before session {cur}"
            ))),
            Some(cur) if ts == cur => {
                if self.opened.contains(bar.bar_type().instrument_id()) {
                    return Err(err(format!(
                        "duplicate bar for {} at ts {ts} in session {cur}",
                        bar.bar_type().instrument_id().symbol()
                    )));
                }
                self.fill_at(std::slice::from_ref(bar))
            }
            _ => self.open_session(bar.ts_event(), std::slice::from_ref(bar)),
        }
    }

    fn now(&self) -> UnixNanos {
        UnixNanos::from_u64(self.session_ts.unwrap_or(0))
    }

    fn fill_at(&mut self, bars: &[Bar]) -> Result<()> {
        let mut opens: Vec<Open> = Vec::new();
        for b in bars {
            let id = b.bar_type().instrument_id();
            if !self.opened.contains(id)
                && usable_open(b.open())
                && !opens.iter().any(|o| &o.instrument == id)
            {
                opens.push(Open {
                    instrument: id.clone(),
                    price: b.open(),
                    ts: b.ts_event(),
                });
            }
        }
        self.opened
            .extend(opens.iter().map(|o| o.instrument.clone()));
        let eligible: Vec<(String, usize)> = self
            .working
            .iter()
            .filter(|w| w.session < self.session)
            .filter_map(|w| {
                opens
                    .iter()
                    .position(|o| &o.instrument == w.order.instrument_id())
                    .map(|i| (w.order.order_id().as_str().to_string(), i))
            })
            .collect();
        for side in [OrderSide::Sell, OrderSide::Buy] {
            for (id, i) in &eligible {
                let Some(idx) = self
                    .working
                    .iter()
                    .position(|w| w.order.order_id().as_str() == id)
                else {
                    continue;
                };
                if self.working[idx].order.side() != side {
                    continue;
                }
                if side == OrderSide::Sell {
                    self.fill_sell(idx, &opens[*i])?;
                } else {
                    self.fill_buy(idx, &opens[*i])?;
                }
            }
        }
        Ok(())
    }

    fn notional(&self, qty: f64, price: f64) -> Result<i64> {
        Money::mul_qty(qty, price, self.currency)
            .map(|m| m.minor())
            .map_err(|e| err(format!("notional: {e}")))
    }

    fn fill_sell(&mut self, idx: usize, open: &Open) -> Result<()> {
        let order = self.working[idx].order.clone();
        let mut want = order.quantity();
        let mut qty = want;
        if self.long_only {
            let held = self.position(order.instrument_id());
            qty = 0.0_f64.max(want.min(held));
            if 0.0 < qty && qty < want && want - qty <= QTY_EPS {
                want = qty; // a hair over the position is float residue: sell it all
            }
        }
        let notional = if qty > 0.0 {
            self.notional(qty, open.price)? // before dequeuing: a failure leaves the order working
        } else {
            0
        };
        let cash = self
            .cash
            .checked_add(notional)
            .ok_or_else(|| err("cash overflow".into()))?;
        self.working.remove(idx);
        if qty < want {
            self.reject(&order, want - qty, "no_position");
        }
        if qty <= 0.0 {
            return Ok(());
        }
        self.cash = cash;
        self.book(&order, open, qty, notional)
    }

    fn fill_buy(&mut self, idx: usize, open: &Open) -> Result<()> {
        let order = self.working[idx].order.clone();
        let want = order.quantity();
        let px = open.price;
        let qty = if self.notional(want, px)? <= self.cash {
            want
        } else {
            self.affordable(want, px, self.lot_size(order.instrument_id()))?
        };
        let notional = if qty > 0.0 {
            self.notional(qty, px)?
        } else {
            0
        };
        self.working.remove(idx);
        if qty < want {
            self.reject(&order, want - qty, "insufficient_funds");
        }
        if qty <= 0.0 {
            return Ok(());
        }
        self.cash -= notional;
        self.book(&order, open, qty, notional)
    }

    fn lot_size(&self, instrument: &InstrumentId) -> f64 {
        self.lot_sizes
            .iter()
            .find(|(i, _)| i == instrument)
            .map_or(1.0, |(_, l)| *l)
    }

    /// Largest whole number of lots (<= `want`) whose notional fits the cash, by bisection.
    fn affordable(&self, want: f64, px: f64, lot: f64) -> Result<f64> {
        if px <= 0.0 || self.cash <= 0 {
            return Ok(0.0);
        }
        let avail_major = Money::new(self.cash, self.currency).to_major_f64();
        let hi = (want / lot + QTY_EPS)
            .floor()
            .min((avail_major / px / lot + QTY_EPS).floor());
        let (mut lo, mut hi) = (0_i64, hi as i64);
        while lo < hi {
            let mid = (lo + hi + 1) / 2;
            if self.notional(mid as f64 * lot, px)? <= self.cash {
                lo = mid;
            } else {
                hi = mid - 1;
            }
        }
        Ok(lo as f64 * lot)
    }

    fn book(&mut self, order: &Order, open: &Open, qty: f64, notional: i64) -> Result<()> {
        let signed = if order.side() == OrderSide::Buy {
            qty
        } else {
            -qty
        };
        let held = self.position(order.instrument_id()) + signed;
        self.positions.retain(|(i, _)| i != order.instrument_id());
        if held.abs() > QTY_EPS {
            self.positions.push((order.instrument_id().clone(), held));
        }
        self.traded_notional = self
            .traded_notional
            .checked_add(notional)
            .ok_or_else(|| err("traded notional overflow".into()))?;
        self.fills.push(Trade::new(
            OrderId::new(order.order_id().as_str()),
            order.instrument_id().clone(),
            order.side(),
            qty,
            open.price,
            self.currency,
            open.ts,
            open.ts,
        ));
        Ok(())
    }

    fn reject(&mut self, order: &Order, qty: f64, reason: &str) {
        let ts = self.now();
        self.push_rejection(order, qty, reason, ts);
    }

    fn push_rejection(&mut self, order: &Order, qty: f64, reason: &str, ts: UnixNanos) {
        self.rejections.push(OrderRejection::rejected(
            order.order_id().clone(),
            order.instrument_id().clone(),
            order.side(),
            qty,
            reason,
            ts,
        ));
    }
}

impl ExecutionEngine for NextOpenSim {
    fn submit(&mut self, order: Order) -> Result<()> {
        let id = order.order_id().as_str();
        if order.side() == OrderSide::NoOrderSide {
            return Err(err(format!(
                "order {id} has no side: it must be buy or sell"
            )));
        }
        if self
            .working
            .iter()
            .any(|w| w.order.order_id().as_str() == id)
        {
            return Err(err(format!("order id {id} is already working")));
        }
        if order.order_type() != OrderType::Market {
            let (qty, ts) = (order.quantity(), order.ts_event());
            self.push_rejection(&order, qty, "unsupported_order_type", ts);
            return Ok(());
        }
        self.working.push(Working {
            order,
            session: self.session,
        });
        Ok(())
    }

    fn cancel(&mut self, order_id: &str, now: UnixNanos) -> Result<()> {
        if let Some(i) = self
            .working
            .iter()
            .position(|w| w.order.order_id().as_str() == order_id)
        {
            let w = self.working.remove(i);
            self.rejections.push(OrderRejection::cancelled(
                w.order.order_id().clone(),
                w.order.instrument_id().clone(),
                w.order.side(),
                w.order.quantity(),
                now,
            ));
        }
        Ok(())
    }

    fn drain_fills(&mut self) -> Result<Vec<Trade>> {
        Ok(std::mem::take(&mut self.fills))
    }

    fn drain_rejections(&mut self) -> Result<Vec<OrderRejection>> {
        Ok(std::mem::take(&mut self.rejections))
    }
}

impl Handler for NextOpenSim {
    fn on_event(&mut self, event: &Event, _ts_init: UnixNanos) -> Result<EngineOutput> {
        if let Event::Bar(b) = event {
            self.on_bar(b)?;
        }
        Ok(EngineOutput::None)
    }
}
