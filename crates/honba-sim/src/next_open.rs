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

/// `(side, quantity, price) -> cost` of one fill, in the simulator's currency and never
/// negative (a negative value is an error that leaves the order working). Injected so that market
/// cost schedules stay above `honba-sim`; the Python counterpart is `FillCostFn`.
pub type FillCostFn = Box<dyn Fn(OrderSide, f64, f64) -> Result<Money> + Send>;

struct Working {
    order: Order,
    /// Index of the session the order was submitted in (-1 before the first).
    session: i64,
    /// First session the order was eligible and considered for funding.
    first_try: Option<i64>,
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
    settlement_days: i64,
    /// `(session the proceeds become available, amount in minor units)`.
    receivables: Vec<(i64, i64)>,
    cost_fn: FillCostFn,
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
            settlement_days: 0,
            receivables: Vec::new(),
            cost_fn: Box::new(|_, _, _| Ok(Money::new(0, Currency::Inr))),
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

    /// Sets the fill cost function (default: no costs). Funding cuts include its cost, so it
    /// should be non-decreasing in quantity.
    #[must_use]
    pub fn with_costs(mut self, cost_fn: FillCostFn) -> Self {
        self.cost_fn = cost_fn;
        self
    }

    /// Sets whether a sell is capped at the position held (default true).
    #[must_use]
    pub fn with_long_only(mut self, long_only: bool) -> Self {
        self.long_only = long_only;
        self
    }

    /// Sets the settlement cycle: sale proceeds become available that many sessions after the
    /// sale (default 0). Errors when negative.
    pub fn with_settlement_days(mut self, days: i64) -> Result<Self> {
        self.set_settlement_days(days)?;
        Ok(self)
    }

    /// Changes the settlement cycle. Errors (state unchanged) when `days` is negative or the
    /// first session has already opened.
    pub fn set_settlement_days(&mut self, days: i64) -> Result<()> {
        if days < 0 {
            return Err(err("settlement_days must be >= 0".into()));
        }
        if self.session_ts.is_some() {
            return Err(err(
                "settlement_days can only change before the first session".into(),
            ));
        }
        self.settlement_days = days;
        Ok(())
    }

    /// The settlement cycle in sessions.
    pub fn settlement_days(&self) -> i64 {
        self.settlement_days
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

    /// Sale proceeds booked but not yet available.
    pub fn unsettled(&self) -> Money {
        Money::new(self.unsettled_minor(), self.currency)
    }

    /// Cash that may fund a buy now: booked cash less unsettled sale proceeds.
    pub fn available_cash(&self) -> Money {
        Money::new(self.cash - self.unsettled_minor(), self.currency)
    }

    /// Pending sale proceeds as `(session index they become available, amount)`.
    pub fn receivables(&self) -> Vec<(i64, Money)> {
        self.receivables
            .iter()
            .map(|&(due, a)| (due, Money::new(a, self.currency)))
            .collect()
    }

    fn unsettled_minor(&self) -> i64 {
        self.receivables
            .iter()
            .filter(|(due, _)| *due > self.session)
            .map(|(_, a)| a)
            .sum()
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
        let session = self.session;
        self.receivables.retain(|(due, _)| *due > session);
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

    /// Cost of one fill in minor units; errors when negative or in another currency (a zero
    /// cost is currency-neutral).
    fn costs(&self, side: OrderSide, qty: f64, price: f64) -> Result<i64> {
        let cost = (self.cost_fn)(side, qty, price)?;
        if cost.minor() == 0 {
            return Ok(0);
        }
        if cost.currency() != self.currency {
            return Err(err(format!(
                "fill cost currency {:?} differs from the cash currency {:?}",
                cost.currency(),
                self.currency
            )));
        }
        if cost.minor() < 0 {
            return Err(err(format!(
                "fill cost must not be negative, got {}",
                cost.minor()
            )));
        }
        Ok(cost.minor())
    }

    /// Notional plus cost of buying `qty` at `px`.
    fn buy_cost(&self, qty: f64, px: f64) -> Result<i64> {
        self.notional(qty, px)?
            .checked_add(self.costs(OrderSide::Buy, qty, px)?)
            .ok_or_else(|| err("buy cost overflow".into()))
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
        let (notional, cost) = if qty > 0.0 {
            // before dequeuing: a failure leaves the order working
            (
                self.notional(qty, open.price)?,
                self.costs(OrderSide::Sell, qty, open.price)?,
            )
        } else {
            (0, 0)
        };
        let proceeds = notional
            .checked_sub(cost)
            .ok_or_else(|| err("proceeds overflow".into()))?;
        let cash = self
            .cash
            .checked_add(proceeds)
            .ok_or_else(|| err("cash overflow".into()))?;
        self.working.remove(idx);
        if qty < want {
            self.reject(&order, want - qty, "no_position");
        }
        if qty <= 0.0 {
            return Ok(());
        }
        self.cash = cash;
        self.receivables
            .push((self.session + self.settlement_days, proceeds));
        self.book(&order, open, qty, notional, cost)
    }

    fn fill_buy(&mut self, idx: usize, open: &Open) -> Result<()> {
        let session = self.session;
        let first_try = *self.working[idx].first_try.get_or_insert(session);
        let order = self.working[idx].order.clone();
        let want = order.quantity();
        let px = open.price;
        let available = self.cash - self.unsettled_minor();
        let qty = if self.buy_cost(want, px)? <= available {
            want
        } else if self.unsettled_minor() > 0 && session - first_try < self.settlement_days {
            return Ok(()); // wait for pending sale proceeds to settle
        } else {
            self.affordable(want, px, available, self.lot_size(order.instrument_id()))?
        };
        let (notional, cost) = if qty > 0.0 {
            // before dequeuing: a failure leaves the order working
            (
                self.notional(qty, px)?,
                self.costs(OrderSide::Buy, qty, px)?,
            )
        } else {
            (0, 0)
        };
        let outlay = notional
            .checked_add(cost)
            .and_then(|o| self.cash.checked_sub(o))
            .ok_or_else(|| err("cash overflow".into()))?;
        self.working.remove(idx);
        if qty < want {
            self.reject(&order, want - qty, "insufficient_funds");
        }
        if qty <= 0.0 {
            return Ok(());
        }
        self.cash = outlay;
        self.book(&order, open, qty, notional, cost)
    }

    fn lot_size(&self, instrument: &InstrumentId) -> f64 {
        self.lot_sizes
            .iter()
            .find(|(i, _)| i == instrument)
            .map_or(1.0, |(_, l)| *l)
    }

    /// Largest whole number of lots (<= `want`) whose notional fits the cash, by bisection.
    fn affordable(&self, want: f64, px: f64, available: i64, lot: f64) -> Result<f64> {
        if px <= 0.0 || available <= 0 {
            return Ok(0.0);
        }
        let avail_major = Money::new(available, self.currency).to_major_f64();
        let hi = (want / lot + QTY_EPS)
            .floor()
            .min((avail_major / px / lot + QTY_EPS).floor());
        let (mut lo, mut hi) = (0_i64, hi as i64);
        while lo < hi {
            let mid = (lo + hi + 1) / 2;
            if self.buy_cost(mid as f64 * lot, px)? <= available {
                lo = mid;
            } else {
                hi = mid - 1;
            }
        }
        Ok(lo as f64 * lot)
    }

    fn book(
        &mut self,
        order: &Order,
        open: &Open,
        qty: f64,
        notional: i64,
        cost: i64,
    ) -> Result<()> {
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
        self.fees = self
            .fees
            .checked_add(cost)
            .ok_or_else(|| err("fees overflow".into()))?;
        self.traded_notional = self
            .traded_notional
            .checked_add(notional)
            .ok_or_else(|| err("traded notional overflow".into()))?;
        self.fills.push(
            Trade::new(
                OrderId::new(order.order_id().as_str()),
                order.instrument_id().clone(),
                order.side(),
                qty,
                open.price,
                self.currency,
                open.ts,
                open.ts,
            )
            .with_costs(Money::new(cost, self.currency)),
        );
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
            first_try: None,
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
