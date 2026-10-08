//! The rules port and its adapter over honba-market's profile (ADR 0018 decision 1).

use std::collections::BTreeMap;
use std::sync::Arc;

use honba_entities::Instrument;
use honba_market::{InstrumentRules, MarketProfile, PriceBand};
use honba_messages::InstrumentId;

/// Where the stage finds an instrument's trading rules.
pub trait RulesSource: Send + Sync {
    /// `None` = the instrument is unknown to this run; the stage refuses (never approves) it.
    fn rules(&self, id: &InstrumentId) -> Option<(InstrumentRules, Option<PriceBand>)>;
}

/// Adapter over a [`MarketProfile`]'s rules provider and a set of known instruments.
///
/// It lives here, not in honba-market, because honba-market cannot name honba-risk.
pub struct ProfileRulesSource {
    profile: Arc<dyn MarketProfile>,
    instruments: BTreeMap<InstrumentId, Instrument>,
}

impl ProfileRulesSource {
    /// Builds a source over `profile` for the given instruments.
    pub fn new(
        profile: Arc<dyn MarketProfile>,
        instruments: impl IntoIterator<Item = Instrument>,
    ) -> Self {
        Self {
            profile,
            instruments: instruments
                .into_iter()
                .map(|i| (i.id().clone(), i))
                .collect(),
        }
    }
}

impl RulesSource for ProfileRulesSource {
    fn rules(&self, id: &InstrumentId) -> Option<(InstrumentRules, Option<PriceBand>)> {
        let provider = self.profile.instrument_rules();
        self.instruments
            .get(id)
            .map(|i| (provider.rules_for(i), provider.price_band(id)))
    }
}
