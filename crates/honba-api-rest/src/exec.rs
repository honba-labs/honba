//! The execution gateway behind the order routes: an embedded [`NextOpenSim`] (ADR 0016).
//!
//! A risk-approved order is recorded in the [`OrderLedger`] as `Submitted` and handed to the
//! simulator. The simulator fills market orders at the open of the instrument's first bar in a
//! later session, so a fill needs the market to advance: the composition root (or a test) pushes
//! bars with [`AppState::advance_market`], and each simulator event updates the matching ledger
//! row (`Filled`, `PartiallyFilled`, `Rejected`, `Cancelled`). Event time is the bar's
//! `ts_event`; nothing here reads the wall clock, and the simulator is seeded, so a replay of
//! the same orders and bars books the same fills.
//!
//! Scope: market orders only. A limit order is refused by the simulator
//! (`unsupported_order_type`) and its ledger row becomes `Rejected`; the reason is logged
//! because `Order` carries no reason field. The simulator is long-only, so a sell with no
//! simulated position is `Rejected` (`no_position`). No venue gateway exists (E3-S7).

use std::sync::{Arc, Mutex};

use honba_engine::ExecutionEngine;
use honba_entities::{Currency, ExecutionEvent, Money};
use honba_messages::{Bar, ErrorCode, ErrorDetail, Order, OrderStatus};
use honba_sim::NextOpenSim;

use crate::OrderLedger;

/// Starting cash of the default simulator, in minor units (10 billion INR in paise).
const DEFAULT_CASH_MINOR: i64 = 1_000_000_000_000;

/// Shared handle on the embedded simulator.
#[derive(Clone)]
pub(crate) struct ExecGateway {
    sim: Arc<Mutex<NextOpenSim>>,
}

impl ExecGateway {
    pub(crate) fn new(sim: NextOpenSim) -> Self {
        Self {
            sim: Arc::new(Mutex::new(sim)),
        }
    }

    pub(crate) fn default_sim() -> Self {
        let sim = NextOpenSim::new(Money::new(DEFAULT_CASH_MINOR, Currency::Inr))
            .expect("non-negative cash");
        Self::new(sim)
    }

    /// Hands `order` to the simulator and applies any immediate event (a rejection).
    pub(crate) fn submit(&self, ledger: &OrderLedger, order: Order) {
        let mut sim = self
            .sim
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Err(e) = sim.submit(order.clone()) {
            tracing::warn!(order = %order.order_id(), error = %e, "simulator refused submit");
            ledger.set_status(order.order_id().as_str(), OrderStatus::Rejected);
            return;
        }
        apply(&mut sim, ledger);
    }

    /// Feeds `bars` (ascending event time) to the simulator and applies its events.
    pub(crate) fn advance(&self, ledger: &OrderLedger, bars: &[Bar]) -> Result<(), ErrorDetail> {
        let mut sim = self
            .sim
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let mut result = Ok(());
        for bar in bars {
            if let Err(e) = sim.on_bar(bar) {
                result = Err(ErrorDetail::new(ErrorCode::InternalError, e.to_string()));
                break;
            }
        }
        apply(&mut sim, ledger);
        result
    }

    /// Withdraws a working order from the simulator (the ledger row is cancelled by the caller).
    pub(crate) fn cancel(&self, order_id: &str, ts: honba_messages::UnixNanos) {
        let mut sim = self
            .sim
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let _ = sim.cancel(order_id, ts);
        let _ = sim.drain_events();
    }
}

/// Drains the simulator's events into ledger status changes.
fn apply(sim: &mut NextOpenSim, ledger: &OrderLedger) {
    let events = sim.drain_events().unwrap_or_default();
    for event in events {
        match event {
            ExecutionEvent::Fill {
                trade, complete, ..
            } => {
                let status = if complete {
                    OrderStatus::Filled
                } else {
                    OrderStatus::PartiallyFilled
                };
                ledger.set_status(trade.order_id().as_str(), status);
            }
            ExecutionEvent::Rejected {
                order_id, reason, ..
            } => {
                tracing::info!(order = %order_id, %reason, "simulator rejected order");
                // The remainder of a partial fill is also reported as a rejection; the
                // filled part stays exposure, so the row keeps `PartiallyFilled`.
                if ledger
                    .get(order_id.as_str())
                    .is_some_and(|o| o.status() != OrderStatus::PartiallyFilled)
                {
                    ledger.set_status(order_id.as_str(), OrderStatus::Rejected);
                }
            }
            ExecutionEvent::Cancelled { order_id, .. } => {
                ledger.set_status(order_id.as_str(), OrderStatus::Cancelled);
            }
            _ => {}
        }
    }
}
