//! Rules 7-8: tick size and price band.

use std::sync::Arc;

use honba_entities::{Currency, Instrument, InstrumentKind};
use honba_market::{InstrumentRulesProvider, MarketProfile, NullMarketProfile, PriceBand};
use honba_messages::InstrumentId;

use super::{refusal, req, stage, x};
use crate::{
    PriceField, ProfileRulesSource, RiskCheck, RiskDecision, RiskLimits, RiskRefusal, RiskRequest,
    RiskStage,
};

fn priced(price: Option<f64>, trigger: Option<f64>) -> RiskRequest {
    RiskRequest {
        price,
        trigger_price: trigger,
        ..req()
    }
}

#[test]
fn tick_size_refused() {
    // Price: off tick.
    let r = refusal(stage().check(&priced(Some(100.03), None)));
    assert_eq!(
        r,
        RiskRefusal::TickSize {
            field: PriceField::Price,
            price: 100.03,
            tick: 0.05
        }
    );
    assert_eq!(r.context()["reason"], "off_tick");

    // Price: non-positive.
    for p in [0.0, -100.0] {
        let r = refusal(stage().check(&priced(Some(p), None)));
        assert_eq!(
            r,
            RiskRefusal::TickSize {
                field: PriceField::Price,
                price: p,
                tick: 0.05
            }
        );
        assert_eq!(r.context()["reason"], "non_positive");
    }

    // Trigger price: off tick and non-positive.
    let r = refusal(stage().check(&priced(Some(100.0), Some(99.97))));
    assert_eq!(
        r,
        RiskRefusal::TickSize {
            field: PriceField::TriggerPrice,
            price: 99.97,
            tick: 0.05
        }
    );
    assert_eq!(r.context()["reason"], "off_tick");
    let r = refusal(stage().check(&priced(None, Some(-1.0))));
    assert_eq!(r.context()["reason"], "non_positive");
    assert_eq!(r.context()["field"], "trigger_price");
}

#[test]
fn on_tick_prices_and_market_orders_are_approved() {
    assert_eq!(
        stage().check(&priced(Some(100.05), None)),
        RiskDecision::Approved
    );
    assert_eq!(
        stage().check(&priced(Some(100.0), Some(99.95))),
        RiskDecision::Approved
    );
    assert_eq!(stage().check(&priced(None, None)), RiskDecision::Approved);
}

#[test]
fn price_band_edges_are_inclusive_and_cover_both_prices() {
    assert_eq!(
        stage().check(&priced(Some(90.0), None)),
        RiskDecision::Approved
    );
    assert_eq!(
        stage().check(&priced(Some(110.0), None)),
        RiskDecision::Approved
    );
    assert_eq!(
        refusal(stage().check(&priced(Some(110.05), None))),
        RiskRefusal::PriceBand {
            field: PriceField::Price,
            price: 110.05,
            lower: 90.0,
            upper: 110.0
        }
    );
    assert_eq!(
        refusal(stage().check(&priced(Some(100.0), Some(89.95)))),
        RiskRefusal::PriceBand {
            field: PriceField::TriggerPrice,
            price: 89.95,
            lower: 90.0,
            upper: 110.0
        }
    );
}

#[test]
fn tick_rule_precedes_band_rule() {
    // 120.03 is both off tick and outside the band: tick wins.
    assert!(matches!(
        refusal(stage().check(&priced(Some(120.03), None))),
        RiskRefusal::TickSize { .. }
    ));
}

/// A profile whose rules provider supplies a price band (the India pack supplies none).
struct BandedRules;

impl InstrumentRulesProvider for BandedRules {
    fn price_band(&self, _id: &InstrumentId) -> Option<PriceBand> {
        Some(PriceBand::new(90.0, 110.0))
    }
}

struct BandedProfile {
    inner: NullMarketProfile,
    rules: BandedRules,
}

impl MarketProfile for BandedProfile {
    fn market_code(&self) -> &str {
        "banded"
    }
    fn currency(&self) -> Currency {
        self.inner.currency()
    }
    fn calendar(&self) -> &dyn honba_market::MarketCalendar {
        self.inner.calendar()
    }
    fn cost_schedule(&self) -> &dyn honba_market::CostSchedule {
        self.inner.cost_schedule()
    }
    fn instrument_rules(&self) -> &dyn InstrumentRulesProvider {
        &self.rules
    }
    fn symbol_grammar(&self) -> &dyn honba_market::SymbolGrammar {
        self.inner.symbol_grammar()
    }
    fn expiry_rules(&self) -> &dyn honba_market::ExpiryRules {
        self.inner.expiry_rules()
    }
    fn margin_model(&self) -> &dyn honba_market::MarginModel {
        self.inner.margin_model()
    }
    fn settlement_rules(&self) -> &dyn honba_market::SettlementRules {
        self.inner.settlement_rules()
    }
}

#[test]
fn price_band_from_profile() {
    let profile = Arc::new(BandedProfile {
        inner: NullMarketProfile::default(),
        rules: BandedRules,
    });
    let instrument = Instrument::new(x(), InstrumentKind::Equity, Currency::Inr, 1.0, 0.05);
    let source = ProfileRulesSource::new(profile, [instrument]);
    let mut stage = RiskStage::new(RiskLimits::default(), Currency::Inr, Arc::new(source)).unwrap();

    assert_eq!(
        stage.check(&priced(Some(100.0), None)),
        RiskDecision::Approved
    );
    assert_eq!(
        refusal(stage.check(&priced(Some(111.0), None))),
        RiskRefusal::PriceBand {
            field: PriceField::Price,
            price: 111.0,
            lower: 90.0,
            upper: 110.0
        }
    );
}

#[test]
fn profile_source_is_none_for_unregistered_instruments() {
    use crate::RulesSource;
    let source = ProfileRulesSource::new(Arc::new(NullMarketProfile::default()), []);
    assert!(source.rules(&x()).is_none());
}
