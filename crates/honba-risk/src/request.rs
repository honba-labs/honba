//! The request a rule evaluates and the decision it yields (ADR 0018 decision 3).

use honba_messages::{InstrumentId, OrderId, OrderSide, TradingState, UnixNanos};

use crate::RiskRefusal;

/// Everything a rule needs, all values in: the stage reads no clock and no store.
#[derive(Clone, Debug, PartialEq)]
pub struct RiskRequest {
    /// The order being checked.
    pub order_id: OrderId,
    /// The instrument it targets.
    pub instrument_id: InstrumentId,
    /// Buy or sell.
    pub side: OrderSide,
    /// Order quantity (positive).
    pub quantity: f64,
    /// Limit price; `None` for market and stop-market orders.
    pub price: Option<f64>,
    /// Stop trigger price, if any.
    pub trigger_price: Option<f64>,
    /// Last observed price, supplied by the submitter; used to price the notional.
    pub reference_price: Option<f64>,
    /// Average daily volume for the instrument; `None` means the participation
    /// rule cannot evaluate and fails closed.
    pub adv: Option<f64>,
    /// Signed position plus the signed working remainder on the order's side
    /// (ADR 0019 `working_exposure(instrument, side)`).
    pub position: f64,
    /// The engine's current trading state.
    pub trading_state: TradingState,
    /// Event time, never the wall clock.
    pub ts: UnixNanos,
    /// Timestamp of the last market-data feed update for the instrument.
    pub last_feed_ts: Option<UnixNanos>,
}

/// The outcome of a risk check.
#[derive(Clone, Debug, PartialEq)]
pub enum RiskDecision {
    /// Every rule passed.
    Approved,
    /// The first rule that refused, with its numbers.
    Refused(RiskRefusal),
}

/// A pre-trade check. Rules run in a fixed order and the first refusal wins.
pub trait RiskCheck: Send {
    /// Evaluate every rule in order for `req`.
    fn check(&mut self, req: &RiskRequest) -> RiskDecision;
}
