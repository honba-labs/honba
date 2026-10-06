//! A deterministic execution engine whose behavior per order is scripted.

use std::collections::HashMap;

use honba_engine::{AlgoError, ExecutionEngine, OrderRejection, Result};
use honba_entities::{Currency, Trade};
use honba_messages::{Order, OrderId, OrderSide};

/// What a [`ScriptedExecution`] does with an order it accepts.
#[derive(Clone, Debug, PartialEq)]
pub enum Behavior {
    /// Fill in full (the default for an unscripted order).
    Fill,
    /// Refuse the whole order for `reason`.
    Reject {
        /// Why it was refused.
        reason: String,
    },
    /// Fill `filled`, refuse the rest for `reason`.
    Partial {
        /// The quantity that fills.
        filled: f64,
        /// Why the remainder was refused.
        reason: String,
    },
    /// Keep the order working until it is cancelled.
    Hold,
}

impl Behavior {
    /// Refuses the whole order for `reason`.
    pub fn reject(reason: impl Into<String>) -> Self {
        Self::Reject {
            reason: reason.into(),
        }
    }

    /// Fills `filled` and refuses the remainder for `reason`.
    pub fn partial(filled: f64, reason: impl Into<String>) -> Self {
        Self::Partial {
            filled,
            reason: reason.into(),
        }
    }
}

/// An [`ExecutionEngine`] that fills at one fixed price and does with each
/// order what its script says (fill, reject, partly fill, or hold until
/// cancelled). Fills and rejections are stamped with the order's `ts_event`.
///
/// It is the reference engine for the rejection queue
/// (`ExecutionEngine::drain_rejections`) and for the shared conformance
/// vectors `schema/conformance/order_rejections.json`.
pub struct ScriptedExecution {
    price: f64,
    currency: Currency,
    script: HashMap<String, Behavior>,
    working: Vec<Order>,
    fills: Vec<Trade>,
    rejections: Vec<OrderRejection>,
}

impl ScriptedExecution {
    /// Creates an engine that fills at `price`; unscripted orders fill in full.
    pub fn new(price: f64) -> Self {
        Self {
            price,
            currency: Currency::Inr,
            script: HashMap::new(),
            working: Vec::new(),
            fills: Vec::new(),
            rejections: Vec::new(),
        }
    }

    /// Scripts what happens to the order with id `order_id`. A later entry
    /// for the same id replaces the earlier one; the script applies each time
    /// that id is submitted.
    ///
    /// # Panics
    ///
    /// If `behavior` is a [`Behavior::Partial`] whose `filled` is not positive
    /// and finite: the script is a test fixture, so a bad one fails loudly when
    /// built. A `filled` at or above the order's quantity is only known at
    /// submit, where it is refused with an error.
    #[must_use]
    pub fn with(mut self, order_id: impl Into<String>, behavior: Behavior) -> Self {
        if let Behavior::Partial { filled, .. } = &behavior {
            assert!(
                filled.is_finite() && *filled > 0.0,
                "partial fill must be positive and finite, got {filled}"
            );
        }
        self.script.insert(order_id.into(), behavior);
        self
    }

    /// Ids of the held orders, in submission order.
    pub fn working_orders(&self) -> Vec<String> {
        self.working
            .iter()
            .map(|o| o.order_id().as_str().to_string())
            .collect()
    }

    fn fill(&mut self, order: &Order, quantity: f64) {
        let side = if order.side() == OrderSide::Sell {
            OrderSide::Sell
        } else {
            OrderSide::Buy
        };
        self.fills.push(Trade::new(
            OrderId::new(order.order_id().as_str()),
            order.instrument_id().clone(),
            side,
            quantity,
            self.price,
            self.currency,
            order.ts_event(),
            order.ts_event(),
        ));
    }

    fn reject(&mut self, order: &Order, quantity: f64, reason: String) {
        self.rejections.push(OrderRejection::rejected(
            order.order_id().clone(),
            order.instrument_id().clone(),
            order.side(),
            quantity,
            reason,
            order.ts_event(),
        ));
    }
}

impl ExecutionEngine for ScriptedExecution {
    fn submit(&mut self, order: Order) -> Result<()> {
        let behavior = self
            .script
            .get(order.order_id().as_str())
            .cloned()
            .unwrap_or(Behavior::Fill);
        match behavior {
            Behavior::Fill => self.fill(&order, order.quantity()),
            Behavior::Reject { reason } => self.reject(&order, order.quantity(), reason),
            Behavior::Partial { filled, reason } => {
                if filled >= order.quantity() {
                    return Err(AlgoError::Component(format!(
                        "scripted partial fill {filled} must be below the order quantity {}",
                        order.quantity()
                    )));
                }
                self.fill(&order, filled);
                self.reject(&order, order.quantity() - filled, reason);
            }
            Behavior::Hold => self.working.push(order),
        }
        Ok(())
    }

    fn cancel(&mut self, order_id: &str) -> Result<()> {
        if let Some(i) = self
            .working
            .iter()
            .position(|o| o.order_id().as_str() == order_id)
        {
            let o = self.working.remove(i);
            self.rejections.push(OrderRejection::cancelled(
                o.order_id().clone(),
                o.instrument_id().clone(),
                o.side(),
                o.quantity(),
                o.ts_event(),
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
