//! The risk stage: fixed-order rules (ADR 0018 decision 4).

use std::collections::VecDeque;
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
    /// `max_participation` must be in (0, 1].
    #[error("max_participation must be in (0, 1], got {0}")]
    InvalidMaxParticipation(f64),
    /// A live run needs both `max_notional` and `order_rate` set.
    #[error("a live run requires both max_notional and order_rate to be set")]
    LiveRunWithoutLimit,
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
    /// Event times (ns) of approved orders still inside the rate window; non-decreasing.
    approved_ts: VecDeque<u64>,
}

impl RiskStage {
    /// Builds a stage; `currency` is the account currency `max_notional` is quoted in.
    pub fn new(
        limits: RiskLimits,
        currency: Currency,
        rules: Arc<dyn RulesSource>,
    ) -> Result<Self, RiskConfigError> {
        limits.validate()?;
        let max_notional = match limits.max_notional {
            None => None,
            Some(v) if v.is_finite() && v > 0.0 => Some(
                Money::from_major_f64(v, currency)
                    .map_err(|_| RiskConfigError::InvalidMaxNotional(v))?,
            ),
            Some(v) => return Err(RiskConfigError::InvalidMaxNotional(v)),
        };
        Ok(Self {
            limits,
            currency,
            max_notional,
            rules,
            approved_ts: VecDeque::new(),
        })
    }

    /// The limits this stage was built with.
    pub fn limits(&self) -> &RiskLimits {
        &self.limits
    }
}

impl RiskStage {
    /// Participation rule: order quantity must not exceed `max_participation * ADV`.
    /// Runs after price rules (so ADV is available in the request) and before notional,
    /// so a market-absorption refusal wins over an account-value refusal.
    fn max_participation_rule(&self, req: &RiskRequest) -> Option<RiskRefusal> {
        let participation = self.limits.max_participation?;
        let Some(adv) = req.adv else {
            return Some(RiskRefusal::MaxParticipation {
                quantity: req.quantity,
                max_quantity: 0.0,
                adv: 0.0,
                participation,
                reason: Some("missing_adv".to_string()),
            });
        };
        if adv <= 0.0 {
            return Some(RiskRefusal::MaxParticipation {
                quantity: req.quantity,
                max_quantity: 0.0,
                adv,
                participation,
                reason: Some("non_positive_adv".to_string()),
            });
        }
        let max_quantity = participation * adv;
        if req.quantity > max_quantity {
            return Some(RiskRefusal::MaxParticipation {
                quantity: req.quantity,
                max_quantity,
                adv,
                participation,
                reason: None,
            });
        }
        None
    }

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

impl RiskStage {
    /// Rule 10: at most `max_orders` approved orders in the half-open window `(ts - W, ts]`.
    ///
    /// Only called once every other rule approved. Approval records the order's event time, so
    /// `check` is deliberately not idempotent with a rate limit. A `ts` earlier than the last
    /// recorded one is clamped to it, so the window is deterministic and never shrinks.
    fn order_rate_rule(&mut self, req: &RiskRequest) -> Option<RiskRefusal> {
        let rate = self.limits.order_rate?;
        let window_ns = rate.window_ms.saturating_mul(1_000_000);
        let last = self.approved_ts.back().copied().unwrap_or(0);
        let now = req.ts.as_u64().max(last);
        // Drop orders at or before `now - window_ns`: exactly one window old is out.
        while self
            .approved_ts
            .front()
            .is_some_and(|t| now.saturating_sub(*t) >= window_ns)
        {
            self.approved_ts.pop_front();
        }
        let count = u32::try_from(self.approved_ts.len()).unwrap_or(u32::MAX);
        if count >= rate.max_orders {
            return Some(RiskRefusal::OrderRate {
                count,
                max_orders: rate.max_orders,
                window_ms: rate.window_ms,
            });
        }
        self.approved_ts.push_back(now);
        None
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
        if let Some(refusal) = self.max_participation_rule(req) {
            return RiskDecision::Refused(refusal);
        }
        if let Some(refusal) = self.max_notional_rule(req) {
            return RiskDecision::Refused(refusal);
        }
        if let Some(refusal) = self.order_rate_rule(req) {
            return RiskDecision::Refused(refusal);
        }
        RiskDecision::Approved
    }
}
