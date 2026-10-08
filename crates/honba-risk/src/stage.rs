//! The risk stage: fixed-order rules (ADR 0018 decision 4).

use std::sync::Arc;

use honba_entities::{Currency, Money};
use honba_market::{InstrumentRules, PriceBand, QuantityViolation};
use honba_messages::{OrderSide, TradingState};

use crate::{
    PriceField, RiskCheck, RiskDecision, RiskLimits, RiskRefusal, RiskRequest, RulesSource,
};

/// Why a [`RiskStage`] could not be built.
#[derive(Clone, Debug, PartialEq, thiserror::Error)]
pub enum RiskConfigError {
    /// `max_notional` must be finite and greater than zero.
    #[error("max_notional must be finite and > 0, got {0}")]
    InvalidMaxNotional(f64),
    /// `order_rate` needs `max_orders >= 1` and `window_ms >= 1`.
    #[error("order_rate needs max_orders >= 1 and window_ms >= 1")]
    InvalidOrderRate,
}

/// Tolerance for fractional positions in the reduce-only comparison.
const POSITION_EPS: f64 = 1e-9;

/// Rules 1-2 (trading halted, reduce-only): need no rules source.
///
/// With `p = req.position` and `s = +1` for a buy / `-1` for a sell, a reducing-state order
/// passes only if `p + s*q` stays within `[min(p, 0), max(p, 0)]`; a flat `p` refuses all.
pub fn check_state(req: &RiskRequest) -> Option<RiskRefusal> {
    match req.trading_state {
        TradingState::Active => None,
        TradingState::Halted => Some(RiskRefusal::TradingHalted),
        TradingState::Reducing => {
            let p = req.position;
            let sign = match req.side {
                OrderSide::Buy => 1.0,
                OrderSide::Sell => -1.0,
                _ => 0.0,
            };
            let after = p + sign * req.quantity;
            let ok = sign != 0.0
                && p.abs() > POSITION_EPS
                && after >= p.min(0.0) - POSITION_EPS
                && after <= p.max(0.0) + POSITION_EPS;
            (!ok).then_some(RiskRefusal::ReduceOnly {
                position: p,
                side: req.side,
                quantity: req.quantity,
            })
        }
    }
}

fn price_rules(
    req: &RiskRequest,
    rules: &InstrumentRules,
    band: Option<PriceBand>,
) -> Option<RiskRefusal> {
    let prices = [
        (PriceField::Price, req.price),
        (PriceField::TriggerPrice, req.trigger_price),
    ];
    for (field, price) in prices {
        if let Some(price) = price {
            if rules.validate_price(price).is_err() {
                return Some(RiskRefusal::TickSize {
                    field,
                    price,
                    tick: rules.tick_size,
                });
            }
        }
    }
    let band = band?;
    for (field, price) in prices {
        if let Some(price) = price {
            if !band.contains(price) {
                return Some(RiskRefusal::PriceBand {
                    field,
                    price,
                    lower: band.lower,
                    upper: band.upper,
                });
            }
        }
    }
    None
}

/// The stage sole [`RiskCheck`]: not `Clone`, owned by one submitter.
pub struct RiskStage {
    limits: RiskLimits,
    currency: Currency,
    max_notional: Option<Money>,
    rules: Arc<dyn RulesSource>,
}

impl RiskStage {
    /// Builds a stage; `currency` is the account currency `max_notional` is quoted in.
    pub fn new(
        limits: RiskLimits,
        currency: Currency,
        rules: Arc<dyn RulesSource>,
    ) -> Result<Self, RiskConfigError> {
        let max_notional = match limits.max_notional {
            None => None,
            Some(v) if v.is_finite() && v > 0.0 => Some(
                Money::from_major_f64(v, currency)
                    .map_err(|_| RiskConfigError::InvalidMaxNotional(v))?,
            ),
            Some(v) => return Err(RiskConfigError::InvalidMaxNotional(v)),
        };
        if let Some(rate) = limits.order_rate {
            if rate.max_orders == 0 || rate.window_ms == 0 {
                return Err(RiskConfigError::InvalidOrderRate);
            }
        }
        Ok(Self {
            limits,
            currency,
            max_notional,
            rules,
        })
    }

    /// The limits this stage was built with.
    pub fn limits(&self) -> &RiskLimits {
        &self.limits
    }
}

impl RiskStage {
    /// Rule 9: the notional is priced from `price`, else `trigger_price`, else `reference_price`.
    fn max_notional_rule(&self, req: &RiskRequest) -> Option<RiskRefusal> {
        let limit = self.max_notional?;
        let unpriceable = RiskRefusal::MaxNotionalUnpriceable { limit };
        let Some(px) = req.price.or(req.trigger_price).or(req.reference_price) else {
            return Some(unpriceable);
        };
        match Money::mul_qty(req.quantity, px, self.currency) {
            Ok(notional) if notional.minor() > limit.minor() => {
                Some(RiskRefusal::MaxNotional { notional, limit })
            }
            Ok(_) => None,
            Err(_) => Some(unpriceable),
        }
    }
}

impl RiskCheck for RiskStage {
    fn check(&mut self, req: &RiskRequest) -> RiskDecision {
        if let Some(refusal) = check_state(req) {
            return RiskDecision::Refused(refusal);
        }
        let Some((rules, band)) = self.rules.rules(&req.instrument_id) else {
            return RiskDecision::Refused(RiskRefusal::InstrumentUnknown {
                instrument_id: req.instrument_id.clone(),
            });
        };
        if let Err(v) = rules.validate_quantity(req.quantity) {
            return RiskDecision::Refused(match v {
                QuantityViolation::BelowMin { quantity, min } => {
                    RiskRefusal::QuantityBelowMin { quantity, min }
                }
                QuantityViolation::OverFreeze { quantity, max } => {
                    RiskRefusal::QuantityOverFreeze { quantity, max }
                }
                QuantityViolation::NotLotMultiple { quantity, lot } => {
                    RiskRefusal::LotMultiple { quantity, lot }
                }
            });
        }
        if let Some(refusal) = price_rules(req, &rules, band) {
            return RiskDecision::Refused(refusal);
        }
        if let Some(refusal) = self.max_notional_rule(req) {
            return RiskDecision::Refused(refusal);
        }
        // Rule 10 (order rate) is enforced in the next chunk (E2-S2 r2b).
        RiskDecision::Approved
    }
}
