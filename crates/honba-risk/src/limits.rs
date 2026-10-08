//! Configurable risk limits (ADR 0018 decision 8).

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::RiskConfigError;

/// Per-run risk limits. `None` means the rule is off; the default has no limits.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RiskLimits {
    /// Per-order notional ceiling in major units of the account currency.
    /// `None` = no notional rule; `Some` must be finite and `> 0`.
    #[serde(default)]
    pub max_notional: Option<f64>,
    /// Orders-per-window ceiling in event time. `None` = no rate rule.
    #[serde(default)]
    pub order_rate: Option<OrderRateLimit>,
}

/// At most `max_orders` approved orders in any `window_ms` of event time.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct OrderRateLimit {
    /// Maximum approved orders per window; must be `>= 1`.
    pub max_orders: u32,
    /// Window length in milliseconds of event time; must be `>= 1`.
    pub window_ms: u64,
}

impl RiskLimits {
    /// Checks every set limit: `max_notional` finite and `> 0`, `max_orders` and `window_ms`
    /// `>= 1`. The default (no limits) is valid.
    pub fn validate(&self) -> Result<(), RiskConfigError> {
        if let Some(v) = self.max_notional {
            if !(v.is_finite() && v > 0.0) {
                return Err(RiskConfigError::InvalidMaxNotional(v));
            }
        }
        if let Some(rate) = self.order_rate {
            if rate.max_orders == 0 || rate.window_ms == 0 {
                return Err(RiskConfigError::InvalidOrderRate);
            }
        }
        Ok(())
    }

    /// The guard for a non-simulated (live) run: the limits must be valid and **both**
    /// `max_notional` and `order_rate` must be set, else
    /// [`RiskConfigError::LiveRunWithoutLimit`].
    pub fn require_live(&self) -> Result<(), RiskConfigError> {
        self.validate()?;
        if self.max_notional.is_none() || self.order_rate.is_none() {
            return Err(RiskConfigError::LiveRunWithoutLimit);
        }
        Ok(())
    }
}
