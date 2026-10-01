//! The [`StrategyContext`] port: a strategy's only view of the world (ADR 008).
//!
//! A strategy reads the clock, its positions, cash and instrument metadata,
//! and submits [`OrderIntent`]s through the context. It never touches the
//! execution engine, I/O or the wall clock, so the same strategy runs
//! unchanged in backtest, paper and live. Mirrors the Python
//! `honba.strategies.context.StrategyContext` ABC.

use honba_entities::Instrument;
use honba_messages::{InstrumentId, UnixNanos};

use crate::intent::OrderIntent;

/// What a strategy may read and do. Implementations are supplied by the
/// runner; every [`Strategy`](crate::Strategy) hook receives one as
/// `&mut dyn StrategyContext`.
pub trait StrategyContext {
    /// The `ts_init` of the event being processed; zero before the first event.
    fn now(&self) -> UnixNanos;

    /// Net signed quantity held (positive long, negative short), updated from fills.
    fn position(&self, instrument_id: &InstrumentId) -> f64;

    /// Every non-flat position, ordered by instrument id (symbol, then venue).
    fn positions(&self) -> Vec<(InstrumentId, f64)>;

    /// Initial cash plus the net cash flow of all fills (buys debit
    /// `quantity * price + costs`, sells credit `quantity * price - costs`).
    fn cash(&self) -> f64;

    /// `true` while an intent submitted for `instrument_id` is not fully
    /// filled or rejected.
    fn busy(&self, instrument_id: &InstrumentId) -> bool;

    /// Instrument metadata (lot and tick size), if the run knows it.
    fn instrument(&self, instrument_id: &InstrumentId) -> Option<&Instrument>;

    /// Queues an intent; the runner turns it into an order after the current
    /// hook returns.
    fn submit(&mut self, intent: OrderIntent);
}
