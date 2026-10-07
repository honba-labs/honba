//! The order-state machine (ADR 0019): events as verbs over [`OrderStatus`].
//!
//! Pure and quantity-only: it needs no trade, money or instrument type. A
//! producer feeds [`OrderEvent`]s to [`OrderState::apply`]; an event the
//! transition table forbids is an [`IllegalTransition`] and leaves the state
//! untouched.

use std::fmt;

use serde::{Deserialize, Serialize};

use super::order::OrderStatus;

/// Tolerance for comparing cumulative quantities (ADR 0016 convention).
const QTY_EPS: f64 = 1e-9;

/// A lifecycle event applied to an [`OrderState`].
#[derive(Clone, Copy, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
#[non_exhaustive]
pub enum OrderEvent {
    /// The order was sent; carries the order quantity (the only event that sets it).
    Submitted {
        /// The order quantity.
        quantity: f64,
    },
    /// The venue acknowledged the order.
    Accepted,
    /// The order was rejected (pre-gate or venue).
    Rejected,
    /// A fill of `last_qty`.
    Fill {
        /// The quantity of this fill.
        last_qty: f64,
        /// The producer's claim that this fill completes the order.
        complete: bool,
    },
    /// A cancel was asked for and is awaiting an answer.
    CancelRequested,
    /// The order was cancelled (client, venue, or IOC/FOK remainder).
    Cancelled,
    /// The order expired by time-in-force.
    Expired,
}

crate::enum_with_all! {
    /// The kind of an [`OrderEvent`], without payload.
    #[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
    #[serde(rename_all = "snake_case")]
    #[non_exhaustive]
    pub enum OrderEventKind {
        /// See [`OrderEvent::Submitted`].
        Submitted,
        /// See [`OrderEvent::Accepted`].
        Accepted,
        /// See [`OrderEvent::Rejected`].
        Rejected,
        /// See [`OrderEvent::Fill`].
        Fill,
        /// See [`OrderEvent::CancelRequested`].
        CancelRequested,
        /// See [`OrderEvent::Cancelled`].
        Cancelled,
        /// See [`OrderEvent::Expired`].
        Expired,
    }
}

impl OrderEvent {
    /// Returns the payload-free kind of this event.
    pub fn kind(&self) -> OrderEventKind {
        match self {
            OrderEvent::Submitted { .. } => OrderEventKind::Submitted,
            OrderEvent::Accepted => OrderEventKind::Accepted,
            OrderEvent::Rejected => OrderEventKind::Rejected,
            OrderEvent::Fill { .. } => OrderEventKind::Fill,
            OrderEvent::CancelRequested => OrderEventKind::CancelRequested,
            OrderEvent::Cancelled => OrderEventKind::Cancelled,
            OrderEvent::Expired => OrderEventKind::Expired,
        }
    }
}

impl fmt::Display for OrderEventKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            OrderEventKind::Submitted => "submitted",
            OrderEventKind::Accepted => "accepted",
            OrderEventKind::Rejected => "rejected",
            OrderEventKind::Fill => "fill",
            OrderEventKind::CancelRequested => "cancel_requested",
            OrderEventKind::Cancelled => "cancelled",
            OrderEventKind::Expired => "expired",
        })
    }
}

/// Why an [`OrderEvent`] could not be applied. The state is unchanged.
#[derive(Clone, Copy, Debug, PartialEq)]
#[non_exhaustive]
pub enum IllegalTransition {
    /// The transition table marks this `(state, event)` cell illegal.
    Transition {
        /// The order status when the event arrived.
        status: OrderStatus,
        /// Whether a cancel was pending.
        cancel_requested: bool,
        /// The offending event.
        event: OrderEventKind,
    },
    /// The fill would exceed the order quantity.
    Overfill {
        /// The order quantity.
        quantity: f64,
        /// The quantity filled so far.
        filled_qty: f64,
        /// The offending fill quantity.
        last_qty: f64,
    },
    /// The producer's `complete` flag disagrees with the derived completeness.
    FillMismatch {
        /// What the producer claimed.
        claimed_complete: bool,
        /// What `filled_qty + 1e-9 >= quantity` derived.
        derived_complete: bool,
    },
    /// A quantity (order or fill) is non-finite or not positive.
    InvalidQuantity {
        /// The offending value.
        value: f64,
    },
}

impl fmt::Display for IllegalTransition {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            IllegalTransition::Transition {
                status,
                cancel_requested,
                event,
            } => write!(
                f,
                "illegal transition: {event} in status {status:?} (cancel_requested={cancel_requested})"
            ),
            IllegalTransition::Overfill {
                quantity,
                filled_qty,
                last_qty,
            } => write!(
                f,
                "overfill: filled {filled_qty} + last {last_qty} exceeds quantity {quantity}"
            ),
            IllegalTransition::FillMismatch {
                claimed_complete,
                derived_complete,
            } => write!(
                f,
                "fill mismatch: claimed complete={claimed_complete}, derived complete={derived_complete}"
            ),
            IllegalTransition::InvalidQuantity { value } => {
                write!(f, "invalid quantity: {value}")
            }
        }
    }
}

impl std::error::Error for IllegalTransition {}

/// The state of one order: status, quantities and a pending-cancel flag.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct OrderState {
    /// The lifecycle status.
    pub status: OrderStatus,
    /// The order quantity; `0.0` until `Submitted`.
    pub quantity: f64,
    /// The cumulative filled quantity.
    pub filled_qty: f64,
    /// A cancel was asked for and not yet answered; cleared on terminal states.
    pub cancel_requested: bool,
}

impl Default for OrderState {
    fn default() -> Self {
        Self::new()
    }
}

fn is_terminal(status: OrderStatus) -> bool {
    use OrderStatus::*;
    matches!(status, Filled | Cancelled | Rejected | Expired)
}

fn terminal_kind(status: OrderStatus) -> Option<OrderEventKind> {
    match status {
        OrderStatus::Cancelled => Some(OrderEventKind::Cancelled),
        OrderStatus::Rejected => Some(OrderEventKind::Rejected),
        OrderStatus::Expired => Some(OrderEventKind::Expired),
        _ => None,
    }
}

impl OrderState {
    /// A new, un-submitted order state.
    pub fn new() -> Self {
        Self {
            status: OrderStatus::Initialized,
            quantity: 0.0,
            filled_qty: 0.0,
            cancel_requested: false,
        }
    }

    /// Whether `via` is legal (transition or duplicate no-op) from
    /// `(status, cancel_requested)`. `Fill` from `Filled` is illegal.
    pub fn can_transition(from: (OrderStatus, bool), via: OrderEventKind) -> bool {
        use OrderEventKind as K;
        use OrderStatus::*;
        let (status, _) = from;
        match status {
            Initialized => matches!(via, K::Submitted | K::Rejected),
            Submitted | Accepted | PartiallyFilled => !matches!(via, K::Submitted),
            Filled => false,
            Cancelled | Rejected | Expired => terminal_kind(status) == Some(via),
        }
    }

    /// Applies `ev`. `Ok(true)` = transitioned, `Ok(false)` = duplicate no-op,
    /// `Err` = illegal (state unchanged).
    pub fn apply(&mut self, ev: &OrderEvent) -> Result<bool, IllegalTransition> {
        use OrderStatus::*;
        let kind = ev.kind();
        let illegal = IllegalTransition::Transition {
            status: self.status,
            cancel_requested: self.cancel_requested,
            event: kind,
        };
        // A repeated fill after completion is indistinguishable from an overfill.
        if let (Filled, OrderEvent::Fill { last_qty, .. }) = (self.status, ev) {
            check_qty(*last_qty)?;
            return Err(IllegalTransition::Overfill {
                quantity: self.quantity,
                filled_qty: self.filled_qty,
                last_qty: *last_qty,
            });
        }
        if !Self::can_transition((self.status, self.cancel_requested), kind) {
            return Err(illegal);
        }
        match *ev {
            OrderEvent::Submitted { quantity } => {
                check_qty(quantity)?;
                self.quantity = quantity;
                self.status = Submitted;
            }
            OrderEvent::Accepted => {
                if self.status != Submitted {
                    return Ok(false);
                }
                self.status = Accepted;
            }
            OrderEvent::Rejected => {
                if is_terminal(self.status) {
                    return Ok(false);
                }
                self.terminate(Rejected);
            }
            OrderEvent::Cancelled => {
                if is_terminal(self.status) {
                    return Ok(false);
                }
                self.terminate(Cancelled);
            }
            OrderEvent::Expired => {
                if is_terminal(self.status) {
                    return Ok(false);
                }
                self.terminate(Expired);
            }
            OrderEvent::CancelRequested => {
                if self.cancel_requested {
                    return Ok(false);
                }
                self.cancel_requested = true;
            }
            OrderEvent::Fill {
                last_qty, complete, ..
            } => {
                check_qty(last_qty)?;
                let cum = self.filled_qty + last_qty;
                if cum > self.quantity + QTY_EPS {
                    return Err(IllegalTransition::Overfill {
                        quantity: self.quantity,
                        filled_qty: self.filled_qty,
                        last_qty,
                    });
                }
                let derived_complete = cum + QTY_EPS >= self.quantity;
                if complete != derived_complete {
                    return Err(IllegalTransition::FillMismatch {
                        claimed_complete: complete,
                        derived_complete,
                    });
                }
                self.filled_qty = cum;
                if derived_complete {
                    self.terminate(Filled);
                } else {
                    self.status = PartiallyFilled;
                }
            }
        }
        Ok(true)
    }

    fn terminate(&mut self, status: OrderStatus) {
        self.status = status;
        self.cancel_requested = false;
    }
}

fn check_qty(value: f64) -> Result<(), IllegalTransition> {
    if value.is_finite() && value > 0.0 {
        Ok(())
    } else {
        Err(IllegalTransition::InvalidQuantity { value })
    }
}
