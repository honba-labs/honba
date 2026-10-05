//! The [`StrategyContext`] port: a strategy's only view of the world (ADR 008).
//!
//! A strategy reads the clock, its positions, cash and instrument metadata,
//! and submits [`OrderIntent`]s through the context. It never touches the
//! execution engine, I/O or the wall clock, so the same strategy runs
//! unchanged in backtest, paper and live. Mirrors the Python
//! `honba.strategies.context.StrategyContext` ABC.

use std::collections::BTreeMap;

use honba_entities::{Currency, Instrument, Money, Trade};
use honba_messages::{InstrumentId, OrderSide, UnixNanos};

use crate::intent::OrderIntent;

/// What a strategy may read and do. Implementations are supplied by the
/// runner; every [`Strategy`](crate::Strategy) hook receives one as
/// `&mut dyn StrategyContext`.
pub trait StrategyContext {
    /// The `ts_init` of the event being processed; zero before the first event.
    fn now(&self) -> UnixNanos;

    /// Net signed quantity held (positive long, negative short), updated from fills.
    fn position(&self, instrument_id: &InstrumentId) -> f64;

    /// Every non-flat position, ordered by instrument id (symbol, then exchange).
    fn positions(&self) -> Vec<(InstrumentId, f64)>;

    /// Initial cash plus the net cash flow of all fills (buys debit
    /// notional plus costs, sells credit notional minus costs), in minor units.
    fn cash(&self) -> Money;

    /// `true` while an intent submitted for `instrument_id` is not fully
    /// filled or rejected.
    fn busy(&self, instrument_id: &InstrumentId) -> bool;

    /// Instrument metadata (lot and tick size), if the run knows it.
    fn instrument(&self, instrument_id: &InstrumentId) -> Option<&Instrument>;

    /// Queues an intent; the runner turns it into an order after the current
    /// hook returns.
    fn submit(&mut self, intent: OrderIntent);
}

/// Unfilled quantity below this counts as filled (float noise from partial fills).
const EPSILON: f64 = 1e-9;

#[derive(Clone, Copy, Debug, Default, PartialEq)]
struct Pending {
    buy: f64,
    sell: f64,
}

/// The reference [`StrategyContext`]: a deterministic in-memory ledger.
///
/// The runner sets the clock, applies fills and releases rejected intents;
/// the strategy reads it and submits intents, which the runner drains. It is
/// pure (no I/O, no wall clock), so backtest, paper and live share it.
/// Mirrors the Python `honba.strategies.context.LedgerContext`.
///
/// ```
/// use honba_entities::{Currency, Money};
/// use honba_strategy::{LedgerContext, OrderIntent, StrategyContext};
/// use honba_messages::{InstrumentId, Exchange};
///
/// let id = InstrumentId::new("NIFTY50", Exchange::new("NSE"));
/// let mut ctx = LedgerContext::with_cash(Money::new(10_000_000, Currency::Inr));
/// ctx.submit(OrderIntent::market_buy(id.clone(), 75.0));
/// assert!(ctx.busy(&id));
/// assert_eq!(ctx.drain_intents().len(), 1);
/// ```
#[derive(Clone, Debug)]
pub struct LedgerContext {
    now: UnixNanos,
    cash: Money,
    currency: Currency,
    positions: BTreeMap<InstrumentId, f64>,
    pending: BTreeMap<InstrumentId, Pending>,
    instruments: BTreeMap<InstrumentId, Instrument>,
    outbox: Vec<OrderIntent>,
}

impl Default for LedgerContext {
    fn default() -> Self {
        Self {
            now: UnixNanos::from_u64(0),
            cash: Money::zero(Currency::Inr),
            currency: Currency::Inr,
            positions: Default::default(),
            pending: Default::default(),
            instruments: Default::default(),
            outbox: Default::default(),
        }
    }
}

impl LedgerContext {
    /// An empty ledger with zero cash, in the given currency.
    pub fn new() -> Self {
        Self::default()
    }

    /// An empty ledger starting with `cash`.
    pub fn with_cash(cash: Money) -> Self {
        Self {
            currency: cash.currency(),
            cash,
            ..Self::default()
        }
    }

    /// The currency this ledger settles in.
    pub fn currency(&self) -> Currency {
        self.currency
    }

    /// Sets the clock to the `ts_init` of the event about to be processed.
    pub fn set_now(&mut self, ts_init: UnixNanos) {
        self.now = ts_init;
    }

    /// Registers instrument metadata for [`StrategyContext::instrument`].
    pub fn add_instrument(&mut self, instrument: Instrument) {
        self.instruments.insert(instrument.id().clone(), instrument);
    }

    /// Returns and clears submitted intents, in submission order.
    pub fn drain_intents(&mut self) -> Vec<OrderIntent> {
        std::mem::take(&mut self.outbox)
    }

    /// Books a fill: position, cash (notional plus costs) and the pending
    /// quantity of the instrument. The notional rounds to minor units once, at
    /// the point of booking (ADR 0011), so cash accumulates integers.
    pub fn apply_fill(&mut self, fill: &Trade) {
        let entry = self
            .positions
            .entry(fill.instrument_id().clone())
            .or_insert(0.0);
        let notional = Money::mul_qty(fill.quantity(), fill.price(), self.currency);
        if fill.side() == OrderSide::Buy {
            *entry += fill.quantity();
            let debit = notional
                .and_then(|n| (n + fill.costs()).map_err(|_| honba_entities::MoneyError::InvalidQuantity.into()));
            if let Ok(debit) = debit {
                self.cash = (self.cash - debit).unwrap_or(self.cash);
            }
        } else {
            *entry -= fill.quantity();
            let credit = notional.and_then(|n| {
                (n - fill.costs()).map_err(|_| honba_entities::MoneyError::InvalidQuantity.into())
            });
            if let Ok(credit) = credit {
                self.cash = (self.cash + credit).unwrap_or(self.cash);
            }
        }
        self.reduce_pending(fill.instrument_id(), fill.side(), fill.quantity());
    }

    /// An intent was rejected or its order cancelled unfilled: it no longer
    /// counts towards [`StrategyContext::busy`].
    ///
    /// An intent that fails [`OrderIntent::validate`] never counted (see
    /// `submit`), so releasing it is a no-op.
    pub fn release(&mut self, intent: &OrderIntent) {
        if intent.validate().is_err() {
            return;
        }
        self.reduce_pending(&intent.instrument_id, intent.side, intent.quantity);
    }

    fn reduce_pending(&mut self, instrument_id: &InstrumentId, side: OrderSide, qty: f64) {
        let Some(p) = self.pending.get_mut(instrument_id) else {
            return;
        };
        let slot = if side == OrderSide::Buy {
            &mut p.buy
        } else {
            &mut p.sell
        };
        let left = *slot - qty;
        *slot = if left > EPSILON { left } else { 0.0 };
        if *p == Pending::default() {
            self.pending.remove(instrument_id);
        }
    }
}

impl StrategyContext for LedgerContext {
    fn now(&self) -> UnixNanos {
        self.now
    }

    fn position(&self, instrument_id: &InstrumentId) -> f64 {
        self.positions.get(instrument_id).copied().unwrap_or(0.0)
    }

    fn positions(&self) -> Vec<(InstrumentId, f64)> {
        self.positions
            .iter()
            .filter(|(_, q)| **q != 0.0)
            .map(|(id, q)| (id.clone(), *q))
            .collect()
    }

    fn cash(&self) -> Money {
        self.cash
    }

    fn busy(&self, instrument_id: &InstrumentId) -> bool {
        self.pending
            .get(instrument_id)
            .is_some_and(|p| p.buy > 0.0 || p.sell > 0.0)
    }

    fn instrument(&self, instrument_id: &InstrumentId) -> Option<&Instrument> {
        self.instruments.get(instrument_id)
    }

    fn submit(&mut self, intent: OrderIntent) {
        // An invalid intent is still queued so the runner can reject it, but
        // it must not touch the pending ledger (a NaN would corrupt it).
        if intent.validate().is_ok() {
            let p = self
                .pending
                .entry(intent.instrument_id.clone())
                .or_default();
            if intent.side == OrderSide::Buy {
                p.buy += intent.quantity;
            } else {
                p.sell += intent.quantity;
            }
        }
        self.outbox.push(intent);
    }
}
