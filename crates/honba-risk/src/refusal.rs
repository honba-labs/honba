//! Typed refusals and their wire mapping (ADR 0018 decisions 3 and 5).

use honba_entities::Money;
use honba_messages::{ErrorCode, InstrumentId, OrderSide};
use serde_json::{json, Value};

/// Which price of an order a price rule refers to.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PriceField {
    /// The limit price.
    Price,
    /// The stop trigger price.
    TriggerPrice,
}

impl PriceField {
    /// Stable wire spelling.
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Price => "price",
            Self::TriggerPrice => "trigger_price",
        }
    }
}

/// Why the stage refused an order, with the numbers behind the decision.
#[derive(Clone, Debug, PartialEq)]
pub enum RiskRefusal {
    /// Trading is halted.
    TradingHalted,
    /// Reduce-only: the order would not stay within `[min(p, 0), max(p, 0)]`.
    ReduceOnly {
        /// Position plus same-side working remainder.
        position: f64,
        /// The order's side.
        side: OrderSide,
        /// The order's quantity.
        quantity: f64,
    },
    /// No rules exist for the instrument.
    InstrumentUnknown {
        /// The unknown instrument.
        instrument_id: InstrumentId,
    },
    /// Quantity below the minimum order quantity.
    QuantityBelowMin {
        /// The order quantity.
        quantity: f64,
        /// The minimum.
        min: f64,
    },
    /// Quantity above the freeze quantity.
    QuantityOverFreeze {
        /// The order quantity.
        quantity: f64,
        /// The maximum.
        max: f64,
    },
    /// Quantity is not a lot multiple.
    LotMultiple {
        /// The order quantity.
        quantity: f64,
        /// The lot size.
        lot: f64,
    },
    /// A price is non-positive or off the tick grid.
    TickSize {
        /// Which price.
        field: PriceField,
        /// The offending price.
        price: f64,
        /// The tick size.
        tick: f64,
    },
    /// A price is outside the instrument's price band.
    PriceBand {
        /// Which price.
        field: PriceField,
        /// The offending price.
        price: f64,
        /// Band floor.
        lower: f64,
        /// Band ceiling.
        upper: f64,
    },
    /// Order notional exceeds the configured maximum.
    MaxNotional {
        /// The order's notional.
        notional: Money,
        /// The configured maximum.
        limit: Money,
    },
    /// A notional limit is set but the order has no price to value it with.
    MaxNotionalUnpriceable {
        /// The configured maximum.
        limit: Money,
    },
    /// Too many approved orders in the event-time window.
    OrderRate {
        /// Approved orders already in the window.
        count: u32,
        /// The configured maximum.
        max_orders: u32,
        /// The window length in milliseconds.
        window_ms: u64,
    },
    /// Order quantity exceeds the configured participation fraction of ADV.
    MaxParticipation {
        /// The order quantity.
        quantity: f64,
        /// The maximum allowed quantity (max_participation * ADV).
        max_quantity: f64,
        /// The ADV used for the calculation.
        adv: f64,
        /// The participation fraction limit.
        participation: f64,
        /// Reason when ADV is missing or non-positive.
        reason: Option<String>,
    },
}

impl RiskRefusal {
    /// Stable rule name.
    pub fn rule(&self) -> &'static str {
        match self {
            Self::TradingHalted => "trading_halted",
            Self::ReduceOnly { .. } => "reduce_only",
            Self::InstrumentUnknown { .. } => "instrument_unknown",
            Self::QuantityBelowMin { .. } => "quantity_below_min",
            Self::QuantityOverFreeze { .. } => "quantity_over_freeze",
            Self::LotMultiple { .. } => "lot_multiple",
            Self::TickSize { .. } => "tick_size",
            Self::PriceBand { .. } => "price_band",
            Self::MaxNotional { .. } | Self::MaxNotionalUnpriceable { .. } => "max_notional",
            Self::OrderRate { .. } => "order_rate",
            Self::MaxParticipation { .. } => "max_participation",
        }
    }

    /// The wire error code; its `as_str()` is the `Rejected.reason`.
    pub fn error_code(&self) -> ErrorCode {
        match self {
            Self::TradingHalted => ErrorCode::RiskTradingHalted,
            Self::ReduceOnly { .. } => ErrorCode::RiskReduceOnlyViolation,
            Self::InstrumentUnknown { .. } => ErrorCode::RiskInstrumentUnknown,
            Self::QuantityBelowMin { .. } => ErrorCode::RiskQuantityBelowMin,
            Self::QuantityOverFreeze { .. } => ErrorCode::RiskQuantityOverFreeze,
            Self::LotMultiple { .. } => ErrorCode::RiskLotMultipleViolation,
            Self::TickSize { .. } => ErrorCode::RiskTickSizeViolation,
            Self::PriceBand { .. } => ErrorCode::RiskPriceBandExceeded,
            Self::MaxNotional { .. } | Self::MaxNotionalUnpriceable { .. } => {
                ErrorCode::RiskMaxNotionalExceeded
            }
            Self::OrderRate { .. } => ErrorCode::RiskOrderRateExceeded,
            Self::MaxParticipation { .. } => ErrorCode::RiskMaxParticipationExceeded,
        }
    }

    /// The numbers behind the refusal as JSON, plus `"rule"` and, where relevant, `"reason"`.
    ///
    /// Money values are major units (`f64`) with a sibling `"currency"` code.
    pub fn context(&self) -> Value {
        let rule = self.rule();
        match self {
            Self::TradingHalted => json!({ "rule": rule }),
            Self::ReduceOnly {
                position,
                side,
                quantity,
            } => json!({
                "rule": rule,
                "position": position,
                "side": match side {
                    OrderSide::Buy => "buy",
                    OrderSide::Sell => "sell",
                    _ => "no_order_side",
                },
                "quantity": quantity,
            }),
            Self::InstrumentUnknown { instrument_id } => {
                json!({ "rule": rule, "instrument_id": instrument_id.to_string() })
            }
            Self::QuantityBelowMin { quantity, min } => {
                json!({ "rule": rule, "quantity": quantity, "min": min })
            }
            Self::QuantityOverFreeze { quantity, max } => {
                json!({ "rule": rule, "quantity": quantity, "max": max })
            }
            Self::LotMultiple { quantity, lot } => {
                json!({ "rule": rule, "quantity": quantity, "lot": lot })
            }
            Self::TickSize { field, price, tick } => json!({
                "rule": rule,
                "field": field.as_str(),
                "price": price,
                "tick": tick,
                "reason": if *price > 0.0 { "off_tick" } else { "non_positive" },
            }),
            Self::PriceBand {
                field,
                price,
                lower,
                upper,
            } => json!({
                "rule": rule,
                "field": field.as_str(),
                "price": price,
                "lower": lower,
                "upper": upper,
            }),
            Self::MaxNotional { notional, limit } => json!({
                "rule": rule,
                "notional": notional.to_major_f64(),
                "limit": limit.to_major_f64(),
                "currency": limit.currency().code(),
            }),
            Self::MaxNotionalUnpriceable { limit } => json!({
                "rule": rule,
                "reason": "unpriceable",
                "limit": limit.to_major_f64(),
                "currency": limit.currency().code(),
            }),
            Self::OrderRate {
                count,
                max_orders,
                window_ms,
            } => json!({
                "rule": rule,
                "count": count,
                "max_orders": max_orders,
                "window_ms": window_ms,
            }),
            Self::MaxParticipation {
                quantity,
                max_quantity,
                adv,
                participation,
                reason,
            } => {
                let mut obj = json!({
                    "rule": rule,
                    "quantity": quantity,
                    "max_quantity": max_quantity,
                    "adv": adv,
                    "participation": participation,
                });
                if let Some(r) = reason {
                    obj.as_object_mut().unwrap().insert("reason".to_string(), json!(r));
                }
                obj
            },
        }
    }
}
