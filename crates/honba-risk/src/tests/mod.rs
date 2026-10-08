//! Unit tests for this crate, one file per area.

mod instrument;
mod limits;
mod notional;
mod price;
mod properties;
mod quantity;
mod rate;
mod refusal;
mod state;

use std::collections::BTreeMap;
use std::sync::Arc;

use honba_entities::Currency;
use honba_market::{InstrumentRules, PriceBand};
use honba_messages::{Exchange, InstrumentId, OrderId, OrderSide, TradingState, UnixNanos};

use crate::{RiskDecision, RiskLimits, RiskRefusal, RiskRequest, RiskStage, RulesSource};

/// The instrument every test order targets.
pub(crate) fn x() -> InstrumentId {
    InstrumentId::new("X", Exchange::new("NSE"))
}

/// Map-backed rules source for tests.
pub(crate) struct StaticRules(BTreeMap<InstrumentId, (InstrumentRules, Option<PriceBand>)>);

impl RulesSource for StaticRules {
    fn rules(&self, id: &InstrumentId) -> Option<(InstrumentRules, Option<PriceBand>)> {
        self.0.get(id).cloned()
    }
}

/// Lot 25, tick 0.05, min 25, freeze 100, band 90..110 (the ADR's golden instrument).
pub(crate) fn rules() -> Arc<dyn RulesSource> {
    let mut m = BTreeMap::new();
    m.insert(
        x(),
        (
            InstrumentRules::new(25.0, 0.05).with_max_quantity(100.0),
            Some(PriceBand::new(90.0, 110.0)),
        ),
    );
    Arc::new(StaticRules(m))
}

pub(crate) fn stage_with(limits: RiskLimits) -> RiskStage {
    RiskStage::new(limits, Currency::Inr, rules()).expect("valid limits")
}

pub(crate) fn stage() -> RiskStage {
    stage_with(RiskLimits::default())
}

/// A valid active buy: 25 @ 100.0, flat.
pub(crate) fn req() -> RiskRequest {
    RiskRequest {
        order_id: OrderId::new("O1"),
        instrument_id: x(),
        side: OrderSide::Buy,
        quantity: 25.0,
        price: Some(100.0),
        trigger_price: None,
        reference_price: None,
        adv: None,
        position: 0.0,
        trading_state: TradingState::Active,
        ts: UnixNanos::new(1),
    }
}

pub(crate) fn refusal(d: RiskDecision) -> RiskRefusal {
    match d {
        RiskDecision::Refused(r) => r,
        RiskDecision::Approved => panic!("expected a refusal, got Approved"),
    }
}
