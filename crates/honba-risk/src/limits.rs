//! Configurable risk limits (ADR 0018 decision 8).

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

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
