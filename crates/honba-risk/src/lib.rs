#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! The pre-trade risk stage (ADR 0018).
//!
//! Pure and deterministic: no I/O, no clock, no broker vocabulary. A submitter builds a
//! [`RiskRequest`] (every value a rule needs), asks a [`RiskCheck`] for a [`RiskDecision`], and
//! on refusal records the typed [`RiskRefusal`] and its [`ErrorCode`](honba_messages::ErrorCode)
//! wire spelling. [`RiskStage`] is the sole implementation; it evaluates the rules in a fixed
//! order and the first refusal wins:
//!
//! 1. trading halted, 2. reduce-only (both in [`check_state`], which needs no rules source),
//! 3. instrument unknown, 4-6. quantity below minimum / over freeze / not a lot multiple,
//! 7. tick size, 8. price band, 9. maximum notional, 10. order rate (half-open event-time
//!    window; only approved orders count, so `check` is not idempotent with a rate limit).

pub mod durable;
pub mod ledger;
mod limits;
mod refusal;
mod request;
mod rules_source;
mod stage;

pub use durable::{DurableRiskState, InFlightOrder};
pub use ledger::{FillFingerprint, FillLedger, FillRecord};
pub use limits::{OrderRateLimit, RiskLimits};
pub use refusal::{PriceField, RiskRefusal};
pub use request::{RiskCheck, RiskDecision, RiskRequest};
pub use rules_source::{ProfileRulesSource, RulesSource};
pub use stage::{check_state, RiskConfigError, RiskStage};

#[cfg(test)]
mod tests;
