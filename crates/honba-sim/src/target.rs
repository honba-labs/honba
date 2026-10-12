//! Target-position ("smart order") execution with per-instrument serialisation.
//!
//! A strategy states the position it *wants* rather than the order it wants: a target of 100
//! from flat buys 100, a later target of 0 sells it back. [`TargetPosition`] turns a desired
//! position into the signed delta order for the current position, honouring the instrument's lot
//! size (the delta floors to whole lots, never overshooting the target) and capping at the freeze
//! (maximum single-order) quantity when the active market exposes one.
//!
//! [`SmartOrderController`] serialises the two per instrument: at most one target order is
//! outstanding for an instrument at a time ([`PerInstrumentLock`]), so a target submitted while an
//! order is working is deferred rather than doubled. The caller releases the instrument once its
//! order has filled, been rejected or cancelled.
//!
//! Splitting a target across several clips to respect a freeze quantity is the lot/freeze splitter
//! (E3-S6); until it lands the delta is capped to one lot-aligned clip here and the remainder is
//! deferred, mirroring `NextOpenSim`'s floor-to-lot funding cut.

use std::collections::HashMap;

use honba_engine::{AlgoError, Result};
use honba_entities::{Instrument, Position};
use honba_market::{InstrumentRules, MarketProfile};
use honba_messages::{InstrumentId, Order, OrderId, OrderSide, OrderType, TimeInForce, UnixNanos};

/// Quantities closer than this are equal (float residue).
const QTY_EPS: f64 = 1e-9;

/// Ticks of float noise tolerated when snapping a delta to a whole number of lots.
const LOT_TOLERANCE: f64 = 1e-6;

/// The net signed quantity of `position`: `+` long, `-` short, `0` flat.
fn net_position(position: &Position) -> f64 {
    position.side().sign() * position.quantity()
}

/// An in-memory per-instrument serialisation lock.
///
/// Keyed by [`InstrumentId`], it tracks which instruments already have an outstanding target
/// order: [`acquire`](Self::acquire) succeeds only when the instrument is free, and
/// [`release`](Self::release) frees it again. There is no blocking and no wall clock; a caller
/// that finds an instrument busy defers its target and retries once the order is done.
#[derive(Debug, Default, Clone)]
pub struct PerInstrumentLock {
    active: HashMap<InstrumentId, bool>,
}

impl PerInstrumentLock {
    /// An empty lock holding no instrument.
    pub fn new() -> Self {
        Self::default()
    }

    /// Acquires `instrument`, returning `false` (and changing nothing) when it is already held.
    pub fn acquire(&mut self, instrument: &InstrumentId) -> bool {
        if self.is_locked(instrument) {
            return false;
        }
        self.active.insert(instrument.clone(), true);
        true
    }

    /// Releases `instrument` (a no-op when it is not held).
    pub fn release(&mut self, instrument: &InstrumentId) {
        self.active.remove(instrument);
    }

    /// Whether `instrument` is currently held.
    pub fn is_locked(&self, instrument: &InstrumentId) -> bool {
        self.active.get(instrument).copied().unwrap_or(false)
    }

    /// The instruments currently held, in no particular order.
    pub fn locked(&self) -> Vec<InstrumentId> {
        self.active.keys().cloned().collect()
    }

    /// Releases every held instrument.
    pub fn clear(&mut self) {
        self.active.clear();
    }
}

/// Turns a desired position into an order: the quantity step its delta floors to, plus an optional
/// freeze cap.
///
/// Build it from an instrument's [`InstrumentRules`] — as the active [`MarketProfile`] gives them
/// ([`Self::from_profile`]) — or directly with [`Self::with_lot_size`]. The default is a lot of 1
/// and no freeze, the shape of cash equity.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TargetPosition {
    lot_size: f64,
    max_order_quantity: Option<f64>,
}

impl Default for TargetPosition {
    fn default() -> Self {
        Self {
            lot_size: 1.0,
            max_order_quantity: None,
        }
    }
}

impl TargetPosition {
    /// A target sizer with lot 1 and no freeze.
    pub fn new() -> Self {
        Self::default()
    }

    /// Sets the quantity step the delta floors to. Errors (state unchanged) when `lot_size` is not
    /// a positive, finite number.
    pub fn with_lot_size(mut self, lot_size: f64) -> Result<Self> {
        if !(lot_size.is_finite() && lot_size > 0.0) {
            return Err(AlgoError::Component(format!(
                "lot_size must be positive and finite, got {lot_size}"
            )));
        }
        self.lot_size = lot_size;
        Ok(self)
    }

    /// Reads an instrument's rules from `profile` (its lot size and, when it has one, its freeze
    /// quantity).
    pub fn from_profile(profile: &dyn MarketProfile, instrument: &Instrument) -> Self {
        Self::from_rules(&profile.instrument_rules().rules_for(instrument))
    }

    /// Reads lot size and freeze directly from an instrument's rules.
    pub fn from_rules(rules: &InstrumentRules) -> Self {
        Self {
            lot_size: rules.lot_size,
            max_order_quantity: rules.max_order_quantity,
        }
    }

    /// The quantity step the delta floors to.
    pub fn lot_size(&self) -> f64 {
        self.lot_size
    }

    /// The freeze (maximum single-order) quantity, if the instrument has one.
    pub fn max_order_quantity(&self) -> Option<f64> {
        self.max_order_quantity
    }

    /// The signed quantity to trade to move `current` to `target`.
    ///
    /// The raw difference floors to a whole number of lots towards zero, so a target is never
    /// overshot; the result is capped at the freeze when one is set (the remainder waits for the
    /// lot/freeze splitter, E3-S6). A result within [`QTY_EPS`] of zero is returned as `0.0`.
    pub fn delta(&self, current: &Position, target: f64) -> f64 {
        let raw = target - net_position(current);
        let lots = raw / self.lot_size;
        let lots = if (lots - lots.round()).abs() <= LOT_TOLERANCE {
            lots.round()
        } else {
            lots.trunc()
        };
        let mut qty = lots * self.lot_size;
        if let Some(max) = self.max_order_quantity {
            if qty.abs() > max + QTY_EPS {
                let clip = (max / self.lot_size + LOT_TOLERANCE).floor() * self.lot_size;
                qty = qty.signum() * qty.abs().min(clip);
            }
        }
        if qty.abs() <= QTY_EPS {
            0.0
        } else {
            qty
        }
    }

    /// The market order that moves `current` to `target`, or `None` when already at target.
    ///
    /// `side_hint`, when given, must agree with the delta's direction; a contradicting hint is an
    /// error rather than a silently reversed order.
    pub fn order(
        &self,
        current: &Position,
        target: f64,
        order_id: &str,
        side_hint: Option<OrderSide>,
        ts: UnixNanos,
    ) -> Result<Option<Order>> {
        let delta = self.delta(current, target);
        if delta.abs() <= QTY_EPS {
            return Ok(None);
        }
        let side = if delta > 0.0 {
            OrderSide::Buy
        } else {
            OrderSide::Sell
        };
        if let Some(hint) = side_hint {
            if hint != side {
                return Err(AlgoError::Component(format!(
                    "side hint {hint:?} contradicts the target delta for {}",
                    current.instrument_id().symbol()
                )));
            }
        }
        Ok(Some(Order::new(
            OrderId::new(order_id),
            current.instrument_id().clone(),
            side,
            OrderType::Market,
            delta.abs(),
            None,
            TimeInForce::Day,
            ts,
            ts,
        )))
    }
}

/// Drives target-position submission with per-instrument serialisation.
///
/// Holds a [`TargetPosition`] sizer and a [`PerInstrumentLock`]. [`Self::submit_target_position`]
/// computes the delta for the current position and, when the instrument is free, returns the one
/// order that closes the gap and books the instrument; when the instrument already has an
/// outstanding target order it returns `None` (deferred) so a working order is never doubled.
#[derive(Debug, Default, Clone)]
pub struct SmartOrderController {
    sizing: TargetPosition,
    lock: PerInstrumentLock,
    seq: u64,
}

impl SmartOrderController {
    /// A controller with the default sizer (lot 1, no freeze).
    pub fn new() -> Self {
        Self::default()
    }

    /// A controller using `sizing` to turn targets into orders.
    pub fn with_sizing(sizing: TargetPosition) -> Self {
        Self {
            sizing,
            ..Self::default()
        }
    }

    /// The per-instrument serialisation lock.
    pub fn lock(&self) -> &PerInstrumentLock {
        &self.lock
    }

    /// Whether `instrument` already has an outstanding target order.
    pub fn is_busy(&self, instrument: &InstrumentId) -> bool {
        self.lock.is_locked(instrument)
    }

    /// The instruments with an outstanding target order, in no particular order.
    pub fn busy(&self) -> Vec<InstrumentId> {
        self.lock.locked()
    }

    /// Submits the target `target` for the instrument of `current`.
    ///
    /// Returns `Ok(None)` when already at target, and `Ok(None)` (deferred) when the instrument
    /// already has an outstanding order; otherwise returns the single order that moves the
    /// position towards the target and books the instrument until [`Self::release`].
    pub fn submit_target_position(
        &mut self,
        current: &Position,
        target: f64,
        side_hint: Option<OrderSide>,
        ts: UnixNanos,
    ) -> Result<Option<Order>> {
        let instrument = current.instrument_id().clone();
        if !self.lock.acquire(&instrument) {
            return Ok(None);
        }
        self.seq += 1;
        let order_id = format!("target-{}-{}", instrument.symbol(), self.seq);
        match self.sizing.order(current, target, &order_id, side_hint, ts) {
            Ok(Some(order)) => Ok(Some(order)),
            Ok(None) => {
                self.lock.release(&instrument);
                Ok(None)
            }
            Err(e) => {
                self.lock.release(&instrument);
                Err(e)
            }
        }
    }

    /// Releases `instrument`'s serialisation lock once its order has filled, been rejected or
    /// cancelled. A no-op when the instrument is not held.
    pub fn release(&mut self, instrument: &InstrumentId) {
        self.lock.release(instrument);
    }

    /// Releases every instrument's lock.
    pub fn release_all(&mut self) {
        self.lock.clear();
    }
}
